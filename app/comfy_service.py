import asyncio
import copy
import io
import json
import logging
import random
import re
import shutil
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import websockets
from PIL import Image

from app.config import (
    COMFYUI_URL,
    DATA_DIR,
    DEFAULT_WORKFLOW_TEMPLATE,
    WORKFLOW_FILE,
    WORKFLOW_SEARCH_DIRS,
    WORKFLOWS_DIR,
)

logger = logging.getLogger("comfy_service")

_CLEAN_STEM_RE = re.compile(r"[^a-zA-Z0-9_\-\s]")
_RES_STR_RE = re.compile(r"\d+\s*[xX*×]\s*\d+")

_DIM_NODE_TYPES = {
    "EmptyLatentImage",
    "SDXLEmptyLatentImage",
    "EmptySD3LatentImage",
    "EmptyLatentImagePresets",
    "EmptyMochiLatentVideo",
    "EmptyHunyuanLatentVideo",
    "EmptyLTXVLatentVideo",
    "CR SDXL Aspect Ratio",
    "CR Aspect Ratio",
    "CR SD1.5 Aspect Ratio",
}
_DIM_CLASS_KEYWORDS = ("EmptyLatent", "EmptySD", "SDXLEmptyLatent", "LatentImage", "Aspect", "Resolution", "Size")
_DIM_INPUT_KEYS = ("width", "height", "empty_latent_width", "empty_latent_height", "aspect_ratio", "resolution")
_WIDTH_KEYS = ("width", "empty_latent_width", "target_width", "original_width", "max_width")
_HEIGHT_KEYS = ("height", "empty_latent_height", "target_height", "original_height", "max_height")


def _unwrap_workflow_json(data: Any) -> Dict[str, Any]:
    """Unwrap nested ComfyUI prompt graph from extra.prompt or prompt wrapper."""
    if isinstance(data, dict):
        if "extra" in data and isinstance(data["extra"], dict) and "prompt" in data["extra"]:
            return data["extra"]["prompt"]
        if "prompt" in data and isinstance(data["prompt"], dict):
            return data["prompt"]
        return data
    return {}


def _extract_workflow_from_image(img: Image.Image) -> Dict[str, Any]:
    """Extract embedded ComfyUI prompt or workflow JSON from an open PIL Image."""
    raw_info = getattr(img, "info", {}) or {}
    prompt_str = raw_info.get("prompt")
    if prompt_str:
        parsed = json.loads(prompt_str) if isinstance(prompt_str, str) else prompt_str
        return _unwrap_workflow_json(parsed)

    workflow_str = raw_info.get("workflow")
    if workflow_str:
        parsed = json.loads(workflow_str) if isinstance(workflow_str, str) else workflow_str
        return _unwrap_workflow_json(parsed)

    raise ValueError("No embedded ComfyUI workflow or prompt found in image metadata.")


def _set_clip_prompt_inputs(inputs: Dict[str, Any], prompt: str):
    """Populate CLIPTextEncode or SDXL CLIP text inputs with the given prompt."""
    if "text" in inputs or ("text_g" not in inputs and "text_l" not in inputs):
        inputs["text"] = prompt
    if "text_g" in inputs:
        inputs["text_g"] = prompt
    if "text_l" in inputs:
        inputs["text_l"] = prompt


