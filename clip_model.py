import io
import threading

import open_clip
import torch
from PIL import Image


def resolve_pretrained(model_name: str) -> str | None:
    tags = open_clip.list_pretrained_tags_by_model(model_name)
    if tags:
        return tags[0]
    pairs = open_clip.list_pretrained()
    for name, tag in pairs:
        if name == model_name:
            return tag
    return None


def model_dimension(model_name: str) -> int:
    cfg = open_clip.get_model_config(model_name)
    if not cfg:
        raise ValueError(f"unknown open_clip model: {model_name}")
    return int(cfg["embed_dim"])


class ClipModel:
    def __init__(self) -> None:
        self.model = None
        self.preprocess = None
        self.device = "cpu"
        self.model_name: str | None = None
        self.pretrained: str | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def load(self, model_name: str) -> None:
        with self._lock:
            self._unload_locked()
            device = "cuda" if torch.cuda.is_available() else "cpu"
            precision = "fp16" if device == "cuda" else "fp32"
            pretrained = resolve_pretrained(model_name)
            model, _, preprocess = open_clip.create_model_and_transforms(
                model_name,
                pretrained=pretrained,
                precision=precision,
                device=device,
            )
            model.eval()
            self.model = model
            self.preprocess = preprocess
            self.device = device
            self.model_name = model_name
            self.pretrained = pretrained

    def _unload_locked(self) -> None:
        if self.model is not None:
            del self.model
            self.model = None
            self.preprocess = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def unload(self) -> None:
        with self._lock:
            self._unload_locked()

    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        with self._lock:
            if self.model is None or self.preprocess is None:
                raise RuntimeError("CLIP model is not loaded")
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            tensor = self.preprocess(image).unsqueeze(0).to(self.device)
            model_dtype = next(self.model.parameters()).dtype
            if tensor.dtype != model_dtype:
                tensor = tensor.to(model_dtype)
            with torch.no_grad():
                embedding = self.model.encode_image(tensor, normalize=True)
            return embedding[0].float().cpu().tolist()

    @property
    def dimension(self) -> int:
        if self.model_name:
            return model_dimension(self.model_name)
        raise RuntimeError("no model configured")
