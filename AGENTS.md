# AGENTS.md — HyCLIP Sifter

## Run it

```bash
source .venv/bin/activate
python -m hyclip_sifter
```

Everything runs from CWD (config, DB, queue JSON, thumbnail cache). The entrypoint is `hyclip_sifter/__main__.py` → `hyclip_sifter/main.py:main()`.

## Architecture

```
main.py          # startup: config → DB → ClipModel → HydrusService → MainWindow
├── config.py    # INI-backed, auto-backfills missing keys
├── database.py  # SQLite + sqlite-vector extension, all ops guarded by threading.RLock
├── clip_model.py   # open_clip wrapper, lazy-load, one global instance, threading.Lock
├── hydrus_service.py  # hydrus-api Client wrapper, retry on transient, no retry on 4xx
├── thumbnail_cache.py # disk cache at cache_dir/{hash[:2]}/{hash}.jpg
├── workers.py   # All workers extend QThread directly (not moveToThread)
└── ui/
    ├── main_window.py  # QMainWindow with 3 tabs + menu/toolbar/statusbar
    ├── ingest_tab.py   # bucket CRUD, model load/eject, ingest queue
    ├── search_tab.py   # query image/text → vector search → triage
    ├── history_tab.py  # browse triage history, undelete, re-ingest
    ├── thumbnail_grid.py  # QListWidget IconMode with lazy ThumbnailLoader
    ├── widgets.py      # helper widgets: hrule, section, count_badge, Toast
    ├── dialogs.py      # FirstRunDialog, PreferencesDialog
    └── theme.py        # QSS stylesheets with @variable substitution
```

**SPEC.md** is the authoritative design doc — code was written to it.

## API / library docs

- open_clip: https://github.com/mlfoundations/open_clip
- sqlite-vector
  - https://github.com/sqliteai/sqlite-vector
  - https://github.com/sqliteai/sqlite-vector/blob/main/API.md
- hydrus-api:
  - https://gitlab.com/cryzed/hydrus-api
  - https://hydrusnetwork.github.io/hydrus/client_api.html
  - https://hydrusnetwork.github.io/hydrus/developer_api.html

## Key conventions

- **CWD-relative paths:** config (`hyclip_sifter.ini`), DB (`hyclip_sifter.db`), queue (`hyclip_sifter.queue.json`), and thumb cache (`./thumb_cache/`) are all resolved against the working directory. Run the app from the project root.
- **No test suite, no lint/typecheck config.** There is nothing to run for verification.
- **Modifying the DB schema:** The `Database.__init__` runs `_create_base_tables` and `_init_existing_indices` on every startup. Add migrations / ALTER TABLE there or in a new migration method called from `__init__`.
- **Adding a worker:** All workers extend `_Cancellable(QThread)`. The cancel flag uses `threading.Lock`. Tabs use the `_retiring` pattern: disconnect signals, cancel, connect `finished` → `deleteLater`. See `ThumbnailGrid._retire_loader` for the canonical pattern.
- **Adding UI to a tab:** The QSS theme uses semantic role names (`@primary`, `@archive`, etc.) defined in `theme.py:ROLES`. Use `setObjectName()` to style widgets rather than inline `setStyleSheet`. The only exception is `count_badge()` in `widgets.py` — it has an inline style that won't update on theme change (known bug).
- **Cross-tab communication:** Use signals (`buckets_changed`, `model_state_changed`, `search_with_image`) wired in `MainWindow._wire_signals()`.

## Adding dependencies

The project has no `pyproject.toml` — dependencies live in `requirements.txt`. Install into the venv and add to `requirements.txt`.
