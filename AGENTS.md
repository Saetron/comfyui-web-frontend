# AGENTS.md - Developer & Agent Guide for ComfyUI Studio

This document provides a concise architectural guide and technical reference for AI assistants and human developers working on the `comfyui-web-frontend` codebase.

---

## 1. Project Overview

`comfyui-web-frontend` (ComfyUI Studio) is a lightweight, responsive web interface acting as a dedicated frontend for a local or remote ComfyUI instance.

Key Goals:
- **Zero build step frontend**: Vanilla JavaScript + HTML5 styled with Tailwind CSS via CDN.
- **Robust workflow injection**: Injects prompts, seeds, dimensions, and checkpoint parameters into standard ComfyUI API JSON workflows.
- **WebSocket progress tracking**: Streams real-time progress percentages, step counts, and active node status directly to the browser.
- **Persistent Filesystem Gallery**: Does **not** rely on ComfyUI's volatile `/history` endpoint. Crawls the mounted output directory (`/app/output`) on startup, monitors changes in real-time with `watchfiles`, extracts embedded PNG metadata, and provides permanent image deletion.
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
│   ├── comfy_service.py         # ComfyUI API integration, workflow injector & WebSocket listener
│   ├── gallery_service.py       # Filesystem scanner, watchfiles watcher, thumbnailing & deletion
│   ├── metadata_parser.py       # ComfyUI PNG chunk parser (prompt/workflow) & EXIF/A1111 reader
│   ├── templates/
│   │   └── default_workflow_api.json  # Baseline ComfyUI API workflow template
│   └── static/
│       ├── index.html           # Single-page UI with Generator, Gallery & Workflow Editor
│       ├── app.js               # Frontend state, WebSocket client, modal & event handlers
│       └── style.css            # Custom glassmorphism, scrollbars, and transitions
├── output/                      # Mounted output volume (./output:/app/output:rw)
└── appdata/                     # Persistent storage (./appdata:/app/data)
```

---

## 3. Core Architecture & Workflow

### 3.1 Generator & Workflow Injection (`app/comfy_service.py`)
- Reads active template from `/app/data/workflow_api.json` (falls back to `app/templates/default_workflow_api.json`).
- `inject_workflow()`:
  - Inspects nodes for `class_type in ["KSampler", "KSamplerAdvanced"]`.
  - Injects `seed` (or generates random seed if randomize toggle is active).
  - Traces `inputs["positive"]` link to identify the positive `CLIPTextEncode` node and updates `text`.
  - Traces `inputs["negative"]` link to identify the negative `CLIPTextEncode` node and updates `text`.
  - Injects optional parameters: `steps`, `cfg`, `sampler_name`, `scheduler`, `width`, `height`, and `ckpt_name`.
- `queue_prompt()`: Posts `{ "prompt": prompt_graph, "client_id": client_id }` to `{COMFYUI_URL}/prompt`.
- `listen_prompt_progress()`: Connects to ComfyUI's WebSocket at `ws://{host}:{port}/ws?clientId={client_id}` and yields typed events (`status`, `execution_start`, `executing`, `progress`, `images_ready`, `complete`, `error`).

### 3.2 Persistent Gallery (`app/gallery_service.py` & `app/metadata_parser.py`)
- **Initialization**: On application startup (`lifespan` in `app/main.py`), `gallery_service.scan()` crawls `OUTPUT_DIR` recursively.
- **Watcher**: Asynchronous background task running `watchfiles.awatch(OUTPUT_DIR)` detects newly written images and broadcasts `gallery_item_added` or `gallery_item_deleted` to all active WebSocket clients.
- **Metadata Parser**: Opens image using `PIL.Image` and extracts:
  - `prompt` chunk (ComfyUI API graph) -> parses positive prompt, negative prompt, seed, steps, sampler, model.
  - `workflow` chunk (ComfyUI UI node graph).
  - `parameters` chunk (Automatic1111 / WebUI compatibility).
- **Security**: All file resolution is guarded by `safe_resolve()` to prevent directory traversal outside `OUTPUT_DIR`.
- **Thumbnails**: Cached on demand in `DATA_DIR/thumbnails` resized with Lanczos filter to 400x400 WebP for instant grid rendering.

---

## 4. API Endpoints Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the single-page application |
| `GET` | `/api/status` | ComfyUI connectivity status and gallery image count |
| `GET` | `/api/models` | Available checkpoint models reported by ComfyUI |
| `GET` | `/api/workflow` | Get current ComfyUI API workflow JSON template |
| `POST` | `/api/workflow` | Update workflow JSON template |
| `POST` | `/api/workflow/reset` | Reset workflow template to baseline default |
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
3. **ComfyUI Cross-Platform Networking**:
   - `host.docker.internal` is mapped via `extra_hosts` in `docker-compose.yml` for Linux compatibility.
   - Default environment variable is `COMFYUI_URL=http://host.docker.internal:8188`.
4. **Preserve Comments & Docstrings**: Follow repository convention to preserve docstrings and clean type hints.
