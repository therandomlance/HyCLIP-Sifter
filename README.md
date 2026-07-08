# HyCLIP Sifter

Desktop application that bridges [open_clip](https://github.com/mlfoundations/open_clip) image embeddings with a [Hydrus Network](https://hydrusnetwork.github.io/hydrus/) instance. Build local vector-search indexes ("buckets") of images managed by Hydrus, search by visual similarity, and bulk-apply triage operations back to Hydrus.

## Requirements

- Python 3.12+
- A running [Hydrus Network](https://hydrusnetwork.github.io/hydrus/) instance with the Client API enabled

## Installation

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On first run, a dialog will prompt for your Hydrus API URL and access key. If you skip this or cancel, the app exits. YOu can edit `hyclip_sifter.ini` to configure it manually.

## Quick Start

```bash
./run.sh
```

1. **Load** the CLIP model (Ingest tab)
2. **Create** a bucket
3. **Paste** SHA256 hashes and start ingesting
4. **Search** by image similarity (Search tab)
5. **Triage** results — archive, delete, skip, or defer

Everything runs from CWD: config, database, queue file, and thumbnail cache all resolve relative to the working directory.

## Configuration

All settings live in `hyclip_sifter.ini` (CWD). Missing keys/sections are backfilled with defaults automatically.

| Section  | Key                  | Default                   | Description                              |
|----------|----------------------|---------------------------|------------------------------------------|
| `hydrus` | `api_url`            | `http://127.0.0.1:45869` | Hydrus Client API base URL               |
| `hydrus` | `api_key`            | *(empty)*                 | Hydrus API access key                    |
| `hydrus` | `tag_service_key`    | *(empty)*                 | Tag service key for Defer operations     |
| `hydrus` | `retries`            | `3`                       | Retry attempts for transient API errors  |
| `hydrus` | `retry_delay_ms`     | `1000`                    | Delay between retries (ms)               |
| `clip`   | `model`              | `ViT-B-16-SigLIP2`        | open_clip model name                     |
| `clip`   | `load_on_startup`    | `false`                   | Auto-load model on launch                |
| `ui`     | `thumbnail_size`     | `400`                     | Default thumbnail size (px)              |
| `ui`     | `search_size`        | `50`                      | Default number of search results         |
| `ui`     | `confirm_triage`     | `true`                    | Show confirmation toasts before triage   |
| `ui`     | `theme`              | `system`                  | dark / light / system                    |
| `ui`     | `stylesheet`         | *(empty)*                 | Optional path to a `.qss` override file  |
| `ui`     | `thumbnail_cache_dir`| `./thumb_cache/`          | Local thumbnail cache directory          |
| `ui`     | `ingest_batch_size`  | `0`                       | Embedding batch size (0 = auto: 8 GPU, 1 CPU) |

## Features

- **Vector search** — find visually similar images by CLIP embedding with optional text refinement and negative queries
- **Inline ingest** — paste SHA256 hashes, batch-download from Hydrus, embed, and store
- **Triage workflow** — archive, delete, skip, or defer images directly from search results, with keyboard shortcuts and floating action bar
- **Incremental results** — search results stream in as the vector scan progresses
- **Deduplication scan** — find near-duplicate image pairs in a bucket by cosine distance
- **History browser** — review past triage operations by bucket and operation type; re-ingest or restore from trash
- **Queue persistence** — ingest queue survives restarts via `hyclip_sifter.queue.json`
- **Thumbnail cache** — local disk cache avoids repeated Hydrus API calls
- **Dark / light theme** — built-in with support for user-provided QSS overrides
- **Keyboard shortcuts** — full keyboard navigation for search, triage, and tab switching (`Ctrl+1/2/3`)

## Architecture

```
hyclip_sifter/
├── __main__.py          # python -m entrypoint
├── main.py              # startup: config → DB → CLIP → Hydrus → MainWindow
├── config.py            # INI-backed, auto-backfills missing keys
├── database.py          # SQLite + sqlite-vector, all ops guarded by RLock
├── clip_model.py        # open_clip wrapper, lazy-load, single global instance
├── hydrus_service.py    # hydrus-api client wrapper with retry logic
├── thumbnail_cache.py   # disk cache at cache_dir/{hash[:2]}/{hash}.jpg
├── workers.py           # All workers extend QThread directly
└── ui/
    ├── main_window.py   # QMainWindow with 3 tabs + menu/toolbar/statusbar
    ├── ingest_tab.py    # bucket CRUD, model load/eject, ingest queue
    ├── search_tab.py    # query image/text → vector search → triage
    ├── history_tab.py   # browse triage history, undelete, re-ingest
    ├── thumbnail_grid.py # QListWidget IconMode with lazy ThumbnailLoader
    ├── widgets.py       # helper widgets
    ├── dialogs.py       # FirstRunDialog, PreferencesDialog
    └── theme.py         # QSS stylesheets with semantic color roles
```

Workers (`QThread` subclasses): `IngestWorker`, `SearchWorker`, `ThumbnailLoader`, `ModelLoadWorker`, `HydrusCheckWorker`, `HydrusOperationWorker`, `DedupWorker`. All run on background threads so the UI stays responsive.

## Triage Operations

| Operation | Hydrus API Call                    | Database Effect                     |
|-----------|------------------------------------|-------------------------------------|
| Archive   | `archive_files(hashes)`            | Remove from bucket, record history  |
| Delete    | `delete_files(hashes)`             | Remove from bucket, record history  |
| Skip      | *(none)*                           | Remove from bucket, record history  |
| Defer     | `add_tags(hashes, "hyclip:defer")` | Remove from bucket, record history  |

All four are permanent and recorded in the `history` table. There is no undo — use the History tab's "Re-ingest into bucket" to recover accidentally skipped images.
