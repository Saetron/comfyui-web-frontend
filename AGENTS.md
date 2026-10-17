# AGENTS.md - Developer & Agent Guide for ComfyUI Studio

This document provides a concise architectural guide and technical reference for AI assistants and human developers working on the `comfyui-web-frontend` codebase.

---

## 1. Project Overview

`comfyui-web-frontend` (ComfyUI Studio) is a lightweight, responsive web interface acting as a dedicated frontend for a local or remote ComfyUI instance.

Key Goals:
- **Zero build step frontend**: Vanilla JavaScript + HTML5 styled with Tailwind CSS via CDN.
- **Danbooru style tagging**: Sub-millisecond indexed typeahead autocomplete with ~140,000 Danbooru tags, category badges (general, character, copyright, artist, meta), post counts, weighting shortcuts (`Ctrl+Up`/`Ctrl+Down`), quick tag palettes, and prompt formatting utilities.
- **ComfyUI Server Workflows**: Seamlessly discovers, loads, and activates workflows directly from ComfyUI server storage (`/userdata?dir=workflows/`) or local files (`appdata/workflows/`).
- **Configurable Defaults**: Default negative prompt configured via environment variable `DEFAULT_NEGATIVE_PROMPT`.
- **WebSocket progress tracking**: Streams real-time progress percentages, step counts, and active node status directly to the browser.
- **Persistent Filesystem Gallery**: Does **not** rely on ComfyUI's volatile `/history` endpoint. Crawls mounted output directory (`/app/output`) on startup, monitors changes in real-time with `watchfiles`, extracts embedded PNG metadata, and provides permanent image deletion.
- **Gallery TikTok Mode & Modal Navigation**: Fullscreen randomized vertical slideshow viewer ("TikTok mode") with auto-play, plus sequential `← Prev` / `Next →` navigation in detailed image viewer.
- **Internal Confirmation Modals**: In-app styled confirmation dialogs replacing browser `alert()`/`confirm()`.
- **Containerized deployment**: Multi-arch Docker image published to GitHub Container Registry (`ghcr.io/saetron/comfyui-web-frontend`).

---

## 2. Directory Structure & File Map

```
.
├── Dockerfile                   # Python 3.11-slim container with healthcheck
├── docker-compose.yml           # Compose setup with volume mounts & port 8080
├── requirements.txt             # Python dependencies (FastAPI, uvicorn, httpx, websockets, pillow, watchfiles)
├── .github/workflows/
│   └── docker-publish.yml       # Multi-arch (amd64/arm64) Docker build & GHCR publish action
├── app/
│   ├── __init__.py
│   ├── config.py                # Environment variable resolution & directory paths
│   ├── main.py                  # FastAPI server, REST routes, WebSocket manager & app lifespan
│   ├── comfy_service.py         # ComfyUI API integration, workflow injector, server workflow manager & WS listener
│   ├── gallery_service.py       # Filesystem scanner, watchfiles watcher, thumbnailing & deletion
│   ├── metadata_parser.py       # ComfyUI PNG chunk parser (prompt/workflow) & EXIF/A1111 reader
│   ├── tag_service.py           # Danbooru tag indexer, ranked search, alias matching & categories
│   ├── data/
│   │   └── tags/
│   │       └── danbooru.csv     # Canonical 140k Danbooru tags dataset with categories and counts
│   ├── templates/
│   │   └── default_workflow_api.json  # Baseline ComfyUI API workflow template
│   └── static/
│       ├── index.html           # Single-page UI with Generator, Gallery, TikTok mode, Tag helper & Editor
│       ├── app.js               # Frontend state, WebSocket client, autocomplete, modal & event handlers
│       └── style.css            # Custom glassmorphism, tag category pills, TikTok transitions, scrollbars
├── output/                      # Mounted output volume (./output:/app/output:rw)
└── appdata/                     # Persistent storage (./appdata:/app/data)
    ├── workflows/               # Local user workflow templates
    ├── tags/                    # Custom user tag CSV overrides
    └── thumbnails/              # Cached WebP thumbnails
```

---

## 3. Core Architecture & Workflow

### 3.1 Generator & Workflow Injection (`app/comfy_service.py`)
- Reads active template from `/app/data/workflow_api.json` (falls back to `app/templates/default_workflow_api.json`).
- Discovers workflows from:
  - Default baseline
  - ComfyUI server (`GET {COMFYUI_URL}/userdata?dir=workflows/`)
  - Local disk (`appdata/workflows/*.json`)
- `inject_workflow()`:
  - Inspects nodes for `class_type in ["KSampler", "KSamplerAdvanced"]`.
  - Injects `seed` (or generates random seed if randomize toggle is active).
  - Traces `inputs["positive"]` link to identify positive `CLIPTextEncode` node and updates `text`.
  - Traces `inputs["negative"]` link to identify negative `CLIPTextEncode` node and updates `text`.
  - Injects optional parameters: `steps`, `cfg`, `sampler_name`, `scheduler`, `width`, `height`, and `ckpt_name`.
