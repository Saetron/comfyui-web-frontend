# ComfyUI Web Studio

A lightweight, Dockerized web interface in Python (**FastAPI** + **vanilla HTML / Tailwind CSS**) that acts as a custom, responsive frontend for your local ComfyUI instance.

---

## Features

### 1. 🎨 Generator View
- **Template Workflow Injection**: Automatically loads standard ComfyUI API workflow JSON (`workflow_api.json`).
- **Clean Form**:
  - Positive Prompt (`textarea`)
  - Negative Prompt (`textarea`)
  - Seed input with **Randomize toggle** and **🎲 Roll button**
  - Collapsible Advanced Controls (Checkpoint selector, Steps, CFG Scale, Resolution presets & custom sizes, Samplers, and Schedulers).
- **Live Progress & WebSocket Tracking**:
  - Connects to ComfyUI's WebSocket endpoint (`/ws?clientId=...`)
  - Real-time progress bar (percentage & step count), active node reporting, queue status, and timer.
  - Live result preview displayed immediately upon generation completion.
  - Shortcut: Press `Ctrl+Enter` or `Cmd+Enter` to generate from anywhere in the form.

### 2. 🖼️ Persistent Gallery View
- **Direct Volume Mount**: Directly mounts the ComfyUI host `output/` directory at `/app/output` with read/write access.
- **Filesystem-based (No in-memory ComfyUI history dependency)**:
  - Crawls `/app/output` recursively on startup.
  - Watches for new, modified, or deleted files in real-time using `watchfiles` and pushes instant updates via WebSocket to all connected browser tabs.
- **Responsive Grid**:
  - Optimized thumbnails generated on the fly and cached.
  - Search by filename or relative path.
  - Sort by Newest First, Oldest First, or Filename.
- **Embedded Metadata Inspection**:
  - Reads embedded ComfyUI PNG `tEXt` / `iTXt` chunks (`prompt`, `workflow`) and A1111 / WebUI parameters.
  - Displays human-readable parameters: Positive & Negative Prompts, Seed, Steps, CFG, Sampler, Model name, Dimensions, and File size.
  - "Use in Generator" button: One-click to reload previous prompts and settings into the Generator.
  - Copy buttons for prompts and raw JSON.
- **Permanent Deletion**:
  - Permanent "Delete" button inside the image modal with confirmation.
  - Deletes the file and thumbnail directly from the mounted output volume and instantly updates the gallery view.

### 3. ⚙️ Workflow JSON Editor
- View, edit, and save custom ComfyUI API workflow JSON templates directly in the web UI.
- Reset to the default template at any time.

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
├── output/                      # Mounted to ComfyUI's output directory (./output -> /app/output)
├── appdata/                     # Persistent storage for custom workflow templates and thumbnails
└── app/
    ├── __init__.py
    ├── config.py                # Environment and path configurations
    ├── comfy_service.py         # ComfyUI API client, workflow injector, and WebSocket listener
    ├── gallery_service.py       # Filesystem scanner, directory watcher, and thumbnail manager
    ├── metadata_parser.py       # ComfyUI PNG chunk & EXIF metadata parser
    ├── main.py                  # FastAPI app, REST endpoints, and WebSocket connection manager
    ├── templates/
    │   └── default_workflow_api.json # Baseline ComfyUI API workflow template
    └── static/
        ├── index.html           # Single-page interface styled with Tailwind CSS
        ├── app.js               # Frontend controller & WebSocket client
        └── style.css            # Custom scrollbars and styling
```

---

## Quick Start with Docker Compose

### 1. Ensure ComfyUI is Running
Start your local ComfyUI instance (default port `8188`).

> [!IMPORTANT]
> If running ComfyUI on Windows/macOS/Linux, ensure ComfyUI listens on all interfaces if necessary (`--listen 0.0.0.0` or default `127.0.0.1`), or use `http://host.docker.internal:8188`.

### 2. Point to Your ComfyUI Output Folder
In `docker-compose.yml`, mount your existing ComfyUI `output/` directory into `./output`:

```yaml
volumes:
  - /path/to/your/comfyui/output:/app/output:rw
  - ./appdata:/app/data
```
*(Or keep `./output:/app/output:rw` and point ComfyUI to save in this project's `output/` folder).*

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

You can configure the frontend using environment variables or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `COMFYUI_URL` | `http://host.docker.internal:8188` | Address of your ComfyUI backend instance |
| `PORT` | `8080` | Internal and exposed web server port |
| `OUTPUT_DIR` | `/app/output` | Path to mounted ComfyUI output directory |
| `DATA_DIR` | `/app/data` | Path to persistent storage for templates and thumbnails |
| `HOST` | `0.0.0.0` | Bind host address |

### Connecting across Operating Systems
- **Windows / macOS (Docker Desktop)**: `http://host.docker.internal:8188` works out of the box.
- **Linux**: The `docker-compose.yml` includes `extra_hosts: ["host.docker.internal:host-gateway"]`, allowing Linux Docker containers to resolve `host.docker.internal` to the host machine.
- **Remote / LAN machine**: Set `COMFYUI_URL=http://192.168.1.100:8188` (replace with your server's LAN IP).

---

## How It Works

### Workflow Injection
When you click **Generate**:
1. The app loads the active template from `/app/data/workflow_api.json` (or `app/templates/default_workflow_api.json`).
2. The injector traces the `KSampler` node to identify:
   - Seed (`seed` or `noise_seed`)
   - Positive prompt link (`inputs.positive` -> `CLIPTextEncode`)
   - Negative prompt link (`inputs.negative` -> `CLIPTextEncode`)
   - Dimensions (`EmptyLatentImage`)
   - Checkpoint model (`CheckpointLoaderSimple`)
3. The modified JSON payload is posted to ComfyUI's `/prompt` endpoint.
4. The backend opens a WebSocket connection to ComfyUI, filters messages by the returned `prompt_id`, and relays real-time step and execution events to the browser.
5. When the `SaveImage` node finishes, the generated image appears in the preview pane, and the directory watcher automatically indexes it into the Gallery.
