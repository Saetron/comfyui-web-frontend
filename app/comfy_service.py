import asyncio
import copy
import json
import logging
import random
import shutil
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import websockets

from app.config import (
    COMFYUI_URL,
    DATA_DIR,
    DEFAULT_WORKFLOW_TEMPLATE,
    WORKFLOW_FILE,
    WORKFLOWS_DIR,
)

logger = logging.getLogger("comfy_service")


class ComfyService:
    def __init__(self, comfy_url: str):
        self.comfy_url = comfy_url.rstrip("/")
        self._ensure_workflow_template()

    def _ensure_workflow_template(self):
        """Ensure active workflow template file exists in data directory."""
        try:
            if not WORKFLOW_FILE.exists():
                WORKFLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
                if DEFAULT_WORKFLOW_TEMPLATE.exists():
                    shutil.copy(DEFAULT_WORKFLOW_TEMPLATE, WORKFLOW_FILE)
                    logger.info(f"Initialized default workflow template at {WORKFLOW_FILE}")
        except Exception as e:
            logger.error(f"Error initializing workflow file: {e}")

    def load_workflow_template(self) -> Dict[str, Any]:
        """Load current workflow API JSON template."""
        self._ensure_workflow_template()
        if WORKFLOW_FILE.exists():
            try:
                with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading {WORKFLOW_FILE}: {e}")

        # Fallback to default
        if DEFAULT_WORKFLOW_TEMPLATE.exists():
            with open(DEFAULT_WORKFLOW_TEMPLATE, "r", encoding="utf-8") as f:
                return json.load(f)

        return {}

    def save_workflow_template(self, workflow_dict: Dict[str, Any]):
        """Save updated workflow API JSON template."""
        WORKFLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WORKFLOW_FILE, "w", encoding="utf-8") as f:
            json.dump(workflow_dict, f, indent=2)
        logger.info(f"Updated workflow template at {WORKFLOW_FILE}")

    def reset_workflow_template(self) -> Dict[str, Any]:
        """Reset workflow to original default template."""
        if DEFAULT_WORKFLOW_TEMPLATE.exists():
            shutil.copy(DEFAULT_WORKFLOW_TEMPLATE, WORKFLOW_FILE)
        self.set_active_workflow_id("default")
        return self.load_workflow_template()

    def get_active_workflow_id(self) -> str:
        """Get currently active workflow ID."""
        id_file = DATA_DIR / "active_workflow_id.txt"
        if id_file.exists():
            try:
                return id_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return "default"

    def set_active_workflow_id(self, workflow_id: str):
        """Save active workflow ID."""
        id_file = DATA_DIR / "active_workflow_id.txt"
        try:
            id_file.write_text(workflow_id, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write active workflow ID: {e}")

    async def list_available_workflows(self) -> List[Dict[str, Any]]:
        """List workflows from default template, ComfyUI server (/userdata?dir=workflows/), and local workflows dir."""
        active_id = self.get_active_workflow_id()
        workflows = [
            {
                "id": "default",
                "name": "Default SD 1.5 Workflow",
                "source": "default",
                "filename": "default_workflow_api.json",
                "is_active": active_id == "default",
            }
        ]

        # 1. Check local WORKFLOWS_DIR
        try:
            if WORKFLOWS_DIR.exists():
                for p in sorted(WORKFLOWS_DIR.glob("*.json")):
                    wid = f"local:{p.name}"
                    workflows.append({
                        "id": wid,
                        "name": p.stem.replace("_", " ").title(),
                        "source": "local",
                        "filename": p.name,
                        "is_active": active_id == wid,
                    })
        except Exception as e:
            logger.warning(f"Error reading local workflows: {e}")

        # 2. Check ComfyUI server userdata endpoint
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                for url in [
                    f"{self.comfy_url}/userdata?dir=workflows/",
                    f"{self.comfy_url}/api/userdata?dir=workflows/",
                    f"{self.comfy_url}/userdata",
                ]:
                    try:
                        res = await client.get(url)
                        if res.status_code == 200:
                            data = res.json()
                            items = data if isinstance(data, list) else data.get("files", [])
                            for item in items:
                                fname = item if isinstance(item, str) else item.get("name", "")
                                if fname.endswith(".json"):
                                    clean_name = fname.replace("workflows/", "").replace("\\", "/")
                                    wid = f"server:{clean_name}"
                                    workflows.append({
                                        "id": wid,
                                        "name": Path(clean_name).stem.replace("_", " ").title(),
                                        "source": "server",
                                        "filename": clean_name,
                                        "is_active": active_id == wid,
                                    })
                            break
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"Could not fetch server workflows: {e}")

        return workflows

    async def fetch_workflow_by_id(self, workflow_id: str) -> Dict[str, Any]:
        """Fetch workflow JSON by id (default, local:<filename>, or server:<filename>)."""
        if workflow_id == "default":
            if DEFAULT_WORKFLOW_TEMPLATE.exists():
                with open(DEFAULT_WORKFLOW_TEMPLATE, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}

        if workflow_id.startswith("local:"):
            filename = workflow_id.replace("local:", "", 1)
            target = WORKFLOWS_DIR / filename
            if target.exists():
                with open(target, "r", encoding="utf-8") as f:
                    return json.load(f)
            raise FileNotFoundError(f"Local workflow not found: {filename}")

        if workflow_id.startswith("server:"):
            filename = workflow_id.replace("server:", "", 1)
            async with httpx.AsyncClient(timeout=6.0) as client:
                for url in [
                    f"{self.comfy_url}/userdata/workflows/{filename}",
                    f"{self.comfy_url}/api/userdata/workflows/{filename}",
                    f"{self.comfy_url}/userdata/{filename}",
                ]:
                    try:
                        res = await client.get(url)
                        if res.status_code == 200:
                            data = res.json()
                            if isinstance(data, dict):
                                if "extra" in data and isinstance(data["extra"], dict) and "prompt" in data["extra"]:
                                    return data["extra"]["prompt"]
                                if "prompt" in data and isinstance(data["prompt"], dict):
                                    return data["prompt"]
                                return data
                    except Exception:
                        continue
            raise FileNotFoundError(f"Server workflow not found: {filename}")

        raise ValueError(f"Unknown workflow id: {workflow_id}")

    async def save_workflow(self, filename: str, workflow_dict: Dict[str, Any], destination: str = "local") -> bool:
        """Save workflow to local storage or ComfyUI server."""
        clean_name = filename if filename.endswith(".json") else f"{filename}.json"
        
        # Always save locally in WORKFLOWS_DIR
        target = WORKFLOWS_DIR / clean_name
        with open(target, "w", encoding="utf-8") as f:
            json.dump(workflow_dict, f, indent=2)

        if destination == "server":
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    res = await client.post(
                        f"{self.comfy_url}/userdata/workflows/{clean_name}",
                        json=workflow_dict
                    )
                    return res.status_code in (200, 201)
            except Exception as e:
                logger.error(f"Failed to post workflow to ComfyUI server: {e}")
                return False

        return True

    async def check_health(self) -> Dict[str, Any]:
        """Check ComfyUI server connectivity and stats."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.comfy_url}/system_stats")
                if res.status_code == 200:
                    return {"connected": True, "stats": res.json(), "url": self.comfy_url}
        except Exception as e:
            return {"connected": False, "error": str(e), "url": self.comfy_url}
        return {"connected": False, "url": self.comfy_url}

    async def get_checkpoints(self) -> List[str]:
        """Fetch list of available checkpoints from ComfyUI."""
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(f"{self.comfy_url}/object_info/CheckpointLoaderSimple")
                if res.status_code == 200:
                    data = res.json()
                    input_info = data.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {})
                    ckpts = input_info.get("ckpt_name", [[]])[0]
                    if isinstance(ckpts, list):
                        return ckpts
        except Exception as e:
            logger.debug(f"Could not fetch checkpoints from ComfyUI: {e}")
        return []

    def inject_workflow(
        self,
        workflow: Dict[str, Any],
        positive_prompt: str,
        negative_prompt: str,
        seed: Optional[int] = None,
        randomize_seed: bool = True,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Inject prompt values and parameters into standard ComfyUI API nodes.
        Returns the modified workflow dict and the resolved parameters.
        """
        prompt_graph = copy.deepcopy(workflow)
        extra = extra_params or {}

        # 1. Determine seed
        if randomize_seed or seed is None or seed < 0:
            effective_seed = random.randint(1, 1125899906842624)
        else:
            effective_seed = int(seed)

        resolved_params: Dict[str, Any] = {
            "seed": effective_seed,
            "positive_prompt": positive_prompt,
            "negative_prompt": negative_prompt,
        }

        # Find KSampler node(s)
        ksampler_node = None
        ksampler_id = None
        for nid, node in prompt_graph.items():
            if isinstance(node, dict) and "KSampler" in node.get("class_type", ""):
                ksampler_node = node
                ksampler_id = nid
                break

        pos_injected = False
        neg_injected = False

        if ksampler_node:
            inputs = ksampler_node.get("inputs", {})
            if "seed" in inputs:
                inputs["seed"] = effective_seed
            elif "noise_seed" in inputs:
                inputs["noise_seed"] = effective_seed

            # Optional extra params
            if extra.get("steps") is not None:
                inputs["steps"] = int(extra["steps"])
                resolved_params["steps"] = inputs["steps"]
            if extra.get("cfg") is not None:
                inputs["cfg"] = float(extra["cfg"])
                resolved_params["cfg"] = inputs["cfg"]
            if extra.get("sampler_name"):
                inputs["sampler_name"] = str(extra["sampler_name"])
                resolved_params["sampler_name"] = inputs["sampler_name"]
            if extra.get("scheduler"):
                inputs["scheduler"] = str(extra["scheduler"])
                resolved_params["scheduler"] = inputs["scheduler"]

            # Trace positive and negative links
            pos_link = inputs.get("positive")
            if isinstance(pos_link, list) and len(pos_link) > 0:
                pos_id = str(pos_link[0])
                if pos_id in prompt_graph and "inputs" in prompt_graph[pos_id]:
                    prompt_graph[pos_id]["inputs"]["text"] = positive_prompt
                    pos_injected = True

            neg_link = inputs.get("negative")
            if isinstance(neg_link, list) and len(neg_link) > 0:
                neg_id = str(neg_link[0])
                if neg_id in prompt_graph and "inputs" in prompt_graph[neg_id]:
                    prompt_graph[neg_id]["inputs"]["text"] = negative_prompt
                    neg_injected = True

        # Fallback for prompt injection if not wired directly via KSampler
        if not pos_injected or not neg_injected:
            clip_nodes = []
            for nid, node in prompt_graph.items():
                if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                    clip_nodes.append((nid, node))

            for nid, node in clip_nodes:
                title = str(node.get("_meta", {}).get("title", "")).lower()
                if not pos_injected and ("positive" in title or "pos" in title):
                    node.setdefault("inputs", {})["text"] = positive_prompt
                    pos_injected = True
                elif not neg_injected and ("negative" in title or "neg" in title):
                    node.setdefault("inputs", {})["text"] = negative_prompt
                    neg_injected = True

            if not pos_injected and len(clip_nodes) > 0:
                clip_nodes[0][1].setdefault("inputs", {})["text"] = positive_prompt
                pos_injected = True
            if not neg_injected and len(clip_nodes) > 1:
                clip_nodes[1][1].setdefault("inputs", {})["text"] = negative_prompt
                neg_injected = True

        # Checkpoint Model injection
        if extra.get("ckpt_name"):
            for nid, node in prompt_graph.items():
                if isinstance(node, dict) and "CheckpointLoader" in node.get("class_type", ""):
                    node.setdefault("inputs", {})["ckpt_name"] = str(extra["ckpt_name"])
                    resolved_params["ckpt_name"] = str(extra["ckpt_name"])
                    break

        # Dimensions injection
        if extra.get("width") or extra.get("height"):
            for nid, node in prompt_graph.items():
                if isinstance(node, dict) and node.get("class_type") == "EmptyLatentImage":
                    inputs = node.setdefault("inputs", {})
                    if extra.get("width"):
                        inputs["width"] = int(extra["width"])
                        resolved_params["width"] = inputs["width"]
                    if extra.get("height"):
                        inputs["height"] = int(extra["height"])
                        resolved_params["height"] = inputs["height"]
                    break

        return prompt_graph, resolved_params

    async def queue_prompt(self, prompt_graph: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """Send prompt payload to ComfyUI POST /prompt endpoint."""
        payload = {
            "prompt": prompt_graph,
            "client_id": client_id,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(f"{self.comfy_url}/prompt", json=payload)
            if res.status_code != 200:
                text = res.text
                raise RuntimeError(f"ComfyUI rejected prompt ({res.status_code}): {text}")
            return res.json()

    async def interrupt(self) -> bool:
        """Interrupt current generation on ComfyUI."""
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.post(f"{self.comfy_url}/interrupt")
                return res.status_code == 200
        except Exception as e:
            logger.error(f"Error interrupting ComfyUI: {e}")
            return False

    def get_ws_url(self, client_id: str) -> str:
        """Derive WebSocket URL from HTTP ComfyUI URL."""
        parsed = urlparse(self.comfy_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{ws_scheme}://{parsed.netloc}/ws?clientId={client_id}"

    async def listen_prompt_progress(
        self, client_id: str, prompt_id: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Listen to ComfyUI's WebSocket for the given prompt_id and yield status/progress events.
        """
        ws_url = self.get_ws_url(client_id)
        logger.info(f"Connecting to ComfyUI WebSocket: {ws_url} for prompt {prompt_id}")

        try:
            async with websockets.connect(ws_url, max_size=50 * 1024 * 1024, ping_interval=20) as ws:
                yield {
                    "type": "connected",
                    "prompt_id": prompt_id,
                    "message": "Connected to ComfyUI engine",
                }

                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=600.0)
                    except asyncio.TimeoutError:
                        yield {"type": "timeout", "prompt_id": prompt_id, "error": "ComfyUI generation timed out"}
                        break

                    # Binary messages can be latent previews
                    if isinstance(message, bytes):
                        # Skip or forward preview image if desired
                        continue

                    try:
                        msg_data = json.loads(message)
                    except Exception:
                        continue

                    msg_type = msg_data.get("type")
                    data = msg_data.get("data", {})

                    # Filter for our prompt
                    msg_prompt_id = data.get("prompt_id")

                    if msg_type == "status":
                        exec_info = data.get("status", {}).get("exec_info", {})
                        yield {
                            "type": "status",
                            "queue_remaining": exec_info.get("queue_remaining", 0),
                        }

                    elif msg_type == "execution_start":
                        if msg_prompt_id == prompt_id:
                            yield {
                                "type": "execution_start",
                                "prompt_id": prompt_id,
                            }

                    elif msg_type == "executing":
                        if msg_prompt_id == prompt_id:
                            node = data.get("node")
                            if node is None:
                                # Execution finished!
                                yield {
                                    "type": "complete",
                                    "prompt_id": prompt_id,
                                }
                                break
                            else:
                                yield {
                                    "type": "executing",
                                    "node": node,
                                    "prompt_id": prompt_id,
                                }

                    elif msg_type == "progress":
                        if msg_prompt_id == prompt_id:
                            value = data.get("value", 0)
                            max_val = data.get("max", 1)
                            percentage = round((value / max_val) * 100) if max_val > 0 else 0
                            yield {
                                "type": "progress",
                                "value": value,
                                "max": max_val,
                                "percentage": percentage,
                                "node": data.get("node"),
                                "prompt_id": prompt_id,
                            }

                    elif msg_type == "executed":
                        if msg_prompt_id == prompt_id:
                            output = data.get("output", {})
                            images = output.get("images", [])
                            if images:
                                yield {
                                    "type": "images_ready",
                                    "images": images,
                                    "node": data.get("node"),
                                    "prompt_id": prompt_id,
                                }

                    elif msg_type == "execution_error":
                        if msg_prompt_id == prompt_id:
                            yield {
                                "type": "error",
                                "error": data.get("exception_message", "ComfyUI Execution Error"),
                                "details": data,
                                "prompt_id": prompt_id,
                            }
                            break

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"ComfyUI WebSocket closed: {e}")
            yield {"type": "ws_closed", "prompt_id": prompt_id, "message": str(e)}
        except Exception as e:
            logger.error(f"WebSocket listener error: {e}")
            yield {"type": "error", "prompt_id": prompt_id, "error": str(e)}


# Global instance
comfy_service = ComfyService(COMFYUI_URL)
