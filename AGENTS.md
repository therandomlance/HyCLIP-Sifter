# AGENTS.md

## Run

```bash
python main.py
```

Requires Python 3.12+ and a virtualenv at `.venv/` with `pip install -r requirements.txt`.

Config (`hyclip_sifter.ini`) and database (`hyclip_sifter.db`) paths are resolved against **CWD**, not the script directory. Run from the project root.

## No tests, no lint, no CI

There is no test suite, no formatter/linter config, and no CI workflows. The `TODO.md` file doubles as the project roadmap and known-bug tracker.

## Architecture

```
main.py              # entry point: QApplication + MainWindow
config.py            # INI config reader/writer with defaults
database.py          # SQLite + sqlite-vector extension; all DB access
clip_model.py        # open_clip wrapper; load, embed_bytes, unload
hydrus_service.py    # thin hydrus-api client wrapper
workers.py           # QThread subclasses: IngestWorker, SearchWorker,
                     #   ThumbnailLoader, ModelLoadWorker, HydrusCheckWorker
ui/
  main_window.py     # QMainWindow: wires services, tabs, status bar
  ingest_tab.py      # bucket CRUD, CLIP model load/eject, ingest hashes
  search_tab.py      # CLIP similarity search, triage operations
  history_tab.py     # browse past triage operations
  thumbnail_grid.py  # QListWidget with lazy thumbnail loading, selection,
                     #   context menu, drag-range select
```

## Threading model

- `database.py` uses `threading.RLock`; `clip_model.py` uses `threading.Lock`. Both are accessed from Qt worker threads.
- Workers extend `QThread`; signals connect `.start()`, `.cancel()`, `.wait()`, `.deleteLater()` on finished.
- The `_cancel` flag on workers is a plain bool with no mutex — relies on CPython GIL. Add synchronization if moving to free-threaded Python.
- `wait()` called on the main thread (`thumbnail_grid.py:138`, `search_tab.py:215`) causes visible UI hangs.

## Schema and operations

Bucket tables are named `bucket_{name}` (via `database.table_name()`). Bucket names allow only `[A-Za-z0-9_-]` with no spaces.

Operation codes stored in `history`:
| Code | Name    | Side effects via Hydrus API               |
|------|---------|-------------------------------------------|
| 0    | Delete  | trash files                               |
| 1    | Archive | archive files                             |
| 2    | Skip    | remove from bucket only                   |
| 3    | Defer   | add `hyclip:defer` tag, remove from bucket |

Removing a hash from a bucket automatically records it in the `history` table.

## Known issues

Refer to `TODO.md` for prioritized items.