class ComfyService:
    def __init__(self, comfy_url: str):
        self.comfy_url = comfy_url.rstrip("/")
        self._local_workflow_paths: Dict[str, Path] = {}
        self._cached_workflow: Optional[Tuple[float, Dict[str, Any]]] = None
        self._cached_active_id: Optional[str] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return a shared httpx.AsyncClient with connection pooling."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=8.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=25),
            )
        return self._http_client

    async def close(self):
        """Close the shared HTTP client cleanly on shutdown."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

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

    def _resolve_local_workflow_path(self, workflow_id: str) -> Optional[Path]:
        """Resolve a local:<filename> workflow ID to its Path on disk."""
        target_path = self._local_workflow_paths.get(workflow_id)
        if target_path and target_path.exists():
            return target_path

        filename = workflow_id.replace("local:", "", 1)
        for d in WORKFLOW_SEARCH_DIRS:
            if not d.exists():
                continue
            candidate = d / filename
            if candidate.exists() and candidate.is_file():
                self._local_workflow_paths[workflow_id] = candidate.resolve()
                return self._local_workflow_paths[workflow_id]
            for p in d.rglob(filename):
                if p.exists() and p.is_file():
                    self._local_workflow_paths[workflow_id] = p.resolve()
                    return self._local_workflow_paths[workflow_id]
        return None

    def load_workflow_template(self) -> Dict[str, Any]:
        """Load current workflow API JSON template with mtime-validated in-memory caching."""
        self._ensure_workflow_template()
        if WORKFLOW_FILE.exists():
            try:
                mtime = WORKFLOW_FILE.stat().st_mtime
                if self._cached_workflow and self._cached_workflow[0] == mtime:
                    return self._cached_workflow[1]
                with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
                    wf = json.load(f)
                self._cached_workflow = (mtime, wf)
                return wf
            except Exception as e:
                logger.error(f"Error reading {WORKFLOW_FILE}: {e}")

        # Fallback to default
        if DEFAULT_WORKFLOW_TEMPLATE.exists():
            with open(DEFAULT_WORKFLOW_TEMPLATE, "r", encoding="utf-8") as f:
                return json.load(f)

        return {}

    def save_workflow_template(self, workflow_dict: Dict[str, Any]):
        """Save updated workflow API JSON template, and update active local workflow file if applicable."""
        WORKFLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WORKFLOW_FILE, "w", encoding="utf-8") as f:
            json.dump(workflow_dict, f, indent=2)
        try:
            self._cached_workflow = (WORKFLOW_FILE.stat().st_mtime, workflow_dict)
        except Exception:
            self._cached_workflow = None
        logger.info(f"Updated workflow template at {WORKFLOW_FILE}")

        # If a local workflow file is currently active, sync the updated parameters to it as well
        active_id = self.get_active_workflow_id()
        if active_id and active_id.startswith("local:"):
            target_path = self._resolve_local_workflow_path(active_id)
            if target_path and target_path.exists() and target_path.suffix.lower() == ".json":
                try:
                    with open(target_path, "w", encoding="utf-8") as f:
                        json.dump(workflow_dict, f, indent=2)
                    logger.info(f"Updated selected local workflow file at {target_path}")
                except Exception as e:
                    logger.warning(f"Could not update local workflow file {target_path}: {e}")

    def reset_workflow_template(self) -> Dict[str, Any]:
        """Reset workflow to original default template."""
        if DEFAULT_WORKFLOW_TEMPLATE.exists():
            shutil.copy(DEFAULT_WORKFLOW_TEMPLATE, WORKFLOW_FILE)
        self._cached_workflow = None
        self.set_active_workflow_id("default")
        return self.load_workflow_template()

    def get_active_workflow_id(self) -> str:
        """Get currently active workflow ID."""
        if self._cached_active_id is not None:
            return self._cached_active_id
        id_file = DATA_DIR / "active_workflow_id.txt"
        if id_file.exists():
            try:
                self._cached_active_id = id_file.read_text(encoding="utf-8").strip() or "default"
                return self._cached_active_id
            except Exception:
                pass
        return "default"

    def set_active_workflow_id(self, workflow_id: str):
        """Save active workflow ID."""
        self._cached_active_id = workflow_id
        id_file = DATA_DIR / "active_workflow_id.txt"
        try:
            id_file.write_text(workflow_id, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write active workflow ID: {e}")

    async def _discover_comfy_users(self, client: httpx.AsyncClient) -> List[str]:
        """Discover ComfyUI user IDs for userdata endpoint headers."""
        try:
            user_res = await client.get(f"{self.comfy_url}/users", timeout=3.0)
            if user_res.status_code == 200:
                users_data = user_res.json()
                if isinstance(users_data, dict) and users_data:
                    return list(users_data.keys())
                if isinstance(users_data, list) and users_data:
                    return [u if isinstance(u, str) else u.get("id", "default") for u in users_data]
        except Exception:
            pass
        return ["default"]

    def _scan_local_workflows_sync(self, active_id: str) -> Tuple[Dict[str, Path], List[Dict[str, Any]]]:
        """Scan local search directories for workflow JSON/PNG/WebP files."""
        local_paths: Dict[str, Path] = {}
        discovered_local = set()
        local_workflows: List[Dict[str, Any]] = []

        for search_dir in WORKFLOW_SEARCH_DIRS:
            try:
                if not search_dir.exists():
                    continue
                for p in search_dir.rglob("*"):
                    if not p.is_file():
                        continue
                    if p.name == "workflow_api.json" or "thumbnails" in p.parts or "tags" in p.parts:
                        continue
                    suffix = p.suffix.lower()
                    if suffix not in (".json", ".png", ".webp"):
                        continue

                    resolved = p.resolve()
                    if str(resolved) in discovered_local:
                        continue
                    discovered_local.add(str(resolved))

                    wid = f"local:{p.name}"
                    if wid in local_paths:
                        wid = f"local:{p.parent.name}_{p.name}"

                    local_paths[wid] = resolved
                    display_name = p.stem.replace("_", " ").title()
                    icon = "🖼️" if suffix in (".png", ".webp") else "📜"
                    parent_tag = f" ({p.parent.name})" if p.parent != WORKFLOWS_DIR else ""

                    local_workflows.append({
                        "id": wid,
                        "name": f"{icon} {display_name}{parent_tag}",
                        "source": "local",
                        "filename": p.name,
                        "path": str(p),
                        "is_active": active_id == wid,
                    })
            except Exception as e:
                logger.debug(f"Error scanning workflow dir {search_dir}: {e}")

        return local_paths, local_workflows

    async def list_available_workflows(self) -> List[Dict[str, Any]]:
        """List workflows from default template, ComfyUI server (/userdata), and local workflows dir."""
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

        # 1. Check all local candidate directories off the event loop
        local_paths, local_wfs = await asyncio.to_thread(self._scan_local_workflows_sync, active_id)
        self._local_workflow_paths = local_paths
        workflows.extend(local_wfs)

        # 2. Check ComfyUI server userdata endpoints concurrently
        try:
            client = self._get_http_client()
            user_ids = await self._discover_comfy_users(client)
            discovered_filenames = set()

            async def _probe_url(url: str, headers: Optional[Dict[str, str]] = None):
                try:
                    return await client.get(url, headers=headers, timeout=3.0)
                except Exception:
                    return None

            for uid in user_ids:
                headers = {"comfy-user": uid}
                probe_urls = [
                    f"{self.comfy_url}/v2/userdata?path=workflows",
                    f"{self.comfy_url}/userdata?dir=workflows&recurse=true",
                    f"{self.comfy_url}/userdata?dir=workflows",
                    f"{self.comfy_url}/api/userdata?dir=workflows/",
                    f"{self.comfy_url}/v2/userdata?path=",
                    f"{self.comfy_url}/userdata",
                ]

                responses = await asyncio.gather(*(_probe_url(u, headers) for u in probe_urls))
                for res in responses:
                    if not res or res.status_code != 200:
                        continue
                    try:
                        data = res.json()
                    except Exception:
                        continue

                    items = data if isinstance(data, list) else (data.get("files") or data.get("items") or []) if isinstance(data, dict) else []
                    for item in items:
                        raw_path = ""
                        if isinstance(item, dict):
                            if item.get("type") == "directory":
                                continue
                            raw_path = item.get("path") or item.get("name", "")
                        elif isinstance(item, str):
                            raw_path = item

                        if raw_path.lower().endswith(".json"):
                            clean_name = raw_path.replace("workflows/", "").replace("workflows\\", "").replace("\\", "/")
                            if clean_name not in discovered_filenames:
                                discovered_filenames.add(clean_name)
                                wid = f"server:{clean_name}"
                                display_name = Path(clean_name).stem.replace("_", " ").title()
                                workflows.append({
                                    "id": wid,
                                    "name": f"☁️ {display_name}",
                                    "source": "server",
                                    "filename": clean_name,
                                    "is_active": active_id == wid,
                                })

            # Also probe PySSSSS (ComfyUI-Custom-Scripts workflow manager)
            pysssss_res = await _probe_url(f"{self.comfy_url}/pysssss/workflows")
            if pysssss_res and pysssss_res.status_code == 200:
                try:
                    pysssss_items = pysssss_res.json()
                    if isinstance(pysssss_items, list):
                        for p_item in pysssss_items:
                            p_name = p_item if isinstance(p_item, str) else p_item.get("name", "")
                            if p_name.lower().endswith(".json"):
                                clean_name = p_name.replace("\\", "/")
                                if clean_name not in discovered_filenames:
                                    discovered_filenames.add(clean_name)
                                    wid = f"server:{clean_name}"
                                    workflows.append({
                                        "id": wid,
                                        "name": f"📜 {Path(clean_name).stem.replace('_', ' ').title()}",
                                        "source": "server",
                                        "filename": clean_name,
                                        "is_active": active_id == wid,
                                    })
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Could not fetch server workflows: {e}")

        logger.info(f"Discovered {len(workflows)} available workflows.")
        return workflows

    async def fetch_workflow_by_id(self, workflow_id: str) -> Dict[str, Any]:
        """Fetch workflow JSON by id (default, local:<filename>, or server:<filename>)."""
        if workflow_id == "default":
            if DEFAULT_WORKFLOW_TEMPLATE.exists():
                with open(DEFAULT_WORKFLOW_TEMPLATE, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}

        if workflow_id.startswith("local:"):
            target_path = self._resolve_local_workflow_path(workflow_id)
            filename = workflow_id.replace("local:", "", 1)

            if not target_path or not target_path.exists():
                raise FileNotFoundError(f"Local workflow not found: {filename}")

            if target_path.suffix.lower() in (".png", ".webp"):
                with Image.open(target_path) as img:
                    return _extract_workflow_from_image(img)
            else:
                with open(target_path, "r", encoding="utf-8") as f:
                    return _unwrap_workflow_json(json.load(f))

        if workflow_id.startswith("server:"):
            filename = workflow_id.replace("server:", "", 1)
            client = self._get_http_client()
            user_ids = await self._discover_comfy_users(client)

            candidate_urls = [
                f"{self.comfy_url}/v2/userdata/workflows/{filename}",
                f"{self.comfy_url}/userdata/workflows/{filename}",
                f"{self.comfy_url}/v2/userdata/{filename}",
                f"{self.comfy_url}/userdata/{filename}",
                f"{self.comfy_url}/pysssss/workflows/{filename}",
                f"{self.comfy_url}/api/userdata/workflows/{filename}",
                f"{self.comfy_url}/userdata?file=workflows/{filename}",
                f"{self.comfy_url}/userdata?file={filename}",
            ]

            async def _try_get(url: str, headers: Dict[str, str]):
                try:
                    res = await client.get(url, headers=headers, timeout=5.0)
                    if res.status_code == 200:
                        data = res.json()
                        if isinstance(data, dict):
                            return _unwrap_workflow_json(data)
                except Exception:
                    pass
                return None

            for uid in user_ids:
                headers = {"comfy-user": uid}
                results = await asyncio.gather(*(_try_get(u, headers) for u in candidate_urls))
                for wf in results:
                    if wf:
                        return wf

            raise FileNotFoundError(f"Server workflow not found: {filename}")

        raise ValueError(f"Unknown workflow id: {workflow_id}")

    def import_workflow_file(self, filename: str, content_bytes: bytes) -> Dict[str, Any]:
        """
        Import a workflow from an uploaded .json or .png file.
        Saves into WORKFLOWS_DIR, activates it, and returns workflow dict and metadata.
        """
        safe_stem = Path(filename).stem
        clean_stem = _CLEAN_STEM_RE.sub("", safe_stem).strip().replace(" ", "_") or "imported_workflow"

        target_json_name = f"{clean_stem}.json"
        target_path = WORKFLOWS_DIR / target_json_name

        lower_name = filename.lower()
        if lower_name.endswith(".json"):
            try:
                parsed = json.loads(content_bytes.decode("utf-8"))
            except Exception as e:
                raise ValueError(f"Invalid JSON file: {e}")

            if not isinstance(parsed, dict):
                raise ValueError("JSON content must be a workflow dictionary / graph.")
            workflow_data = _unwrap_workflow_json(parsed)

        elif lower_name.endswith((".png", ".webp")):
            try:
                with Image.open(io.BytesIO(content_bytes)) as img:
                    workflow_data = _extract_workflow_from_image(img)
            except Exception as e:
                raise ValueError(f"Failed to extract workflow from image: {e}")
        else:
            raise ValueError("Supported formats are .json or .png/.webp image files.")

        if not isinstance(workflow_data, dict) or not workflow_data:
            raise ValueError("Parsed workflow is empty or invalid.")

        # Save to local workflows directory
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(workflow_data, f, indent=2)

        # Activate imported workflow
        self.save_workflow_template(workflow_data)
        wid = f"local:{target_json_name}"
        self._local_workflow_paths[wid] = target_path.resolve()
        self.set_active_workflow_id(wid)

        display_name = clean_stem.replace("_", " ").title()
        return {
            "id": wid,
            "name": f"✨ {display_name}",
            "filename": target_json_name,
            "workflow": workflow_data,
        }

    async def save_workflow(self, filename: str, workflow_dict: Dict[str, Any], destination: str = "local") -> bool:
        """Save workflow to local storage or ComfyUI server."""
        clean_name = filename if filename.endswith(".json") else f"{filename}.json"

        # Always save locally in WORKFLOWS_DIR
        target = WORKFLOWS_DIR / clean_name
        with open(target, "w", encoding="utf-8") as f:
            json.dump(workflow_dict, f, indent=2)

        if destination == "server":
            try:
                client = self._get_http_client()
                res = await client.post(
                    f"{self.comfy_url}/userdata/workflows/{clean_name}",
                    headers={"comfy-user": "default"},
                    json=workflow_dict,
                    timeout=6.0,
                )
                return res.status_code in (200, 201)
            except Exception as e:
                logger.error(f"Failed to post workflow to ComfyUI server: {e}")
                return False

        return True

    async def check_health(self) -> Dict[str, Any]:
        """Check ComfyUI server connectivity and stats."""
        try:
            client = self._get_http_client()
            res = await client.get(f"{self.comfy_url}/system_stats", timeout=3.0)
            if res.status_code == 200:
                return {"connected": True, "stats": res.json(), "url": self.comfy_url}
        except Exception as e:
            return {"connected": False, "error": str(e), "url": self.comfy_url}
        return {"connected": False, "url": self.comfy_url}

    async def get_checkpoints(self) -> List[str]:
        """Fetch list of available checkpoints from ComfyUI."""
        try:
            client = self._get_http_client()
            res = await client.get(f"{self.comfy_url}/object_info/CheckpointLoaderSimple", timeout=4.0)
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
        for node in prompt_graph.values():
            if isinstance(node, dict) and "KSampler" in node.get("class_type", ""):
                ksampler_node = node
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
                    _set_clip_prompt_inputs(prompt_graph[pos_id].setdefault("inputs", {}), positive_prompt)
                    pos_injected = True

            neg_link = inputs.get("negative")
            if isinstance(neg_link, list) and len(neg_link) > 0:
                neg_id = str(neg_link[0])
                if neg_id in prompt_graph and "inputs" in prompt_graph[neg_id]:
                    _set_clip_prompt_inputs(prompt_graph[neg_id].setdefault("inputs", {}), negative_prompt)
                    neg_injected = True

        # Fallback for prompt injection if not wired directly via KSampler
        if not pos_injected or not neg_injected:
            clip_nodes = [
                (nid, node)
                for nid, node in prompt_graph.items()
                if isinstance(node, dict) and ("CLIPTextEncode" in node.get("class_type", "") or "CLIP" in node.get("class_type", ""))
            ]

            for _, node in clip_nodes:
                title = str(node.get("_meta", {}).get("title", "")).lower()
                n_inputs = node.setdefault("inputs", {})
                if not pos_injected and ("positive" in title or "pos" in title):
                    _set_clip_prompt_inputs(n_inputs, positive_prompt)
                    pos_injected = True
                elif not neg_injected and ("negative" in title or "neg" in title):
                    _set_clip_prompt_inputs(n_inputs, negative_prompt)
                    neg_injected = True

            if not pos_injected and len(clip_nodes) > 0:
                _set_clip_prompt_inputs(clip_nodes[0][1].setdefault("inputs", {}), positive_prompt)
                pos_injected = True
            if not neg_injected and len(clip_nodes) > 1:
                _set_clip_prompt_inputs(clip_nodes[1][1].setdefault("inputs", {}), negative_prompt)
                neg_injected = True

        # Checkpoint Model injection
        if extra.get("ckpt_name"):
            for node in prompt_graph.values():
                if isinstance(node, dict):
                    c_type = node.get("class_type", "")
                    inputs = node.setdefault("inputs", {})
                    if "CheckpointLoader" in c_type or ("ckpt_name" in inputs and "Loader" in c_type):
                        inputs["ckpt_name"] = str(extra["ckpt_name"])
                        resolved_params["ckpt_name"] = str(extra["ckpt_name"])
                        break

        # Comprehensive Dimensions injection: Latent creation, Aspect Ratio, SDXL Conditioning, and Primitive links
        target_width = int(extra["width"]) if extra.get("width") else None
        target_height = int(extra["height"]) if extra.get("height") else None

        if target_width or target_height:
            modified_nodes = set()

            # 1. Update upstream PrimitiveNodes linked to width or height
            for node in prompt_graph.values():
                if not isinstance(node, dict):
                    continue
                inputs = node.setdefault("inputs", {})
                for k, v in inputs.items():
                    if isinstance(v, list) and len(v) > 0 and str(v[0]) in prompt_graph:
                        k_lower = k.lower()
                        p_id = str(v[0])
                        p_in = prompt_graph[p_id].setdefault("inputs", {})
                        if "width" in k_lower and target_width:
                            if "value" in p_in:
                                p_in["value"] = target_width
                                modified_nodes.add(p_id)
                            if "width" in p_in:
                                p_in["width"] = target_width
                                modified_nodes.add(p_id)
                        elif "height" in k_lower and target_height:
                            if "value" in p_in:
                                p_in["value"] = target_height
                                modified_nodes.add(p_id)
                            if "height" in p_in:
                                p_in["height"] = target_height
                                modified_nodes.add(p_id)

            # 2. Comprehensive node update: Dimensions, Aspect Ratio presets, and Latent generators
            for nid, node in prompt_graph.items():
                if not isinstance(node, dict):
                    continue
                c_type = node.get("class_type", "")
                inputs = node.setdefault("inputs", {})

                is_dim_node = (
                    c_type in _DIM_NODE_TYPES
                    or any(k in c_type for k in _DIM_CLASS_KEYWORDS)
                    or any(k in inputs for k in _DIM_INPUT_KEYS)
                )

                if is_dim_node:
                    if target_width:
                        for wk in _WIDTH_KEYS:
                            if wk in inputs:
                                inputs[wk] = target_width
                                modified_nodes.add(nid)
                    if target_height:
                        for hk in _HEIGHT_KEYS:
                            if hk in inputs:
                                inputs[hk] = target_height
                                modified_nodes.add(nid)

                    for ark in ("aspect_ratio", "aspect_ratio_preset", "aspect"):
                        if ark in inputs:
                            inputs[ark] = "custom"
                            modified_nodes.add(nid)
                    if "swap_dimensions" in inputs:
                        inputs["swap_dimensions"] = "Off"
                        modified_nodes.add(nid)

                    for rk in ("resolution", "dimensions", "preset", "size"):
                        if rk in inputs and isinstance(inputs[rk], str) and _RES_STR_RE.search(inputs[rk]):
                            inputs[rk] = f"{target_width}x{target_height}"
                            modified_nodes.add(nid)

                    if any(k in c_type for k in ("EmptyLatent", "EmptySD", "SDXLEmptyLatent", "LatentImage")):
                        if target_width:
                            inputs["width"] = target_width
                        if target_height:
                            inputs["height"] = target_height
                        modified_nodes.add(nid)

                # 3. Synchronize SDXL conditioning dimensions (CLIPTextEncodeSDXL, etc.)
                if "CLIPTextEncodeSDXL" in c_type or "SDXL" in c_type:
                    if target_width:
                        for wk in ("width", "target_width", "original_width"):
                            if wk in inputs:
                                inputs[wk] = target_width
                        modified_nodes.add(nid)
                    if target_height:
                        for hk in ("height", "target_height", "original_height"):
                            if hk in inputs:
                                inputs[hk] = target_height
                        modified_nodes.add(nid)

            if target_width:
                resolved_params["width"] = target_width
            if target_height:
                resolved_params["height"] = target_height

            logger.info(
                f"Injected resolution {target_width}x{target_height} into {len(modified_nodes)} node(s): {list(modified_nodes)}"
            )

        return prompt_graph, resolved_params

    async def queue_prompt(self, prompt_graph: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """Send prompt payload to ComfyUI POST /prompt endpoint."""
        payload = {
            "prompt": prompt_graph,
            "client_id": client_id,
        }
        client = self._get_http_client()
        res = await client.post(f"{self.comfy_url}/prompt", json=payload, timeout=10.0)
        if res.status_code != 200:
            raise RuntimeError(f"ComfyUI rejected prompt ({res.status_code}): {res.text}")
        return res.json()

    async def interrupt(self) -> bool:
        """Interrupt current generation on ComfyUI and clear queued prompts."""
        try:
            client = self._get_http_client()
            try:
                await client.post(f"{self.comfy_url}/queue", json={"clear": True}, timeout=4.0)
            except Exception:
                pass
            res = await client.post(f"{self.comfy_url}/interrupt", timeout=4.0)
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
