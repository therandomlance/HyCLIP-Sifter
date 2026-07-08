# HyCLIP Sifter — Specification

## 1. Project Overview

HyCLIP Sifter is a desktop application that bridges the [open_clip](https://github.com/mlfoundations/open_clip) image embedding library with a [Hydrus Network](https://hydrusnetwork.github.io/hydrus/) instance. It lets a user build local vector-search indexes ("buckets") of images managed by Hydrus, then search those buckets by visual similarity using CLIP embeddings, and bulk-apply triage operations (archive, delete, defer, skip) back to Hydrus.

The target user has a large Hydrus library and wants to find images visually similar to a reference image (e.g. to delete bad images near good ones, or group related images). All operations that hit the Hydrus API or perform heavy computation run on background threads so the UI stays responsive.

Carefully review the docs for these libraries

- PySide6
- open_clip: https://github.com/mlfoundations/open_clip
- sqliteai-vector
  - https://github.com/sqliteai/sqlite-vector
  - https://github.com/sqliteai/sqlite-vector/blob/main/API.md
- hydrus-api
  - https://gitlab.com/cryzed/hydrus-api
  - https://hydrusnetwork.github.io/hydrus/client_api.html
  - https://hydrusnetwork.github.io/hydrus/developer_api.html

These dependencies have already been installed in a venv.

---

## 2. Configuration and First-Run

### 2.1 Configuration File

A `.ini` file (default `hyclip_sifter.ini`, resolved against the current working directory) stores all settings:

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `hydrus` | `api_url` | `http://127.0.0.1:45869` | Hydrus Client API base URL |
| `hydrus` | `api_key` | *(empty)* | Hydrus API access key |
| `hydrus` | `tag_service_key` | *(empty)* | Key of the tag service used for the "Defer" operation |
| `hydrus` | `rating_service_key` | *(empty)* | Key of the rating service (stored for future use) |
| `clip` | `model` | `ViT-B-16-SigLIP2` | open_clip model name |
| `clip` | `load_on_startup` | `false` | Whether to auto-load the CLIP model on launch |
| `ui` | `thumbnail_size` | `400` | Default thumbnail size in pixels |
| `ui` | `search_size` | `50` | Default number of search results |
| `ui` | `confirm_triage` | `true` | Whether to show confirmation dialogs before triage operations |
| `ui` | `hydrus_retries` | `3` | Number of retry attempts for transient Hydrus API failures |
| `ui` | `hydrus_retry_delay_ms` | `1000` | Delay between Hydrus API retries in milliseconds |

The config system automatically backfills missing keys/sections with defaults so the file can be hand-edited without breaking.

### 2.2 First-Run Dialog

If no `.ini` file exists at startup, the app shows a modal dialog prompting for:
- Hydrus API URL (pre-filled with default)
- Hydrus API access key
- Hydrus tag service key

All three are optional except the URL. On accept, a new `.ini` is written with defaults for all other sections. On cancel, the app exits gracefully.

### 2.3 Start on Startup Toggle

When `load_on_startup` is `true`, the CLIP model loading begins automatically on app launch (delegated to the Ingest tab's model loader).

### 2.4 Window Geometry Persistence

The window size and position are saved on close and restored on next launch using `QSettings`. The initial default is computed from `screen().availableGeometry()` rather than hardcoded dimensions.

---

## 3. Database

### 3.1 Storage Engine

SQLite with the [sqlite-vector](https://github.com/sqliteai/sqlite-vector) extension loaded at connect time. The extension file is expected at `<sqlite_vector_module_path>/binaries/vector`. If it fails to load, the app reports an error with the extension path.

The database file (`hyclip_sifter.db` by default) is resolved against CWD. SQLite is opened with `check_same_thread=False` to allow access from worker threads, and all operations are protected by a single `threading.RLock`.

The database uses SQLite WAL journal mode to reduce lock contention between concurrent reads and writes from worker threads.

### 3.2 Schema

**`buckets` table:**
| Column | Type | Description |
|--------|------|-------------|
| `name` | TEXT PK | Bucket name (validated: no spaces, `[A-Za-z0-9_-]` only) |
| `dimension` | INTEGER | Embedding vector dimension for this bucket |

**`history` table:**
| Column | Type | Description |
|--------|------|-------------|
| `hash` | TEXT | SHA256 hash of the removed image |
| `bucket` | TEXT | Bucket the hash was removed from |
| `operation` | INTEGER | Operation code (0=Delete, 1=Archive, 2=Skip, 3=Defer) |
| `timestamp` | TEXT | ISO-8601 timestamp of when the operation was performed |

History is ordered by `timestamp DESC` (falling back to `rowid DESC` for pre-migration records).

**Per-bucket tables:** Named `bucket_{bucket_name}`:
| Column | Type | Description |
|--------|------|-------------|
| `hash` | TEXT PK | SHA256 hash of the image |
| `embedding` | BLOB | FLOAT32 embedding vector (managed by sqlite-vector) |

The `embedding` column is a virtual vector column backed by sqlite-vector's `vector_init` / `vector_as_f32` functions.

### 3.3 Vector Search

Nearest-neighbor search uses `vector_quantize_scan`, with lazy quantization triggered on dirty writes. On each search, the database:
1. Quantizes the column if dirty (`vector_quantize`)
2. Optionally preloads the quantized index into memory (`vector_quantize_preload`)
3. Scans for `k` nearest neighbors by cosine distance, optionally excluding a given hash

Quantization of newly-written embeddings is deferred to a background thread and batched, so frequent small writes (like single-image ingests) don't trigger repeated re-quantization that delays the next search.

### 3.4 Operations

| Method | Description |
|--------|-------------|
| `create_bucket(name, dim)` | Creates bucket table + vector index, inserts into `buckets` |
| `delete_bucket(name)` | Drops bucket table, removes from `buckets` (cleans up quantized index first) |
| `rename_bucket(old_name, new_name)` | Renames a bucket: renames the table and updates `buckets` entry. History records retain the old name. |
| `list_buckets()` | Returns all bucket names ordered |
| `bucket_dimension(name)` | Returns embedding dimension for a bucket |
| `bucket_count(name)` | Returns count of hashes in a bucket |
| `add_embedding(bucket, hash, embedding)` | Inserts/replaces embedding into bucket table |
| `has_hash(bucket, hash)` | Checks if hash exists in bucket |
| `get_embedding(bucket, hash)` | Decodes blob to list of floats |
| `nearest_neighbors(bucket, query_blob, k, exclude_hash)` | Returns top-k results as `[(hash, distance)]` |
| `random_sample(bucket, k)` | Returns k random hashes from bucket |
| `remove_from_bucket(bucket, hash, operation)` | Deletes hash from bucket, records in history with timestamp |
| `restore_to_bucket(bucket, hash)` | Re-inserts a hash from history back into a bucket (requires re-embedding) |
| `history_query(bucket, operation, limit)` | Returns hashes from history for given bucket/op, newest first |
| `history_query_filtered(bucket, operation, limit, search)` | Returns hashes from history matching a text filter on the hash prefix |
| `history_buckets()` | Returns `[(bucket, count)]` for all buckets with history |
| `history_counts(bucket)` | Returns `{operation: count}` summary for a bucket |
| `history_export_csv(bucket, operation)` | Exports matching history rows to a CSV file |
| `verify_integrity()` | Checks for orphaned vector indices or history entries referencing deleted buckets |

### 3.5 Embedding Storage Format

Embeddings are stored as FLOAT32 blobs. On retrieval, they're unpacked with `struct.unpack("<Nd", blob)` where N is the bucket dimension.

### 3.6 Deduplication Detection

The database supports a `find_duplicates(bucket, threshold)` method that returns pairs of hashes within the given cosine distance threshold, using a self-join scan of the vector index.

---

## 4. CLIP Model

### 4.1 Library

Uses [open_clip_torch](https://github.com/mlfoundations/open_clip) (`open_clip` package). The model is loaded lazily (user action or auto-startup toggle). Only one model instance exists globally.

### 4.2 Model Selection

The model name is read from config (`clip.model`). A `resolve_pretrained()` function looks up the first available pretrained tag for that model name from open_clip's registry. The model is loaded with:
- Device: CUDA if available, otherwise CPU
- Precision: FP16 on CUDA, FP32 on CPU

During model download (first-time use), progress is streamed to the UI so the user sees download status rather than a frozen "Loading model..." message.

### 4.3 Embedding Generation

**Image embedding:** Accepts raw image bytes, decodes via PIL with RGB conversion, applies open_clip's preprocessing transform, runs `model.encode_image` with `normalize=True`, returns a normalized float list.

**Text embedding:** Tokenizes via `open_clip.get_tokenizer(model_name)`, runs `model.encode_text` with `normalize=True`, returns a normalized float list.

**Batch image embedding:** Accepts a list of raw image bytes, preprocesses them into a stacked tensor, and runs `model.encode_image` on the batch with `normalize=True`. Returns a list of normalized float lists. On GPU, batch embedding significantly improves ingest throughput. Batch size is configurable (default 8 on GPU, 1 on CPU).

Both methods hold a `threading.Lock` to serialize access, since they may be called from multiple worker threads.

### 4.4 Lifecycle

- **Load:** Creates model on a background thread (`ModelLoadWorker`). Emits signals on success (model name + device) or failure.
- **Eject:** Unloads model from RAM, clears CUDA cache if applicable.
- **Startup auto-load:** If `load_on_startup` is true, `load_model()` is called from the main window constructor after tabs are initialized.

### 4.5 Thread Safety

The model's `_lock` ensures serialized access to the model during embedding. The `is_loaded` property is a simple bool check (no lock), relying on the GIL for atomicity. Workers check `is_loaded` before attempting to embed.

---

## 5. Hydrus API Client

### 5.1 Client

Uses the `hydrus-api` Python package. Configured with the API URL and access key from config.

### 5.2 API Calls

| Operation | Endpoint | Purpose |
|-----------|----------|---------|
| `get_file(hash)` | GET file | Download full file bytes for embedding |
| `get_thumbnail(hash)` | GET thumbnail | Download thumbnail for display |
| `get_file_path(hash)` | GET file path | Get local filesystem path (for "open externally") |
| `get_file_metadata(hashes)` | GET metadata | Batch-fetch file extensions for filtering |
| `archive_files(hashes)` | POST archive | Archive files in Hydrus |
| `delete_files(hashes)` | POST delete | Send files to Hydrus trash |
| `undelete_files(hashes)` | POST undelete | Remove files from trash (restore) |
| `add_tags(hashes, service_key, tags)` | POST add tags | Add tags to files (used for Defer) |
| `verify_access_key()` | GET verify | Check access key validity |
| `get_service(service_key)` | GET service | Check service key validity |
| `get_api_version()` | GET version | Get Hydrus API version |
| `get_file_search(hashes)` | POST search | Fetch hashes matching a Hydrus search query |

### 5.3 Extension Filtering

Only files with extensions in `{png, jpg, jpeg, webp, tiff, tif}` are eligible for embedding. The Hydrus API is queried for file metadata in batch before individual download.

### 5.4 Error Handling

All Hydrus API calls have configurable retry logic: on transient network failures (connection errors, timeouts, 5xx responses), the operation retries up to a configurable number of times (`hydrus_retries`) with a configurable delay between attempts (`hydrus_retry_delay_ms`). Fatal errors (4xx, auth failures, service key invalid) are not retried and are reported immediately.

---

## 6. Tabs and UI

### 6.1 Window Structure

A `QMainWindow` with:
- Title: "HyCLIP Sifter"
- Minimum window size: 1024×700
- Initial size computed as 75% of `screen().availableGeometry()` (width and height)
- Window position and size saved/restored via `QSettings` between sessions
- A **menu bar** with:
  - **File:** Preferences (opens settings dialog), Quit
  - **Edit:** Copy (copies selected hash), Select All / Deselect All / Invert Selection
  - **View:** Theme submenu (Dark / Light / System default), Thumbnail Size submenu (Small 150px / Medium 300px / Large 450px presets)
  - **Help:** About, Keyboard Shortcuts reference (opens a non-modal reference popup)
- A **thin toolbar** below the menu bar with context-sensitive actions (visible on all tabs):
  - Search tab: Back/Forward search history navigation, Stop Search button
  - Ingest tab: Load Model / Eject Model buttons visible regardless of scroll position
- A **status bar** with:
  - Left: current operation/status message text
  - Right: persistent status indicator row — model state icon (`🧠 Not loaded` or `🧠 ViT-B-16 (CUDA)`), Hydrus connection dot (green/red), bucket count badge
  - Styled with a slightly darker background and 1px top border to separate from content
- A `QTabWidget` with three tabs in workflow order: **Ingest | Search | History**
  - On first launch (no model loaded, no buckets exist): defaults to the Ingest tab
  - If a first-launch state is detected, a thin banner appears at the top of every tab: *"Welcome! ① Load the CLIP model → ② Create a bucket → ③ Ingest hashes → ④ Start searching"* — each step number is a clickable link that navigates to the relevant control
- Built-in theme switching (dark/light/system) via `View` menu or config setting. All colors use semantic CSS-like role names defined in QSS (see section 16), not hardcoded inline `setStyleSheet` calls. Roles include `--color-primary`, `--color-archive`, `--color-delete`, `--color-defer`, `--color-skip`, `--color-surface`, `--color-border`.
- Support for user-provided QSS stylesheets: a `ui.stylesheet` config key points to a `.qss` file loaded after the built-in theme, overriding any widget style
- **QSplitter** sidebars on Search and History tabs (not fixed-width): initial split at 260px, collapsible to 180px, user-draggable
- Consistent spacing: all section frames use 12px internal padding, 8px between adjacent sections, 4px vertical / 6px horizontal spacing between paired controls
- Standardized button sizes: primary action buttons 28–32px tall, secondary buttons 24–28px tall, icon-only buttons square at 28×28px
- Clean shutdown on close: cancels all running workers, waits up to 2 seconds each (non-blocking via `finished` signal gates), closes DB only after confirming no workers access it, unloads CLIP model, saves window geometry

Cross-tab signals:
- `buckets_changed`: emitted by Ingest and Search tabs, triggers refresh of all tab bucket lists
- `model_state_changed`: emitted by Ingest tab, triggers refresh of Search tab model-dependent controls
- `search_with_image(hash)`: emitted by History tab, switches to Search tab and runs a search with that hash as the query

### 6.2 Search Tab

**Purpose:** Find visually similar images in a bucket and triage them.

**Layout:** Horizontal split using `QSplitter` — sidebar on the left (resizable), thumbnail grid taking remaining space.

#### Sidebar — Visual Organization

The sidebar is divided into three visually-distinct groups separated by `QFrame` horizontal rules with bold section titles:

```
┌─ QUERY ──────────────────────┐
│  [Query Thumbnail 180×180]   │    ← scales with sidebar width, min 150px
│  [-- Set Negative Image --]  │    ← collapsible button, hidden by default
│  Text influence:             │
│  [________________________]  │
│  [+] [1.50x]                 │    ← tooltip: "Added to image embedding.
│                              │        + = more like this, − = less like this"
├─ SEARCH ─────────────────────┤
│  Bucket: [▼______________]   │
│  Results: [50       ]        │
│  Size:    [300 px  ]         │
│  [       Search       ]      │    ← full-width, accent color
│  [    Random Sample    ]     │    ← full-width, secondary style
│  [Clear Query] [Deselect]    │    ← smaller link-style buttons
├─ TRIAGE ─────────────────────┤
│  [Archive]  [Delete  ]       │    ← primary color from theme
│  [Defer  ]  [Skip    ]       │
└──────────────────────────────┘
```

#### Sidebar Controls — Detail

1. **Query thumbnail** (180×180, scaling with sidebar): Displays the current query image thumbnail. Double-clicking clears the query image. Shows placeholder text when no query image is set. The size scales with the sidebar width (min 150px) to avoid overflow on narrow panels.

2. **Negative query toggle** (collapsible, hidden by default): A button labeled "Set Negative Image" that reveals a second smaller thumbnail slot. When set, its embedding is subtracted element-wise from the query vector. Right-click a grid thumbnail and choose "Add to negative query" to populate it. Provides "find images similar to A but NOT similar to B" functionality.

3. **Text influence controls:**
   - **Text input:** `QLineEdit` with placeholder "Describe what to look for (or avoid)..."
   - **Sign toggle:** A checkable `±` button (28×28px square) controlling whether the text embedding is added (+) or subtracted (−) from the image embedding
   - **Weight spinbox:** `QDoubleSpinBox` (0.0–100.0, step 0.25, default 1.5, suffix "x")
   - The row is labeled "Text influence:" with a tooltip: *"The text embedding (× weight) is combined with the image embedding before search. Use + for 'more like this', − for 'less like this'."*
   - All text controls disabled unless CLIP model is loaded

4. **Search configuration:**
   - **Bucket selector:** `QComboBox` listing all buckets
   - **Number of results:** `QSpinBox` 1–2000, default from config
   - **Thumbnail size:** `QSpinBox` 48–512, step 25, suffix " px", default from config. Changes the grid's icon size in real time

5. **Primary action buttons:**
   - **Search** (full-width, accent color): Runs nearest-neighbor search with current query
   - **Random Sample** (full-width, secondary style): Clears query image, fetches random sample
   - **Clear Query** and **Deselect All** (smaller link-style buttons, secondary actions)

6. **Triage buttons** (2×2 grid, colors from theme semantic roles):
   - **Archive** (green): Archives selected images in Hydrus
   - **Delete** (red): Sends selected images to trash in Hydrus
   - **Defer** (purple): Adds `hyclip:defer` tag, removes from bucket
   - **Skip** (yellow/amber): Removes from bucket only

   Triage buttons show keyboard shortcut hints in tooltips: `"Archive selected images (A)"`.

#### Floating Triage Bar (Primary Triage UI)

When one or more thumbnails are selected in the grid, a **floating triage bar** appears at the bottom edge of the grid (semi-transparent overlay, auto-hides when nothing is selected):

```
[Archive (A)] [Delete (D)] [Defer (F)] [Skip (S)]  │  12 selected
```

This is the primary triage surface — the user never needs to leave the grid to triage. The sidebar triage buttons serve as a secondary access point. The bar includes a selection count badge on the right.

#### Grid Header Bar

Above the thumbnail grid, a small header bar shows:

```
"72 results from 'my_bucket'  |  Sorted by: distance ▼"  |  [Show Duplicates]
```

- Result count updates live as incremental results stream in
- Sort dropdown: distance ascending (default), distance descending, random
- "Show Duplicates" button triggers a deduplication scan (see Search Behavior)

#### Search Behavior

**Image query:** The query image's embedding is fetched from the database if it's already in the bucket. If not, it's re-embedded on-the-fly via CLIP (requires model loaded). The hash is excluded from results so the query image doesn't appear as its own nearest neighbor.

**Text query:** The text is embedded via `clip.embed_text()`. Each value is multiplied by the weight multiplier (possibly negative), then element-wise added to the image embedding (if present) to form the combined query vector.

**Negative image query:** If a negative query image is set, its embedding is subtracted element-wise from the combined query vector.

**Multi-image query:** When multiple images are selected in the grid and "Search using selection" is invoked (via right-click or `Shift+Enter`), the embeddings of all selected images are averaged to form the query vector.

**If neither image nor text is provided (or `random=True`):** A random sample of k hashes is returned from the bucket. The query image is cleared.

**Result display:** Results populate the thumbnail grid. Tooltips show `"hash\ndistance: X.XXXX"` for search results. Distance badges (`0.042`) appear in the top-left corner of each thumbnail so similarity is visible at a glance without hovering. On hover after ~500ms, a larger popup preview appears next to the cursor.

**Incremental results:** Results are streamed to the grid in batches as the vector scan progresses. The grid updates progressively, showing available thumbnails immediately. The grid header bar updates the result count live.

**Refinement:** When a text query is present alongside an image, the embeddings are combined by element-wise addition. If dimensions don't match, an error is reported.

**Deduplication scan:** When invoked, the system performs a self-join cosine-distance check on all embeddings in the bucket and highlights pairs below the threshold. Results are shown as color-coded borders (orange for close, red for very close) on the grid items.

#### Triage Workflow

1. User selects one or more thumbnails (click, drag-range, Shift+Click, Ctrl+Click, or `Ctrl+A`)
2. The floating triage bar appears at the bottom of the grid showing the selection count
3. User clicks a triage button in the floating bar, presses a keyboard shortcut (A/D/S/F), or uses the context menu
4. If `confirm_triage` is enabled in config, a non-blocking **toast notification** slides up at the bottom of the grid: `"Archive 5 images? [Yes] [No]"` — auto-dismisses after 5 seconds (no = timeout). This replaces modal `QMessageBox` dialogs.
5. While the operation is in-flight, the triage button text changes to show a spinner + progress: `"Archiving 5..."`
6. On success: each hash is removed from the database bucket (recorded in history) and the thumbnail fades out of the grid. A toast confirms: `"Archived 5 images"` (auto-dismiss 3s)
7. The `buckets_changed` signal is emitted to update counts elsewhere

**Visual preview:** When hovering over a triage button with selection active, selected thumbnails briefly tint green (archive), red (delete), purple (defer), or yellow (skip) as a preview.

#### Keyboard Shortcuts (Complete)

| Shortcut | Context | Action |
|----------|---------|--------|
| `A` | Grid has selection | Archive selected |
| `D` | Grid has selection | Delete selected |
| `S` | Grid has selection | Skip selected |
| `F` | Grid has selection | Defer selected |
| `Delete` | Grid has selection | Delete selected (same as D) |
| `Ctrl+A` / `Cmd+A` | Grid focused | Select all thumbnails |
| `Ctrl+Shift+A` | Grid focused | Deselect all |
| `Ctrl+I` | Grid focused | Invert selection |
| `Enter` | Grid focused, 1 item selected | Search using selected image |
| `Shift+Enter` | Grid focused, multiple selected | Search using averaged selection |
| `Ctrl+Enter` | Search tab | Run search with current query |
| `Ctrl+R` | Search tab | Random sample |
| `Ctrl+L` | Search tab | Clear query |
| `Space` | Grid focused | Toggle selection of focused item |
| `Arrow keys` | Grid focused | Move focus rect between thumbnails |
| `Shift+Arrow` | Grid focused | Extend selection from anchor |
| `Home` | Grid focused | Jump to first thumbnail |
| `End` | Grid focused | Jump to last thumbnail |
| `Page Up/Down` | Grid focused | Jump one page of thumbnails |
| `Escape` | Search tab | Deselect all / clear query |
| `Ctrl+1/2/3` | Any | Switch to Ingest / Search / History tab |
| `Ctrl+P` | Any | Open Preferences dialog |
| `F5` | History tab | Refresh history search |

Shortcut hints appear in button tooltips and in the Keyboard Shortcuts reference window accessible from the Help menu.

#### Context Menu (per-grid-item)

The context menu is built once per `ThumbnailGrid` instance and updated on `aboutToShow` (not recreated per right-click). Items:

- **Search using this image** — sets as query hash and runs a search
- **Search using selection** — element-wise adds selected images' embeddings and runs a search (appears when multiple items selected)
- **Add to negative query** — sets as the negative query image (appears when negative query slot is enabled)
- *(separator)*
- **Archive** — archives selected images
- **Delete** — deletes selected images
- **Skip** — skips (bucket removal only)
- **Defer** — defers (add `hyclip:defer` tag, remove from bucket)
- *(separator)*
- **Open externally** — opens the file with the system default application (if local path available)
- **Copy file path** — copies the local filesystem path to clipboard (if available)
- **Open containing folder** — opens the file's parent folder in the file manager
- *(separator)*
- **Select All** / **Deselect All** / **Invert Selection**

#### Thumbnail Grid

A `QListWidget` in `IconMode` with:

- **Loading skeleton:** Each empty cell shows an animated pulse placeholder (gray rounded rectangle) while the thumbnail is in-flight from cache or Hydrus, giving immediate feedback that content is loading
- **Custom selection style:** Selected items get a 2px colored border (accent color from theme) with a subtle corner checkmark overlay, rather than the default system blue highlight
- **Distance badge:** Search results display their cosine distance in a small semi-transparent badge in the top-left corner of the thumbnail
- **Grid styling:** 4–6px gap between cells, rounded corners (`border-radius: 4px`) on thumbnail containers via stylesheet
- **Lazy thumbnail loading:** When hashes are set, a `ThumbnailLoader` worker fetches thumbnails. Disk cache is checked first, Hydrus API is the fallback
- **Hover preview popup:** Hovering for ~500ms shows a larger popup preview next to the cursor
- **Multi-selection:** Standard `MultiSelection` mode
- **Drag-range select:** Mouse drag (without modifiers) creates a range selection from drag-start to current row. **Auto-scrolls** smoothly when the cursor is within 30px of the top or bottom grid edge
- **Shift+Click range select:** Extends selection from the anchor row
- **Ctrl+Click toggle:** Toggles individual item selection
- **Keyboard navigation:** Arrow keys move a visible focus rect (dashed border) between thumbnails without changing selection. Space toggles the focused item. Enter searches using the focused image. Home/End jump to first/last
- **Icon size:** Configurable in real time (all items re-rendered)
- **Worker retirement:** Old loaders are cancelled, signals disconnected, and queued for deferred deletion via `_retiring` list — no blocking `wait()` calls on the main thread

### 6.3 Ingest Tab

**Purpose:** Create/manage buckets, load/eject the CLIP model, embed hashes into buckets, test the Hydrus API connection.

**Layout:** Three sections arranged vertically — no scrolling needed on any reasonable screen height.

#### Top Section — Two-Column Layout (Buckets + Add to Bucket)

```
┌── Buckets ────────────┬── Add to Bucket ──────────────────────────┐
│ [New] [Delete] [Rename]│ [Hash text area (stretches to fill height)] │
│ [▼ bucket_combo     ] │                                            │
│                        │ [Paste] [Clear] [Import from Search...]    │
│ [■ 42 in bucket] [■ 5A│ [              Start Ingest             ]  │
│  ■ 3D] [■ 1S] [■ 2F] │                                            │
└────────────────────────┴────────────────────────────────────────────┘
```

The left column (Buckets, ~280px) and right column (Add to Bucket, filling remaining width) use horizontal space efficiently.

**Buckets column:**
- **New Bucket:** Prompts for a name (no spaces, alphanumeric + `_-`). Creates the bucket in the database with the dimension of the configured CLIP model.
- **Delete Bucket:** Deletes the currently selected bucket, preserving history. Confirms with a dialog.
- **Rename Bucket:** Prompts for a new name and renames the bucket table in-place. History records retain the old bucket name.
- **Copy/Move between buckets:** A dialog to select hashes from one bucket and copy or move their embeddings to another bucket (requires same CLIP dimension).
- **Bucket combo:** Dropdown of all buckets.

**Bucket counts display** — a horizontal row of small colored stat badges (not a pipe-delimited text label):

```
[■ 42 in bucket]  [■ 5 archived]  [■ 3 deleted]  [■ 1 skipped]  [■ 2 deferred]
```

Each badge is a small `QFrame` with rounded corners and a color (blue for active, green for archived, red for deleted, yellow for skipped, purple for deferred). Clicking a badge switches to the History tab pre-filtered to that bucket and operation.

**Add to Bucket column:**
- **Hash text area:** Multi-line `QTextEdit` for pasting SHA256 hashes. Supports drag-and-drop of `.txt` files. Stretches to fill available vertical space.
- **Paste from Clipboard:** Pastes clipboard content into the text area.
- **Clear:** Clears the text area.
- **Import from Hydrus search:** Opens a dialog to paste a Hydrus search query; the app fetches matching hashes via the Hydrus search API and populates the text area.
- **Start Ingest:** Queues the current hashes into the ingest queue for the selected bucket.

#### Middle Section — Ingest Queue (Full Width)

```
┌── Ingest Queue — 2 jobs remaining ───────────────────────────────┐
│  ▶ my_bucket                                                      │
│    120 files  ████████░░░░░░░░░░  42%  ~3m remaining              │
│  ⏳ other_bucket (120 files)                                      │
│  [⏸ Paused] [Clear] [Save Queue...]                               │
└────────────────────────────────────────────────────────────────────┘
```

- **Custom item widgets** per queue row (using `QListWidgetItem` + `setItemWidget`), not plain text strings. Each row shows bucket name, a progress bar with percentage, and an estimated time remaining
- **Animated active indicator:** The currently-running job shows a pulsing dot or small spinner instead of a static `▶` prefix
- **"Jobs remaining: N"** in the section header, updating live
- **Pause/Resume button:** Fixed-width toggle with icon + text. Background color changes: yellow when paused, default when running. Text reads "⏸ Paused" or "▶ Running"
- **Clear button:** Clears pending jobs (disabled while a job is running)
- **Save Queue button:** Manually triggers a queue save to disk
- **Queue persistence:** Saved to `hyclip_sifter.queue.json` in CWD after every change, loaded on tab construction. Periodically auto-saved during active ingest to preserve partial progress

#### Bottom Section — Setup (Full Width)

```
┌── Setup ─────────────────────────────────────────────────────────┐
│  Model: [Loaded: ViT-B-16-SigLIP2 (cuda)] [Eject]                │
│  Hydrus: [✓ Connected — API v42, permissions: search, manage] [Test]│
└───────────────────────────────────────────────────────────────────┘
```

A single compact section replacing the former separate Model and Hydrus API sections. Both are one-line status displays with an action button.

**Model row:**
- **Status label:** Shows "Model not loaded", "Loading model... (downloading...)", or "Loaded: {name} ({device})"
- **Load Model:** Starts `ModelLoadWorker` on a background thread. During first-time model download, the label shows download progress
- **Eject Model:** Unloads the model from memory
- Load enabled when model not loaded; Eject enabled when model loaded

**Hydrus row:**
- **Inline status:** On load and after test, shows a green checkmark with version and permissions, or a red X with error — directly in the section, no modal dialog
- **Test Hydrus API:** Starts a `HydrusCheckWorker` that verifies API reachability, access key, and service keys. Results update the inline status label rather than opening a modal

**Button states** (cross-section):
- Ingest-related controls (hash area, paste, clear, import, start ingest) enabled only when model is loaded AND a bucket is selected
- Delete bucket disabled when queue is active
- Clear queue disabled when a job is running

#### Ingest Processing (per job)

1. Fetch file metadata in batch → determine extensions
2. Split remaining hashes into micro-batches (configurable size, default 8 on GPU, 1 on CPU)
3. For each micro-batch:
   - Skip hashes with unsupported extensions
   - Skip hashes already in the bucket
   - Download batch file bytes from Hydrus (with retry logic)
   - Generate CLIP embeddings for the batch
   - Store all embeddings in the database bucket
   - Update the queue item's progress bar, percentage, and ETA
4. On completion: emit `buckets_changed`, advance queue to next job
5. On error for individual files: retry up to the configured retry count, then skip and continue with remaining files
6. On fatal error: report to user, discard the failed job, advance queue

The ingest worker can be cancelled mid-job; already-embedded hashes remain in the bucket.

### 6.4 History Tab

**Purpose:** Browse previously triaged images by bucket and operation type.

**Layout:** Horizontal split using `QSplitter` — sidebar on the left (resizable), thumbnail grid on the right.

#### Sidebar Controls

```
┌─ FILTER ─────────────────────┐
│  Bucket: [▼_______________]  │
│                               │
│  [ Archived ] [ Deleted  ]    │   ← segmented button bar
│  [ Skipped  ] [ Deferred ]    │
│                               │
│  2,341 matching entries       │   ← live count preview
│                               │
│  Date from: [2024-01-01  ]    │
│  Date to:   [2024-12-31  ]   │
│  Hash filter: [__________]    │
│                               │
│  Thumbnail size: [300 px ]    │
│  Limit: [100           ]      │
│                               │
│  [        Browse         ]    │
│  [      Export CSV       ]    │
└───────────────────────────────┘
```

**Operation selector:** A segmented button bar (not radio buttons). Four `QPushButton`s in a 2×2 grid, styled so the selected button has the accent background. More compact and more clickable than vertical radio buttons.

**Live count preview:** As the user changes the bucket or operation, a lightweight count query runs (background thread) and displays `"N matching entries"` below the selector. This lets users gauge data volume before browsing.

**Date range filter:** Compact `QDateEdit` widgets (From/To) with a small "Clear" link button to reset.

**Hash filter:** `QLineEdit` for filtering by partial hash string prefix.

**Thumbnail size:** Same `QSpinBox` as Search tab, synchronized from config.

**Limit:** `QSpinBox` 1–10000, default 100.

**Browse button** (renamed from "Search History" — this browses past operations, not CLIP searches).

**Export CSV button:** Exports current query results to a CSV file (fields: hash, bucket, operation, timestamp).

#### Grid Behavior

- Same `ThumbnailGrid` as the Search tab with all visual polish (loading skeletons, selection style, rounded corners, grid gaps, keyboard navigation, hover preview)
- Tooltips show `"hash\ntimestamp: YYYY-MM-DD HH:MM"` for history entries
- Context menu (cached and updated on `aboutToShow`):
  - **Search using this image** — emits `search_with_image(hash_)`, which the MainWindow catches to switch to the Search tab and run a search
  - **Re-ingest into bucket** — prompts for a target bucket and re-embeds the hash into that bucket (useful for recovering accidentally-skipped images)
  - **Remove from trash** (for Deleted entries) — calls Hydrus `undelete_files` API to restore the file from trash
  - *(separator)*
  - **Open externally** — opens the file with the system default application (if local path available)
  - **Copy file path** — copies the local filesystem path to clipboard (if available)
  - **Open containing folder** — opens the file's parent folder in the file manager
- No triage operations (archive/delete/skip/defer) are available in the history tab
- Delete key does not trigger triage operations in the history tab

---

## 7. Threading Model

### 7.1 Worker Threads

All workers extend `QThread` directly (not `QObject` + `moveToThread`).

| Worker | Purpose | Key Signals |
|--------|---------|-------------|
| `IngestWorker` | Batch-download, embed, and store images | `progress(int, int, str)`, `log(str)`, `finished_ok(int)`, `failed(str)` |
| `SearchWorker` | Perform vector search (with optional text refinement) | `results(list)`, `incremental_results(list)`, `random_results(list)`, `failed(str)` |
| `ThumbnailLoader` | Batch-fetch thumbnails for display (with disk cache) | `loaded(str, bytes)`, `finished_all()` |
| `ModelLoadWorker` | Load CLIP model on background thread (with download progress) | `download_progress(int, int)`, `loaded(str, str)`, `failed(str)` |
| `HydrusCheckWorker` | Verify Hydrus API/settings | `ok(str)`, `failed(str)` |
| `HydrusOperationWorker` | Execute triage operations via Hydrus API (with retry) | `done(int, list)`, `failed(int, list, str)` |
| `DedupWorker` | Run deduplication scans on a bucket | `results(list)`, `failed(str)` |

### 7.2 Worker Lifecycle

1. Worker is instantiated with references to shared services (DB, CLIP, Hydrus)
2. Signals are connected; old signal connections from prior workers are explicitly disconnected before new ones are created
3. `worker.start()` begins background work
4. On `finished`, the worker reference is set to `None` and `deleteLater()` is called
5. **Cancellation:** Setting `_cancel = True` (checked at strategic points in `run()`) causes early termination. The flag uses `threading.Lock` or `QMutex` for proper synchronization.
6. **Retirement pattern:** When replacing a running worker, its signals are disconnected, it's cancelled, added to a `_retiring` list, and the `finished` signal (not a blocking `wait()`) triggers removal from the list and `deleteLater()`

### 7.3 Thread Safety Notes

- Database: `threading.RLock` on all operations
- CLIP Model: `threading.Lock` on `embed_bytes`, `embed_text`, `load`, `unload`
- `_cancel` flag: protected by `threading.Lock` for safe cross-thread access

---

## 8. Triage Operations Reference

| Code | Name | Hydrus API Call | Database Effect | UI Effect |
|------|------|----------------|-----------------|-----------|
| 0 | Delete | `delete_files(hashes)` | Remove hash from bucket, record in history | Remove thumbnail from grid |
| 1 | Archive | `archive_files(hashes)` | Remove hash from bucket, record in history | Remove thumbnail from grid |
| 2 | Skip | *(none)* | Remove hash from bucket, record in history | Remove thumbnail from grid |
| 3 | Defer | `add_tags(hashes, service_key, ["hyclip:defer"])` | Remove hash from bucket, record in history | Remove thumbnail from grid |

All four operations permanently record the (hash, bucket, operation, timestamp) tuple in the `history` table. There is no "undo" feature.

The "Remove from trash" action (available in History tab for Deleted entries) calls `undelete_files` to restore the file in Hydrus but does **not** re-add the hash to any bucket — use "Re-ingest into bucket" for that.

If the Defer operation's tag service key is not configured, the operation fails with an error before any API call is made.

---

## 9. File Type Support

Only these file extensions are eligible for CLIP embedding:
- `png`
- `jpg`, `jpeg`
- `webp`
- `tiff`, `tif`

Other file types in Hydrus are silently skipped during ingest (reported in the progress log as "skipped ... (unsupported ext)").

---

## 10. Dependencies

| Package           | Version | Purpose                          |
| -------------------| ---------| ----------------------------------|
| `PySide6`         | 6.11.1  | Qt GUI framework                 |
| `open_clip_torch` | 3.3.0   | CLIP model loading and embedding |
| `hydrus-api`      | 5.3.0   | Hydrus Client API client         |
| `sqliteai-vector` | 1.0.0   | SQLite vector search extension   |
| `transformers`    | 5.12.1  | Required by open_clip            |

Python 3.12+ required.

---

## 11. Startup Sequence

1. `QApplication` created
2. Check for `hyclip_sifter.ini` in CWD
   - If missing: show first-run dialog → write config file on accept, exit on cancel
3. Instantiate `Config` (reads/backfills ini)
4. Instantiate `Database` (opens SQLite with WAL mode, loads sqlite-vector extension, creates base tables, initializes existing vector indices, runs migration for any new columns)
5. Instantiate `ClipModel` (does not load model yet)
6. Instantiate `HydrusService` (creates API client with stored credentials)
7. Create `MainWindow`:
   - Restore window geometry from `QSettings` if available, otherwise size to 75% of `screen().availableGeometry()`
   - Set title, create menu bar, toolbar, and styled status bar with persistent model/connection indicators
   - Create three tabs in workflow order (Ingest, Search, History)
   - Apply QSS stylesheet if configured
   - Wire cross-tab signals
   - Refresh all tab bucket lists
   - If no model is loaded and no buckets exist: default to Ingest tab and show first-launch wizard banner
   - If `load_on_startup`: trigger model load

---

## 12. Shutdown Sequence

1. Collect all worker references from all tabs (by attribute name)
2. Cancel each worker (with proper lock on cancel flag)
3. Wait up to 2 seconds per worker (non-blocking: use `finished` signal with a timeout gate)
4. Call `deleteLater()` on each
5. Verify no worker threads are still accessing the database
6. Unload CLIP model (free CUDA memory)
7. Close database connection
8. Save window geometry to `QSettings`

---

## 13. Data Flow Summary

```
User pastes hashes
       │
       ▼
IngestWorker ──► Hydrus API (get file bytes, with retry)
       │
       ▼
ClipModel.embed_bytes_batch() ──► float[][] embeddings
       │
       ▼
Database.add_embedding(bucket, hash, embedding) × N
       │
       ▼
sqlite-vector stores FLOAT32 blobs + deferred background quantization
```

```
User selects query image + optional text
       │
       ▼
SearchWorker ──► get_embedding(query_hash) OR embed_bytes(query_hash)
       │         embed_text(text_query) × multiplier
       │         subtract negative_image_embedding (if set)
       │         combine element-wise (if both present)
       ▼
Database.nearest_neighbors(bucket, combined_blob, k)
       │
       ▼
ThumbnailGrid.set_hashes(results) ──► ThumbnailLoader fetches thumbnails (disk cache → Hydrus API fallback)
```

```
User selects images + clicks triage button (or keyboard shortcut)
       │
       ▼
HydrusOperationWorker ──► Hydrus API (archive/delete/add_tags, with retry)
       │
       ▼
Database.remove_from_bucket(bucket, hash, operation) — recorded with timestamp
       │
       ▼
ThumbnailGrid.remove_hash() + history record
```

```
History tab "Remove from trash" action
       │
       ▼
HydrusOperationWorker ──► Hydrus API (undelete_files)
       │
       ▼
Status update only (no bucket or history changes)
```

---

## 14. Error Handling

- **Config/database construction failure:** Fatal — critical message box, app exits
- **CLIP model not loaded:** Operation blocked with information dialog
- **CLIP model load failure:** Error dialog, model status reverts
- **Hydrus API transient failure:** Automatic retry up to configured count with backoff; failure reported only after all retries exhausted
- **Hydrus API auth/service error:** Immediate error message, no retry
- **Ingest per-file failure:** Retry up to configured count, then skip and continue with remaining files
- **Search failure (no results / embedding error):** Warning dialog, grid cleared
- **Defer with no tag service key:** Warning dialog, operation aborted
- **Queue persistence failure:** Silently ignored (JSON write errors)
- **Database closed during worker:** Prevented by ensuring all workers are cancelled and finished before `close()` is called during shutdown
- **Thumbnail cache disk full:** Graceful fallback to direct Hydrus API fetch, warning logged to status bar

---

## 15. Thumbnail Cache

Thumbnails are cached to a local directory (configurable via `ui.thumbnail_cache_dir`, default `./thumb_cache/`) to avoid repeated Hydrus API requests.

- Cache is keyed by SHA256 hash, stored as `{hash[:2]}/{hash}.jpg` to avoid too many files in one directory
- Before fetching a thumbnail from Hydrus, the cache is checked first
- Cache is append-only (no eviction); the user can clear it manually via a button in the Ingest tab or by deleting the directory
- Cache misses fall through to the Hydrus API, and the result is written to the cache for future use

---

## 16. Stylesheets and Theming

- A built-in theme toggle in the View menu or config switches between dark, light, and system-default palettes
- The `ui.stylesheet` config key (optional) accepts a path to a `.qss` file that is loaded after the built-in theme and can override any widget style
- If the stylesheet file is missing or unreadable at startup, a warning is logged to the status bar and the app continues with the built-in theme