- `queue_prompt()`: Posts `{ "prompt": prompt_graph, "client_id": client_id }` to `{COMFYUI_URL}/prompt`.
- `listen_prompt_progress()`: Connects to ComfyUI's WebSocket at `ws://{host}:{port}/ws?clientId={client_id}` and yields typed events (`status`, `execution_start`, `executing`, `progress`, `images_ready`, `complete`, `error`).

### 3.2 Danbooru Tagging Engine (`app/tag_service.py`)
- Loaded on startup into an in-memory prefix index:
  - 140,000+ Danbooru tags with post frequencies and aliases.
  - Multi-tier ranking: exact matches > exact prefix matches > word-boundary matches > substrings > aliases. Tie-breaker by post count descending.
  - Category mapping:
    - `0`: General (`text-sky-400 bg-sky-950`)
    - `1`: Artist (`text-rose-400 bg-rose-950`)
    - `3`: Copyright / Series (`text-purple-400 bg-purple-950`)
    - `4`: Character (`text-emerald-400 bg-emerald-950`)
    - `5`: Meta (`text-amber-400 bg-amber-950`)
- Frontend typeahead autocomplete attached to `#positive-prompt` and `#negative-prompt`.
- Weighting adjustment via `Ctrl+Up` / `Ctrl+Down` (`(tag:1.1)` increments / decrements).
- Quick tag pill palettes (Quality, Subject, Style, Lighting, Negative).

### 3.3 Persistent Gallery & TikTok Mode (`app/gallery_service.py`)
- **Initialization**: Crawls `OUTPUT_DIR` recursively on startup.
- **Watcher**: Asynchronous background task running `watchfiles.awatch(OUTPUT_DIR)` broadcasts `gallery_item_added` or `gallery_item_deleted` to active clients.
- **Safe Path Resolution**: `safe_resolve()` strips any accidental `/app/output/` or `OUTPUT_DIR` absolute prefixes, preventing 500 deletion errors while protecting against directory traversal.
- **Detailed Viewer**: Full image inspection with sequential `← Prev` and `Next →` navigation buttons and keyboard arrow support.
- **TikTok Mode**: Randomized fullscreen slideshow with vertical snap scroll/wheel navigation, auto-play timer (5s), and action bar.
- **Internal Modal**: Replaces browser `confirm()` with a themed confirmation modal.

---

## 4. API Endpoints Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the single-page application |
| `GET` | `/api/status` | ComfyUI connectivity, default negative prompt, and image counts |
| `GET` | `/api/config` | System defaults (`default_negative_prompt`, `comfyui_url`) |
| `GET` | `/api/tags` | Search Danbooru tags (`q`, `category`, `limit`) |
| `GET` | `/api/tags/top` | Preload top Danbooru tags for browser cache |
| `GET` | `/api/models` | Available checkpoint models reported by ComfyUI |
| `GET` | `/api/workflow` | Get active ComfyUI API workflow JSON template and ID |
| `POST` | `/api/workflow` | Update active workflow JSON template |
| `POST` | `/api/workflow/reset` | Reset workflow template to baseline default |
| `GET` | `/api/comfy/workflows` | List workflows from ComfyUI server, local disk, and default |
| `GET` | `/api/comfy/workflows/{id}` | Fetch specific workflow JSON content |
| `POST` | `/api/comfy/workflows/activate` | Activate a specific workflow for generation |
| `POST` | `/api/comfy/workflows/save` | Save workflow to ComfyUI server or local storage |
| `POST` | `/api/generate` | Validate form inputs, inject nodes, queue prompt to ComfyUI |
| `POST` | `/api/interrupt` | Send interrupt signal to ComfyUI |
| `GET` | `/api/images` | List gallery images (supports `query`, `sort`, `limit`, `offset`) |
| `GET` | `/api/images/{file_path}` | Stream full-resolution image with HTTP caching |
| `GET` | `/api/thumbnails/{file_path}` | Stream generated thumbnail |
| `GET` | `/api/images/{file_path}/metadata` | Return parsed & raw embedded generation metadata |
| `DELETE` | `/api/images/{file_path}` | Permanently delete image and thumbnail from disk |
| `WS` | `/ws/{client_id}` | WebSocket for real-time progress and live gallery updates |

---

## 5. Development Guidelines

1. **Keep Frontend Lightweight**: Do not introduce heavy frontend toolchains or bundlers (Webpack, Vite, npm) unless requested. Vanilla JS with Tailwind CSS CDN keeps deployment zero-build and instantly hackable.
2. **Preserve Path Sandboxing**: Always use `gallery_service.safe_resolve(relative_path)` when reading or deleting files from the filesystem.
3. **Environment Defaults**:
   - `COMFYUI_URL=http://host.docker.internal:8188`
   - `DEFAULT_NEGATIVE_PROMPT` sets default negative prompt text across the application.
4. **Preserve Comments & Docstrings**: Follow repository convention to preserve docstrings and clean type hints.
