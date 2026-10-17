import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent

# ComfyUI Host URL
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://host.docker.internal:8188").rstrip("/")

# In Docker, mounted at /app/output and /app/data. In local dev, falls back to ./output and ./appdata
is_in_docker = Path("/.dockerenv").exists() or (os.path.exists("/app") and os.name != "nt")

_default_output = "/app/output" if is_in_docker else str(BASE_DIR / "output")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", _default_output)).resolve()

_default_data = "/app/data" if is_in_docker else str(BASE_DIR / "appdata")
DATA_DIR = Path(os.environ.get("DATA_DIR", _default_data)).resolve()

# Thumbnail directory inside DATA_DIR
THUMBNAILS_DIR = DATA_DIR / "thumbnails"

# Template workflow path
WORKFLOW_FILE = DATA_DIR / "workflow_api.json"
DEFAULT_WORKFLOW_TEMPLATE = BASE_DIR / "app" / "templates" / "default_workflow_api.json"

# Server configuration
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

# Default Negative Prompt (configurable via environment variable)
DEFAULT_NEGATIVE_PROMPT = os.environ.get(
    "DEFAULT_NEGATIVE_PROMPT",
    "worst quality, low quality, bad anatomy, bad hands, missing fingers, extra digits, signature, watermark, blurry, deformed",
)

# Workflows and Tags directories
WORKFLOWS_DIR = DATA_DIR / "workflows"
DEFAULT_TAGS_FILE = BASE_DIR / "app" / "data" / "tags" / "danbooru.csv"
USER_TAGS_DIR = DATA_DIR / "tags"

# Ensure essential directories exist
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
USER_TAGS_DIR.mkdir(parents=True, exist_ok=True)
WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
