"""CLIP model wrapper around :mod:`open_clip`.

The model is loaded lazily. A single :class:`threading.Lock` serializes access
to the model during embedding. ``is_loaded`` is a plain bool read (relying on
the GIL for atomicity).
"""

from __future__ import annotations

import io
import threading

import open_clip
from PIL import Image


def resolve_pretrained(model_name: str) -> str | None:
    """Return the first available pretrained tag for ``model_name``."""
    tags = open_clip.list_pretrained_tags_by_model(model_name)
    if tags:
        return tags[0]
    pretrained = open_clip.list_pretrained()
    for name, tag in pretrained:
        if name == model_name:
            return tag
    return None


class ClipModel:
    """Holds a single open_clip model and produces normalized embeddings."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._lock = threading.Lock()
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._device = None
        self._precision = None
        self._loaded_name: str | None = None

    # -------------------------------------------------------------- lifecycle
    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def device(self) -> str | None:
        return self._device

    @property
    def loaded_name(self) -> str | None:
        return self._loaded_name

    @property
    def dimension(self) -> int | None:
        if not self.is_loaded:
            return None
        # Common CLIP output dimenion lives on the model. Fallbacks below.
        for attr in ("visual_output_dim", "proj_dim", "embed_dim", "output_dim"):
            val = getattr(self._model, attr, None)
            if isinstance(val, int):
                return val
        try:
            return int(self._model.visual.output_dim)
        except Exception:
            pass
        # Probe via a tiny dummy tensor (cast to model precision).
        import torch

        with torch.no_grad():
            dummy = torch.zeros(1, 3, 224, 224, device=self._device)
            if self._precision == "fp16":
                dummy = dummy.half()
            out = self._model.encode_image(dummy, normalize=True)
            return int(out.shape[-1])

    def load(self, progress_callback=None) -> str:
        """Load the model. Returns a ``"name (device)"`` descriptor string.

        ``progress_callback(done, total, message)`` is called during download.
        Per-byte streaming isn't exposed by open_clip/huggingface_hub, so we
        emit stage markers: (0, 0) before download, (1, 1) once loaded.
        """
        def _emit(done: int, total: int, message: str = "") -> None:
            if progress_callback is not None:
                try:
                    progress_callback(done, total, message)
                except Exception:
                    pass

        with self._lock:
            import torch

            _emit(0, 0, "preparing")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            precision = "fp16" if device == "cuda" else "fp32"
            pretrained = resolve_pretrained(self.model_name)
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.model_name,
                pretrained=pretrained,
                device=device,
                precision=precision,
            )
            _emit(1, 1, "loaded")
            tokenizer = open_clip.get_tokenizer(self.model_name)
            self._model = model
            self._preprocess = preprocess
            self._tokenizer = tokenizer
            self._device = device
            self._precision = precision
            self._loaded_name = self.model_name
            return f"{self.model_name} ({device})"

    def eject(self) -> None:
        with self._lock:
            if self._model is None:
                return
            try:
                import torch

                if self._device == "cuda":
                    del self._model
                    torch.cuda.empty_cache()
                else:
                    del self._model
                    if hasattr(torch, "cpu"):
                        try:
                            torch.cpu.empty_cache()  # type: ignore[attr-defined]
                        except Exception:
                            pass
            except Exception:
                pass
            self._model = None
            self._preprocess = None
            self._tokenizer = None
            self._device = None
            self._precision = None
            self._loaded_name = None

    # --------------------------------------------------------------- embedding
    def embed_bytes(self, data: bytes) -> list[float]:
        """Embed a single raw image (RGB-decoded) into a normalized float list."""
        with self._lock:
            import torch

            image = Image.open(io.BytesIO(data)).convert("RGB")
            tensor = self._preprocess(image).unsqueeze(0).to(self._device)
            with torch.no_grad():
                if self._precision == "fp16":
                    tensor = tensor.half()
                vec = self._model.encode_image(tensor, normalize=True)
            return vec.squeeze(0).float().cpu().tolist()

    def embed_bytes_batch(self, batch: list[bytes]) -> list[list[float]]:
        """Embed a batch of raw images, returning one normalized vector each."""
        if not batch:
            return []
        with self._lock:
            import torch

            tensors = []
            for data in batch:
                image = Image.open(io.BytesIO(data)).convert("RGB")
                tensors.append(self._preprocess(image))
            stacked = torch.stack(tensors).to(self._device)
            with torch.no_grad():
                if self._precision == "fp16":
                    stacked = stacked.half()
                vecs = self._model.encode_image(stacked, normalize=True)
            return [v.float().cpu().tolist() for v in vecs]

    def embed_text(self, text: str) -> list[float]:
        with self._lock:
            import torch

            tokens = self._tokenizer([text]).to(self._device)
            with torch.no_grad():
                # Token indices must remain Long/Int for the embedding lookup.
                # Do NOT cast tokens to half even on CUDA fp16.
                vec = self._model.encode_text(tokens, normalize=True)
            return vec.squeeze(0).float().cpu().tolist()
