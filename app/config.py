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

# Candidate search directories for workflows (supports dropping in config/ or data/)
CONFIG_DIR = Path("/app/config") if is_in_docker else (BASE_DIR / "config")
DATA_EXTRA_DIR = Path("/app/data_extra") if is_in_docker else (BASE_DIR / "data")

_raw_search_dirs = [
    WORKFLOWS_DIR,
    DATA_DIR,
    CONFIG_DIR / "workflows",
    CONFIG_DIR,
    DATA_EXTRA_DIR / "workflows",
    DATA_EXTRA_DIR,
    BASE_DIR / "config",
    BASE_DIR / "data",
    BASE_DIR / "appdata",
]

# Deduplicate search directories while preserving priority order
WORKFLOW_SEARCH_DIRS = list(dict.fromkeys(d.resolve() for d in _raw_search_dirs))

# Ensure essential directories exist
for _dir in (OUTPUT_DIR, DATA_DIR, THUMBNAILS_DIR, USER_TAGS_DIR, WORKFLOWS_DIR, CONFIG_DIR, DATA_EXTRA_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

