# ComfyUI Web Studio

[![Build and Publish Docker Image](https://github.com/Saetron/comfyui-web-frontend/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Saetron/comfyui-web-frontend/actions/workflows/docker-publish.yml)
[![Docker Image](https://img.shields.io/badge/ghcr.io-Saetron%2Fcomfyui--web--frontend-blue?logo=docker)](https://github.com/Saetron/comfyui-web-frontend/pkgs/container/comfyui-web-frontend)

A lightweight, Dockerized web interface in Python (**FastAPI** + **vanilla HTML / Tailwind CSS**) that acts as a custom, responsive frontend for your local ComfyUI instance.

---

## Features

### 1. 🌸 Playful Anime Aesthetic & Danbooru Tagging
- **Playful Anime Design (スタジオ)**:
  - Vibrant cosmic midnight background with soft sakura pink and neon lavender glowing cards.
  - Playful kaomoji decals `(✿◠‿◠)`, bouncy buttons, cute Japanese subtitles, and custom candy sliders.
- **Danbooru Style Autocomplete**:
  - Embedded **140,000+ tag dictionary** with live frequency counts and alias support.
  - Autocomplete dropdown pops up as you type in positive or negative prompt textareas.
  - **Category Color-Coded Badges**: General (blue), Character (green), Copyright (purple), Artist (red), Meta (amber).
  - Keyboard navigation (`ArrowUp`/`ArrowDown`, `Enter`/`Tab` to accept, `Esc` to close).
- **Tag Weighting Shortcuts**:
  - Press `Ctrl+Up` / `Ctrl+Down` (or `Cmd+Up` / `Cmd+Down`) to adjust tag weight: `(tag:1.1)` ⇄ `(tag:1.2)`.
- **Quick Tag Palettes & Format Tools**:
  - One-click tag drawers for Quality, Subject, Style, Lighting, and Negative staples.
  - Utilities: `space ➔ _`, `_ ➔ space`, and `🧹 Clean & Deduplicate`.
- **Configurable Standard Negative Prompt**:
  - Configure your favorite baseline negative prompt via the `DEFAULT_NEGATIVE_PROMPT` environment variable.
- **Drag-and-Drop & Server Workflow Management**:
  - **Instant Import**: Drag & drop any ComfyUI `.json` workflow or generated `.png` image directly onto the browser to extract and activate the workflow.
  - Seamlessly discovers workflows stored on your **ComfyUI Server** (`/userdata`, `/v2/userdata`, and PySSSSS `/pysssss/workflows`), or local workflows in `appdata/workflows/`.
- **Live Progress & WebSocket Tracking**:
  - Real-time percentage, step counter, active node status, and queue depth.
  - Live result preview displayed immediately upon generation completion (`Ctrl+Enter` to generate).

### 2. 🖼️ Persistent Gallery, TikTok Mode & Detailed Viewer
- **Filesystem-based (No in-memory ComfyUI history dependency)**:
  - Crawls `/app/output` recursively on startup.
  - Monitors changes in real-time with `watchfiles` and pushes instant updates via WebSocket.
- **📱 TikTok Style Mode (Random)**:
  - Fullscreen randomized vertical slideshow viewer.
  - Navigate with mouse wheel, swipe, `Space`, or `ArrowDown` / `ArrowUp`.
  - Glass caption overlay displaying prompt (with clickable tag pills), seed, model, and dimensions.
  - Floating action sidebar: Next Random (🎲), Copy Prompt, Use in Generator, and Auto-Play toggle (5s timer).
- **Detailed Image Modal**:
  - High-resolution viewer with **`← Prev` and `Next →` navigation buttons** (and keyboard `ArrowLeft` / `ArrowRight` support).
  - Embedded PNG generation metadata extraction (`prompt`, `workflow`, and A1111 parameters).
  - Interactive clickable prompt tag pills.
- **Internal Delete Confirmation**:
  - In-app styled confirmation modal replacing native browser alerts.
  - Permanently removes images and cached thumbnails from the mounted volume.

### 3. ⚙️ ComfyUI Server & Local Workflow Manager
- Discover and load workflows directly from ComfyUI server user data (`/userdata?dir=workflows/`).
- Save custom workflows locally (`appdata/workflows/`) or upload them directly to the ComfyUI server.

---

## Project Structure

```
Comfy/
├── Dockerfile                   # Python 3.11-slim container definition
├── docker-compose.yml           # Compose configuration exposing port 8080 and volume mounts
├── requirements.txt             # FastAPI, uvicorn, websockets, httpx, pillow, watchfiles
├── .dockerignore
├── .env.example
├── README.md
├── AGENTS.md                    # Developer and architecture guide
├── output/                      # Mounted to ComfyUI's output directory (./output -> /app/output)
├── appdata/                     # Persistent storage for custom workflows, tags, and thumbnails
└── app/
    ├── __init__.py
    ├── config.py                # Environment and path configurations
    ├── comfy_service.py         # ComfyUI API client, workflow injector, server workflow manager
    ├── gallery_service.py       # Filesystem scanner, directory watcher, and thumbnail manager
    ├── metadata_parser.py       # ComfyUI PNG chunk & EXIF metadata parser
    ├── tag_service.py           # Danbooru tag search engine with 140k dataset
    ├── main.py                  # FastAPI app, REST endpoints, and WebSocket connection manager
    ├── data/
    │   └── tags/danbooru.csv    # 140,000+ Danbooru tags dataset
    ├── templates/
    │   └── default_workflow_api.json # Baseline ComfyUI API workflow template
    └── static/
        ├── index.html           # Single-page interface styled with Tailwind CSS
        ├── app.js               # Frontend controller, tag autocomplete, TikTok mode & WebSocket client
        └── style.css            # Custom styling, tag category badges, and animations
```

---

## Quick Start with Docker Compose

### 1. Ensure ComfyUI is Running
Start your local ComfyUI instance (default port `8188`).

### 2. Point to Your ComfyUI Output Folder
In `docker-compose.yml`, mount your existing ComfyUI `output/` directory into `./output`:

```yaml
volumes:
  - /path/to/your/comfyui/output:/app/output:rw
  - ./appdata:/app/data
```

### 3. Launch the Container
Run:
```bash
docker compose up -d --build
```

### 4. Access the Web Interface
Open your browser at:
```
http://localhost:8080
```

---

## Configuration & Environment Variables

| Variable | Default | Description |
|---|---|---|
| `COMFYUI_URL` | `http://host.docker.internal:8188` | Address of your ComfyUI backend instance |
| `DEFAULT_NEGATIVE_PROMPT` | `worst quality, low quality, bad anatomy...` | Default baseline negative prompt text |
| `PORT` | `8080` | Internal and exposed web server port |
| `OUTPUT_DIR` | `/app/output` | Path to mounted ComfyUI output directory |
| `DATA_DIR` | `/app/data` | Path to persistent storage for templates and thumbnails |
| `HOST` | `0.0.0.0` | Bind host address |
