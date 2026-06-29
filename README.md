> Shitty porn would be so much easier to get rid of if it wasn't so mixed in with all the good stuff
- Albert Einsetin

Digging through large image scrapes tends to produce a lot of garbage mixed in with the ones you want with no good way of singling them out and mass-deleting them. CLIP is good at finding similar images, so now you can find the bad stuff, find everything similar to it, and nuke it.  

This was vibe coded in about 2 hours; one to write the spec, another to bugfix and make tweaks. Model used was GLM 5.2. Caveat emptor. 

Anyways, here's the README it wrote for me.

# HyCLIP Sifter

A Qt-based desktop tool for sifting through images in a [Hydrus Network](https://hydrusnetwork.github.io/hydrus/) instance using CLIP embeddings and nearest-neighbor vector search.

## Overview

HyCLIP Sifter pulls images from Hydrus via its Client API, generates CLIP embeddings using [open_clip](https://github.com/mlfoundations/open_clip), stores them in a local SQLite database with the [sqlite-vector](https://github.com/sqliteai/sqlite-vector) extension, and lets you search and triage images by visual similarity.

## Features

- **Ingest** — Create buckets of images, paste SHA256 hashes, and embed them in the background while you continue using the app
- **Search** — Find nearest neighbors by example image, or pull a random sample from a bucket
- **Triage** — Archive, delete, skip, or defer images; operations are applied via the Hydrus API and recorded in a history table
- **History** — Browse previously triaged images filtered by bucket and operation

## Setup

### Prerequisites

- Python 3.12+
- A running Hydrus Network instance with the Client API enabled

### Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Edit `hyclip_sifter.ini`:

```ini
[hydrus]
api_url = http://127.0.0.1:45869
api_key = your-api-key-here
tag_service_key = your-tag-service-key
rating_service_key = your-rating-service-key

[clip]
model = ViT-B-16-SigLIP2
```

The API key needs at least the following permissions in Hydrus:
- Search Files
- Manage Database (for file paths)

The tag service key is required for the Defer operation (adds the `hyclip:defer` tag). Service keys can be found in Hydrus under *services > review services*.

## Usage

```bash
python main.py
```

### Workflow

1. **Ingest tab** — Load the CLIP model, create a bucket, paste hashes, and start ingesting
2. **Search tab** — Select a bucket, search by example image or random sample, then triage results with archive/skip/defer/delete
3. **History tab** — Review past operations by bucket and operation type

### Operations

| Operation | Effect | History Code |
|-----------|--------|--------------|
| Archive   | Archives the file in Hydrus | 1 |
| Skip      | Removes from bucket only | 2 |
| Defer     | Adds `hyclip:defer` tag and removes from bucket | 3 |
| Delete    | Sends to trash in Hydrus | 0 |

## Dependencies

- [PySide6](https://www.qt.io/) — Qt UI framework
- [open_clip](https://github.com/mlfoundations/open_clip) — CLIP model loading and image embeddings
- [sqliteai-vector](https://github.com/sqliteai/sqlite-vector) — SQLite vector search extension
- [hydrus-api](https://gitlab.com/cryzed/hydrus-api) — Python client for the Hydrus Client API
