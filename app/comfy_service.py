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
    CONFIG_DIR,
    DATA_DIR,
    DATA_EXTRA_DIR,
    DEFAULT_WORKFLOW_TEMPLATE,
    WORKFLOW_FILE,
    WORKFLOW_SEARCH_DIRS,
    WORKFLOWS_DIR,
)

logger = logging.getLogger("comfy_service")


class ComfyService:
    def __init__(self, comfy_url: str):
        self.comfy_url = comfy_url.rstrip("/")
        self._local_workflow_paths: Dict[str, Path] = {}
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

        # 1. Check all local candidate directories (workflows/, config/, data/, appdata/)
        self._local_workflow_paths = {}
        discovered_local = set()

        for search_dir in WORKFLOW_SEARCH_DIRS:
            try:
                if not search_dir.exists():
                    continue
                # Search directory and subdirectories for *.json, *.png, *.webp
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
                    if wid in self._local_workflow_paths:
                        wid = f"local:{p.parent.name}_{p.name}"

                    self._local_workflow_paths[wid] = resolved
                    display_name = p.stem.replace("_", " ").title()
                    icon = "🖼️" if suffix in (".png", ".webp") else "📜"
                    parent_tag = f" ({p.parent.name})" if p.parent != WORKFLOWS_DIR else ""

                    workflows.append({
                        "id": wid,
                        "name": f"{icon} {display_name}{parent_tag}",
                        "source": "local",
                        "filename": p.name,
                        "path": str(p),
                        "is_active": active_id == wid,
                    })
            except Exception as e:
                logger.debug(f"Error scanning workflow dir {search_dir}: {e}")

        # 2. Check ComfyUI server userdata endpoints
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                # First discover users (default, plus any multi-user IDs)
                user_ids = ["default"]
                try:
                    user_res = await client.get(f"{self.comfy_url}/users")
                    if user_res.status_code == 200:
                        users_data = user_res.json()
                        if isinstance(users_data, dict):
                            user_ids = list(users_data.keys())
                        elif isinstance(users_data, list):
                            user_ids = [u if isinstance(u, str) else u.get("id", "default") for u in users_data]
                except Exception:
                    pass

                discovered_filenames = set()

                for uid in user_ids:
                    headers = {"comfy-user": uid}
                    # Test various paths and endpoints
                    probe_urls = [
                        f"{self.comfy_url}/v2/userdata?path=workflows",
                        f"{self.comfy_url}/v2/userdata?path=",
                        f"{self.comfy_url}/userdata?dir=workflows&recurse=true",
                        f"{self.comfy_url}/userdata?dir=workflows/&recurse=true",
                        f"{self.comfy_url}/userdata?dir=&recurse=true",
                        f"{self.comfy_url}/userdata?dir=workflows",
                        f"{self.comfy_url}/userdata?dir=workflows/",
                        f"{self.comfy_url}/api/userdata?dir=workflows/",
                        f"{self.comfy_url}/userdata",
                    ]

                    for url in probe_urls:
                        try:
                            res = await client.get(url, headers=headers)
                            if res.status_code == 200:
                                data = res.json()
                                items = []
                                if isinstance(data, list):
                                    items = data
                                elif isinstance(data, dict):
                                    items = data.get("files", []) or data.get("items", []) or []

                                for item in items:
                                    # v2/userdata item: {"name": "foo.json", "type": "file", "path": "workflows/foo.json"}
                                    # userdata item: "workflows/foo.json" or "foo.json"
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
                        except Exception as e:
                            logger.debug(f"Error querying {url}: {e}")

                # Also probe PySSSSS (ComfyUI-Custom-Scripts workflow manager)
                try:
                    res = await client.get(f"{self.comfy_url}/pysssss/workflows")
                    if res.status_code == 200:
                        pysssss_items = res.json()
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
            target_path = self._local_workflow_paths.get(workflow_id)
            filename = workflow_id.replace("local:", "", 1)

            if not target_path or not target_path.exists():
                for d in WORKFLOW_SEARCH_DIRS:
                    if not d.exists():
                        continue
                    candidate = d / filename
                    if candidate.exists() and candidate.is_file():
                        target_path = candidate
                        break
                    for p in d.rglob(filename):
                        if p.exists() and p.is_file():
                            target_path = p
                            break
                    if target_path:
                        break

            if not target_path or not target_path.exists():
                raise FileNotFoundError(f"Local workflow not found: {filename}")

            if target_path.suffix.lower() in (".png", ".webp"):
                from PIL import Image
                with Image.open(target_path) as img:
                    info = getattr(img, "info", {}) or {}
                    prompt_str = info.get("prompt")
                    workflow_str = info.get("workflow")
                    if prompt_str:
                        return json.loads(prompt_str) if isinstance(prompt_str, str) else prompt_str
                    if workflow_str:
                        wf = json.loads(workflow_str) if isinstance(workflow_str, str) else workflow_str
                        if isinstance(wf, dict) and "extra" in wf and "prompt" in wf["extra"]:
                            return wf["extra"]["prompt"]
                        return wf
                    raise ValueError(f"No ComfyUI metadata found inside image: {target_path.name}")
            else:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        if "extra" in data and isinstance(data["extra"], dict) and "prompt" in data["extra"]:
                            return data["extra"]["prompt"]
                        if "prompt" in data and isinstance(data["prompt"], dict):
                            return data["prompt"]
                    return data


        if workflow_id.startswith("server:"):
            filename = workflow_id.replace("server:", "", 1)
            async with httpx.AsyncClient(timeout=8.0) as client:
                # Find candidate user headers
                user_ids = ["default"]
                try:
                    user_res = await client.get(f"{self.comfy_url}/users")
                    if user_res.status_code == 200:
                        u_data = user_res.json()
                        if isinstance(u_data, dict):
                            user_ids = list(u_data.keys())
                        elif isinstance(u_data, list):
                            user_ids = [u if isinstance(u, str) else u.get("id", "default") for u in u_data]
                except Exception:
                    pass

                candidate_urls = [
                    f"{self.comfy_url}/v2/userdata/workflows/{filename}",
                    f"{self.comfy_url}/v2/userdata/{filename}",
                    f"{self.comfy_url}/userdata/workflows/{filename}",
                    f"{self.comfy_url}/userdata/{filename}",
                    f"{self.comfy_url}/pysssss/workflows/{filename}",
                    f"{self.comfy_url}/api/userdata/workflows/{filename}",
                    f"{self.comfy_url}/userdata?file=workflows/{filename}",
                    f"{self.comfy_url}/userdata?file={filename}",
                ]

                for uid in user_ids:
                    headers = {"comfy-user": uid}
                    for url in candidate_urls:
                        try:
                            res = await client.get(url, headers=headers)
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

    def import_workflow_file(self, filename: str, content_bytes: bytes) -> Dict[str, Any]:
        """
        Import a workflow from an uploaded .json or .png file.
        Saves into WORKFLOWS_DIR, activates it, and returns workflow dict and metadata.
        """
        import io
        from PIL import Image

        safe_stem = Path(filename).stem
        # Clean stem to valid filename
        clean_stem = re.sub(r'[^a-zA-Z0-9_\-\s]', '', safe_stem).strip().replace(' ', '_')
        if not clean_stem:
            clean_stem = "imported_workflow"

        target_json_name = f"{clean_stem}.json"
        target_path = WORKFLOWS_DIR / target_json_name

        workflow_data: Dict[str, Any] = {}

        if filename.lower().endswith(".json"):
            try:
                parsed = json.loads(content_bytes.decode("utf-8"))
            except Exception as e:
                raise ValueError(f"Invalid JSON file: {e}")

            if isinstance(parsed, dict):
                if "extra" in parsed and isinstance(parsed["extra"], dict) and "prompt" in parsed["extra"]:
                    workflow_data = parsed["extra"]["prompt"]
                elif "prompt" in parsed and isinstance(parsed["prompt"], dict):
                    workflow_data = parsed["prompt"]
                else:
                    workflow_data = parsed
            else:
                raise ValueError("JSON content must be a workflow dictionary / graph.")

        elif filename.lower().endswith((".png", ".webp")):
            try:
                with Image.open(io.BytesIO(content_bytes)) as img:
                    raw_info = getattr(img, "info", {}) or {}
                    prompt_str = raw_info.get("prompt")
                    workflow_str = raw_info.get("workflow")

                    if prompt_str:
                        if isinstance(prompt_str, str):
                            workflow_data = json.loads(prompt_str)
                        else:
                            workflow_data = prompt_str
                    elif workflow_str:
                        if isinstance(workflow_str, str):
                            wf_dict = json.loads(workflow_str)
                        else:
                            wf_dict = workflow_str
                        if isinstance(wf_dict, dict) and "extra" in wf_dict and "prompt" in wf_dict["extra"]:
                            workflow_data = wf_dict["extra"]["prompt"]
                        else:
                            workflow_data = wf_dict
                    else:
                        raise ValueError("No embedded ComfyUI workflow or prompt found in PNG metadata.")
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
                async with httpx.AsyncClient(timeout=6.0) as client:
                    res = await client.post(
                        f"{self.comfy_url}/userdata/workflows/{clean_name}",
                        headers={"comfy-user": "default"},
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
                    p_node = prompt_graph[pos_id]
                    p_inputs = p_node.setdefault("inputs", {})
                    if "text" in p_inputs or ("text_g" not in p_inputs and "text_l" not in p_inputs):
                        p_inputs["text"] = positive_prompt
                    if "text_g" in p_inputs:
                        p_inputs["text_g"] = positive_prompt
                    if "text_l" in p_inputs:
                        p_inputs["text_l"] = positive_prompt
                    pos_injected = True

            neg_link = inputs.get("negative")
            if isinstance(neg_link, list) and len(neg_link) > 0:
                neg_id = str(neg_link[0])
                if neg_id in prompt_graph and "inputs" in prompt_graph[neg_id]:
                    n_node = prompt_graph[neg_id]
                    n_inputs = n_node.setdefault("inputs", {})
                    if "text" in n_inputs or ("text_g" not in n_inputs and "text_l" not in n_inputs):
                        n_inputs["text"] = negative_prompt
                    if "text_g" in n_inputs:
                        n_inputs["text_g"] = negative_prompt
                    if "text_l" in n_inputs:
                        n_inputs["text_l"] = negative_prompt
                    neg_injected = True

        # Fallback for prompt injection if not wired directly via KSampler
        if not pos_injected or not neg_injected:
            clip_nodes = []
            for nid, node in prompt_graph.items():
                if isinstance(node, dict) and ("CLIPTextEncode" in node.get("class_type", "") or "CLIP" in node.get("class_type", "")):
                    clip_nodes.append((nid, node))

            for nid, node in clip_nodes:
                title = str(node.get("_meta", {}).get("title", "")).lower()
                n_inputs = node.setdefault("inputs", {})
                if not pos_injected and ("positive" in title or "pos" in title):
                    if "text" in n_inputs or ("text_g" not in n_inputs and "text_l" not in n_inputs):
                        n_inputs["text"] = positive_prompt
                    if "text_g" in n_inputs:
                        n_inputs["text_g"] = positive_prompt
                    if "text_l" in n_inputs:
                        n_inputs["text_l"] = positive_prompt
                    pos_injected = True
                elif not neg_injected and ("negative" in title or "neg" in title):
                    if "text" in n_inputs or ("text_g" not in n_inputs and "text_l" not in n_inputs):
                        n_inputs["text"] = negative_prompt
                    if "text_g" in n_inputs:
                        n_inputs["text_g"] = negative_prompt
                    if "text_l" in n_inputs:
                        n_inputs["text_l"] = negative_prompt
                    neg_injected = True

            if not pos_injected and len(clip_nodes) > 0:
                c0_in = clip_nodes[0][1].setdefault("inputs", {})
                if "text" in c0_in or ("text_g" not in c0_in and "text_l" not in c0_in):
                    c0_in["text"] = positive_prompt
                if "text_g" in c0_in:
                    c0_in["text_g"] = positive_prompt
                if "text_l" in c0_in:
                    c0_in["text_l"] = positive_prompt
                pos_injected = True
            if not neg_injected and len(clip_nodes) > 1:
                c1_in = clip_nodes[1][1].setdefault("inputs", {})
                if "text" in c1_in or ("text_g" not in c1_in and "text_l" not in c1_in):
                    c1_in["text"] = negative_prompt
                if "text_g" in c1_in:
                    c1_in["text_g"] = negative_prompt
                if "text_l" in c1_in:
                    c1_in["text_l"] = negative_prompt
                neg_injected = True

        # Checkpoint Model injection
        if extra.get("ckpt_name"):
            for nid, node in prompt_graph.items():
                if isinstance(node, dict):
                    c_type = node.get("class_type", "")
                    inputs = node.setdefault("inputs", {})
                    if "CheckpointLoader" in c_type or ("ckpt_name" in inputs and "Loader" in c_type):
                        inputs["ckpt_name"] = str(extra["ckpt_name"])
                        resolved_params["ckpt_name"] = str(extra["ckpt_name"])
                        break

        # Dimensions injection (Latent creation, Aspect Ratio, SDXL Conditioning, and Primitive links)
        target_width = int(extra["width"]) if extra.get("width") else None
        target_height = int(extra["height"]) if extra.get("height") else None

        if target_width or target_height:
            def update_node_dim(node: Dict[str, Any], w: Optional[int], h: Optional[int]):
                n_inputs = node.setdefault("inputs", {})

                # If width input is a link [provider_id, slot], update upstream provider as well
                w_link = n_inputs.get("width")
                if isinstance(w_link, list) and len(w_link) > 0 and str(w_link[0]) in prompt_graph:
                    w_provider = prompt_graph[str(w_link[0])]
                    wp_in = w_provider.setdefault("inputs", {})
                    if "value" in wp_in and w:
                        wp_in["value"] = w
                    if "width" in wp_in and w:
                        wp_in["width"] = w

                # If height input is a link [provider_id, slot], update upstream provider as well
                h_link = n_inputs.get("height")
                if isinstance(h_link, list) and len(h_link) > 0 and str(h_link[0]) in prompt_graph:
                    h_provider = prompt_graph[str(h_link[0])]
                    hp_in = h_provider.setdefault("inputs", {})
                    if "value" in hp_in and h:
                        hp_in["value"] = h
                    if "height" in hp_in and h:
                        hp_in["height"] = h

                # Set dimensions on the node itself
                if w:
                    n_inputs["width"] = w
                if h:
                    n_inputs["height"] = h

                # Handle empty_latent_width / empty_latent_height (Efficient Loader)
                if "empty_latent_width" in n_inputs and w:
                    n_inputs["empty_latent_width"] = w
                if "empty_latent_height" in n_inputs and h:
                    n_inputs["empty_latent_height"] = h

            # 1. First priority: Trace directly from active KSampler's latent_image link
            if ksampler_node:
                latent_link = ksampler_node.get("inputs", {}).get("latent_image")
                if isinstance(latent_link, list) and len(latent_link) > 0:
                    curr_id = str(latent_link[0])
                    visited = set()
                    while curr_id in prompt_graph and curr_id not in visited:
                        visited.add(curr_id)
                        curr_node = prompt_graph[curr_id]
                        c_type = curr_node.get("class_type", "")
                        c_inputs = curr_node.get("inputs", {})

                        is_latent_source = (
                            "width" in c_inputs
                            or "empty_latent_width" in c_inputs
                            or any(k in c_type for k in ["EmptyLatent", "EmptySD", "LatentImage", "Aspect", "Size", "Resolution"])
                        )

                        if is_latent_source:
                            update_node_dim(curr_node, target_width, target_height)
                            break

                        # Walk upstream
                        upstream = c_inputs.get("samples") or c_inputs.get("latent") or c_inputs.get("latent_image")
                        if isinstance(upstream, list) and len(upstream) > 0:
                            curr_id = str(upstream[0])
                        else:
                            break

            # 2. General scan: Update all Empty Latent, Aspect Ratio, and Sizing nodes in the workflow
            for nid, node in prompt_graph.items():
                if not isinstance(node, dict):
                    continue
                c_type = node.get("class_type", "")
                c_inputs = node.setdefault("inputs", {})

                is_dim_node = (
                    c_type in [
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
                    ]
                    or any(k in c_type for k in ["EmptyLatent", "EmptySD", "Aspect"])
                    or ("width" in c_inputs and "height" in c_inputs and any(k in c_type for k in ["Latent", "Image", "Resolution"]))
                    or ("empty_latent_width" in c_inputs)
                )

                if is_dim_node:
                    update_node_dim(node, target_width, target_height)

                # 3. Synchronize SDXL conditioning dimensions (CLIPTextEncodeSDXL)
                if "CLIPTextEncodeSDXL" in c_type:
                    if target_width:
                        c_inputs["width"] = target_width
                        if "target_width" in c_inputs:
                            c_inputs["target_width"] = target_width
                    if target_height:
                        c_inputs["height"] = target_height
                        if "target_height" in c_inputs:
                            c_inputs["target_height"] = target_height

            if target_width:
                resolved_params["width"] = target_width
            if target_height:
                resolved_params["height"] = target_height

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
        """Interrupt current generation on ComfyUI and clear queued prompts."""
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                try:
                    await client.post(f"{self.comfy_url}/queue", json={"clear": True})
                except Exception:
                    pass
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
