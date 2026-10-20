import asyncio
import json
import logging
import random
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import (
    COMFYUI_URL,
    DATA_DIR,
    DEFAULT_NEGATIVE_PROMPT,
    HOST,
    OUTPUT_DIR,
    PORT,
    THUMBNAILS_DIR,
)
from app.comfy_service import comfy_service
from app.gallery_service import gallery_service
from app.metadata_parser import parse_comfyui_prompt_graph
from app.tag_service import tag_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")


# Pydantic models for API
class GenerateRequest(BaseModel):
    positive_prompt: str = Field(..., description="Positive prompt text")
    negative_prompt: str = Field("", description="Negative prompt text")
    seed: Optional[int] = Field(None, description="Seed value")
    randomize_seed: bool = Field(True, description="Whether to randomize seed")
    batch_count: int = Field(1, ge=1, le=100, description="Number of images to queue in batch")
    client_id: str = Field(..., description="Unique client identifier")
    steps: Optional[int] = Field(None, ge=1, le=150)
    cfg: Optional[float] = Field(None, ge=0.0, le=30.0)
    width: Optional[int] = Field(None, ge=64, le=4096)
    height: Optional[int] = Field(None, ge=64, le=4096)
    ckpt_name: Optional[str] = None
    sampler_name: Optional[str] = None
    scheduler: Optional[str] = None


class WorkflowUpdateRequest(BaseModel):
    workflow: Dict[str, Any]


# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        # Maps client_id to WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        # All connected clients for broadcast
        self.all_clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections[client_id] = websocket
            self.all_clients.add(websocket)
        logger.info(f"Client connected: {client_id} (total: {len(self.all_clients)})")

    async def disconnect(self, client_id: str, websocket: WebSocket):
        async with self._lock:
            self.active_connections.pop(client_id, None)
            self.all_clients.discard(websocket)
        logger.info(f"Client disconnected: {client_id}")

    async def send_to_client(self, client_id: str, message: Dict[str, Any]):
        ws = self.active_connections.get(client_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Error sending to client {client_id}: {e}")

    async def broadcast(self, message: Dict[str, Any]):
        dead = []
        for ws in list(self.all_clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self.all_clients.discard(ws)


ws_manager = ConnectionManager()


# Register gallery broadcast with WebSocket manager
gallery_service.register_broadcast_callback(ws_manager.broadcast)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting ComfyUI Web Frontend on {HOST}:{PORT}")
    logger.info(f"Target ComfyUI URL: {COMFYUI_URL}")
    logger.info(f"Mounted Output Directory: {OUTPUT_DIR}")
    logger.info(f"Mounted Data Directory: {DATA_DIR}")

    # Crawl output directory on startup
    await gallery_service.scan()

    # Preload Danbooru tags asynchronously
    asyncio.create_task(asyncio.to_thread(tag_service.load_tags))

    # Start directory watcher in background
    watcher_task = asyncio.create_task(gallery_service.start_watcher())

    yield

    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass
    logger.info("Application shutdown complete.")


app = FastAPI(title="ComfyUI Web Interface", lifespan=lifespan)

# Allow CORS for development convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h3>ComfyUI Web UI loading...</h3>")


@app.get("/api/config")
async def get_config():
    """Get system and UI configuration defaults."""
    return {
        "default_negative_prompt": DEFAULT_NEGATIVE_PROMPT,
        "comfyui_url": COMFYUI_URL,
    }


@app.get("/api/status")
async def get_status():
    """Get status of ComfyUI backend, defaults, and gallery directory stats."""
    comfy_health = await comfy_service.check_health()
    gallery_info = gallery_service.list_images(limit=1)
    return {
        "comfyui": comfy_health,
        "output_dir": str(OUTPUT_DIR),
        "total_images": gallery_info["total"],
        "default_negative_prompt": DEFAULT_NEGATIVE_PROMPT,
    }


@app.get("/api/tags")
async def search_tags(
    q: str = Query("", description="Danbooru tag search query"),
    category: Optional[int] = Query(None, description="Tag category filter: 0=general, 1=artist, 3=copyright, 4=character, 5=meta"),
    limit: int = Query(25, ge=1, le=100),
):
    """Search Danbooru tags with ranking and alias matching."""
    return tag_service.search(query=q, category=category, limit=limit)


@app.get("/api/tags/top")
async def get_top_tags(limit: int = Query(1500, ge=1, le=5000)):
    """Return top most frequent Danbooru tags for browser preloading and instant autocomplete."""
    return tag_service.get_top_tags(limit=limit)


@app.get("/api/models")
async def get_models():
    """Get list of checkpoints installed on ComfyUI."""
    models = await comfy_service.get_checkpoints()
    return {"models": models}


class WorkflowActivateRequest(BaseModel):
    workflow_id: str


class WorkflowSaveRequest(BaseModel):
    filename: str
    workflow: Dict[str, Any]
    destination: str = Field("local", description="'local' or 'server'")


@app.get("/api/workflow")
async def get_workflow():
    """Get active ComfyUI API workflow JSON template and ID."""
    wf = comfy_service.load_workflow_template()
    active_id = comfy_service.get_active_workflow_id()
    parsed_params = parse_comfyui_prompt_graph(wf)
    return {"workflow": wf, "active_id": active_id, "parsed_params": parsed_params}


@app.post("/api/workflow")
async def update_workflow(req: WorkflowUpdateRequest):
    """Save an updated workflow template."""
    comfy_service.save_workflow_template(req.workflow)
    parsed_params = parse_comfyui_prompt_graph(req.workflow)
    return {"status": "saved", "workflow": req.workflow, "parsed_params": parsed_params}


@app.post("/api/workflow/reset")
async def reset_workflow():
    """Reset workflow template to default."""
    wf = comfy_service.reset_workflow_template()
    parsed_params = parse_comfyui_prompt_graph(wf)
    return {"status": "reset", "workflow": wf, "parsed_params": parsed_params}


@app.get("/api/comfy/workflows")
async def list_comfy_workflows():
    """List workflows available on ComfyUI server, local storage, and default template."""
    return await comfy_service.list_available_workflows()


@app.get("/api/comfy/workflows/{workflow_id:path}")
async def get_comfy_workflow(workflow_id: str):
    """Retrieve workflow JSON by id (default, local:<filename>, server:<filename>)."""
    try:
        wf = await comfy_service.fetch_workflow_by_id(workflow_id)
        parsed_params = parse_comfyui_prompt_graph(wf)
        return {"id": workflow_id, "workflow": wf, "parsed_params": parsed_params}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Workflow not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/comfy/workflows/activate")
async def activate_comfy_workflow(req: WorkflowActivateRequest):
    """Activate a specific workflow for generation and editing."""
    try:
        wf = await comfy_service.fetch_workflow_by_id(req.workflow_id)
        comfy_service.save_workflow_template(wf)
        comfy_service.set_active_workflow_id(req.workflow_id)
        parsed_params = parse_comfyui_prompt_graph(wf)
        return {
            "status": "activated",
            "workflow_id": req.workflow_id,
            "workflow": wf,
            "parsed_params": parsed_params,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/comfy/workflows/save")
async def save_comfy_workflow(req: WorkflowSaveRequest):
    """Save workflow to local storage or ComfyUI server."""
    success = await comfy_service.save_workflow(req.filename, req.workflow, destination=req.destination)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save workflow to destination")
    return {"status": "saved", "filename": req.filename, "destination": req.destination}


@app.post("/api/comfy/workflows/upload")
async def upload_workflow(file: UploadFile = File(...)):
    """Upload and activate a workflow from a .json file or a ComfyUI-generated .png file."""
    try:
        content_bytes = await file.read()
        res = comfy_service.import_workflow_file(file.filename or "uploaded_workflow.json", content_bytes)
        # Fetch updated workflow list
        all_workflows = await comfy_service.list_available_workflows()
        parsed_params = parse_comfyui_prompt_graph(res.get("workflow", {}))
        return {
            "status": "success",
            "message": f"Workflow '{res['name']}' imported successfully!",
            "imported": res,
            "workflows": all_workflows,
            "parsed_params": parsed_params,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error uploading workflow: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to import workflow: {e}")


async def _run_generation_tracker(
    browser_client_id: str,
    prompt_client_id: str,
    prompt_id: str,
    resolved_params: Dict[str, Any],
):
    """Background task listening to ComfyUI WebSocket and relaying to browser client."""
    try:
        async for event in comfy_service.listen_prompt_progress(prompt_client_id, prompt_id):
            # Send event to browser client
            await ws_manager.send_to_client(browser_client_id, {
                "channel": "generation",
                "data": event,
                "resolved_params": resolved_params,
            })
    except Exception as e:
        logger.error(f"Error in generation tracker for prompt {prompt_id}: {e}")
        await ws_manager.send_to_client(browser_client_id, {
            "channel": "generation",
            "data": {"type": "error", "error": str(e), "prompt_id": prompt_id},
        })


@app.post("/api/generate")
async def generate_image(req: GenerateRequest):
    """
    Inject form parameters into workflow JSON, send to ComfyUI,
    and initiate progress relaying over WebSocket. Supports batch queuing.
    """
    # 1. Load active workflow template
    workflow = comfy_service.load_workflow_template()
    if not workflow:
        raise HTTPException(status_code=500, detail="No valid workflow template found.")

    extra = {
        "steps": req.steps,
        "cfg": req.cfg,
        "width": req.width,
        "height": req.height,
        "ckpt_name": req.ckpt_name,
        "sampler_name": req.sampler_name,
        "scheduler": req.scheduler,
    }

    batch_count = max(1, min(req.batch_count, 100))
    queued_prompts = []
    base_seed = req.seed

    for i in range(batch_count):
        if req.randomize_seed:
            current_seed = None
            randomize = True
        else:
            if base_seed is not None:
                current_seed = (base_seed + i) % (2**32)
            else:
                current_seed = random.randint(1, 2**32 - 1)
                base_seed = current_seed
            randomize = False

        prompt_graph, resolved_params = comfy_service.inject_workflow(
            workflow=workflow,
            positive_prompt=req.positive_prompt,
            negative_prompt=req.negative_prompt,
            seed=current_seed,
            randomize_seed=randomize,
            extra_params=extra,
        )

        # Persist the injected workflow template so future generations and page reloads retain updated resolution & parameters
        if i == 0:
            try:
                comfy_service.save_workflow_template(prompt_graph)
            except Exception as e:
                logger.warning(f"Failed to persist workflow template: {e}")

        logger.info(
            f"Queueing prompt (batch {i+1}/{batch_count}) with resolution {resolved_params.get('width')}x{resolved_params.get('height')}, seed={resolved_params.get('seed')}"
        )

        prompt_client_id = f"{req.client_id}_{uuid.uuid4().hex[:6]}"

        try:
            res = await comfy_service.queue_prompt(prompt_graph, client_id=prompt_client_id)
            prompt_id = res.get("prompt_id")
            if not prompt_id:
                raise RuntimeError(f"ComfyUI did not return prompt_id: {res}")

            queued_prompts.append({
                "prompt_id": prompt_id,
                "resolved_params": resolved_params,
                "queue_number": res.get("number"),
                "batch_index": i + 1,
                "batch_total": batch_count,
            })

            # Spawn background progress listener for this client and prompt
            asyncio.create_task(_run_generation_tracker(req.client_id, prompt_client_id, prompt_id, resolved_params))
        except Exception as e:
            if not queued_prompts:
                raise HTTPException(status_code=502, detail=f"Failed to queue prompt to ComfyUI: {e}")
            logger.warning(f"Error queueing batch item {i+1}/{batch_count}: {e}")
            break

    return {
        "status": "queued",
        "batch_count": len(queued_prompts),
        "prompts": queued_prompts,
        "prompt_id": queued_prompts[0]["prompt_id"] if queued_prompts else None,
        "resolved_params": queued_prompts[0]["resolved_params"] if queued_prompts else {},
        "queue_number": queued_prompts[0].get("queue_number") if queued_prompts else None,
    }


@app.post("/api/interrupt")
async def interrupt_generation():
    """Interrupt the currently executing prompt on ComfyUI."""
    success = await comfy_service.interrupt()
    return {"success": success}


@app.get("/api/images")
async def list_gallery_images(
    query: str = Query("", description="Search query"),
    sort: str = Query("newest", description="Sort order: newest, oldest, name_asc, name_desc"),
    limit: Optional[int] = Query(60, ge=0, le=100000, description="Page limit (0 returns all images)"),
    offset: int = Query(0, ge=0),
):
    """Return paginated list of gallery images, newest first by default. limit=0 returns all images."""
    return gallery_service.list_images(query=query, sort=sort, limit=limit, offset=offset)


@app.get("/api/images/{file_path:path}/metadata")
async def get_image_metadata(file_path: str):
    """Extract embedded prompt and generation metadata for an image."""
    meta = gallery_service.get_metadata(file_path)
    if not meta:
        raise HTTPException(status_code=404, detail="Image not found or could not be read.")
    return meta


@app.get("/api/images/{file_path:path}")
async def serve_image(file_path: str):
    """Serve full resolution image from the mounted output directory."""
    resolved = gallery_service.safe_resolve(file_path)
    if not resolved or not resolved.exists():
        raise HTTPException(status_code=404, detail="Image file not found.")

    return FileResponse(
        path=resolved,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/thumbnails/{file_path:path}")
async def serve_thumbnail(file_path: str):
    """Serve or generate a fast thumbnail for the gallery grid."""
    resolved = gallery_service.safe_resolve(file_path)
    if not resolved or not resolved.exists():
        raise HTTPException(status_code=404, detail="Image file not found.")

    thumb_path = gallery_service.ensure_thumbnail(resolved)
    return FileResponse(
        path=thumb_path,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.delete("/api/images/{file_path:path}")
async def delete_image(file_path: str):
    """Permanently delete image and its thumbnail from the mounted volume."""
    try:
        deleted = await gallery_service.delete_image(file_path)
        if not deleted:
            raise HTTPException(status_code=404, detail="Image file not found.")
        return {"success": True, "deleted": file_path}
    except Exception as e:
        logger.error(f"Error deleting image {file_path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket connection for real-time progress updates and gallery events."""
    await ws_manager.connect(client_id, websocket)
    try:
        while True:
            # We can receive ping/heartbeat or client messages
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        await ws_manager.disconnect(client_id, websocket)
    except Exception as e:
        logger.warning(f"WebSocket error for {client_id}: {e}")
        await ws_manager.disconnect(client_id, websocket)


def start():
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    start()
