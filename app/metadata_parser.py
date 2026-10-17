import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from PIL import Image, ExifTags


def parse_a1111_parameters(text: str) -> Dict[str, Any]:
    """Parse Automatic1111 / WebUI style parameters string."""
    result: Dict[str, Any] = {}
    lines = text.strip().split("\n")
    if not lines:
        return result

    # Find the Negative prompt line and params line
    prompt_lines = []
    neg_lines = []
    params_line = ""
    current_section = "prompt"

    for line in lines:
        if line.startswith("Negative prompt:"):
            current_section = "negative"
            neg_lines.append(line.replace("Negative prompt:", "").strip())
        elif current_section == "prompt" and not line.startswith("Steps:"):
            prompt_lines.append(line)
        elif current_section == "negative" and not line.startswith("Steps:"):
            neg_lines.append(line)
        elif line.startswith("Steps:") or "Steps:" in line:
            params_line = line
            break

    result["positive_prompt"] = "\n".join(prompt_lines).strip()
    result["negative_prompt"] = "\n".join(neg_lines).strip()

    if params_line:
        # e.g., Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 12345, Size: 512x512, Model hash: ...
        parts = [p.strip() for p in params_line.split(",") if ":" in p]
        for part in parts:
            key, val = part.split(":", 1)
            key = key.strip()
            val = val.strip()
            if key == "Steps":
                result["steps"] = int(val) if val.isdigit() else val
            elif key == "Sampler":
                result["sampler_name"] = val
            elif key == "CFG scale":
                try:
                    result["cfg"] = float(val)
                except ValueError:
                    result["cfg"] = val
            elif key == "Seed":
                try:
                    result["seed"] = int(val)
                except ValueError:
                    result["seed"] = val
            elif key == "Size":
                result["size"] = val
            elif key == "Model":
                result["model"] = val

    return result


def parse_comfyui_prompt_graph(prompt_graph: Dict[str, Any]) -> Dict[str, Any]:
    """Extract standard parameters from ComfyUI API prompt JSON graph or UI workflow format."""
    params: Dict[str, Any] = {}
    if not isinstance(prompt_graph, dict):
        return params

    # Unwrap extra.prompt or prompt if nested
    if "extra" in prompt_graph and isinstance(prompt_graph["extra"], dict) and "prompt" in prompt_graph["extra"]:
        return parse_comfyui_prompt_graph(prompt_graph["extra"]["prompt"])
    if "prompt" in prompt_graph and isinstance(prompt_graph["prompt"], dict):
        return parse_comfyui_prompt_graph(prompt_graph["prompt"])

    # Handle ComfyUI UI workflow format: {"nodes": [...], "links": [...]}
    if "nodes" in prompt_graph and isinstance(prompt_graph["nodes"], list):
        for node in prompt_graph["nodes"]:
            if not isinstance(node, dict):
                continue
            ntype = str(node.get("type", ""))
            widgets = node.get("widgets_values", [])

            # KSampler node
            if "KSampler" in ntype:
                if isinstance(widgets, list) and len(widgets) >= 5:
                    # Typical ComfyUI KSampler widgets: [seed, control, steps, cfg, sampler_name, scheduler, denoise]
                    try:
                        if "seed" not in params:
                            params["seed"] = widgets[0]
                        if len(widgets) >= 3:
                            params["steps"] = int(widgets[2])
                        if len(widgets) >= 4:
                            params["cfg"] = float(widgets[3])
                        if len(widgets) >= 5:
                            params["sampler_name"] = str(widgets[4])
                        if len(widgets) >= 6:
                            params["scheduler"] = str(widgets[5])
                    except (ValueError, IndexError):
                        pass
                elif isinstance(widgets, dict):
                    if "steps" in widgets: params["steps"] = int(widgets["steps"])
                    if "cfg" in widgets: params["cfg"] = float(widgets["cfg"])
                    if "sampler_name" in widgets: params["sampler_name"] = str(widgets["sampler_name"])
                    if "scheduler" in widgets: params["scheduler"] = str(widgets["scheduler"])
                    if "seed" in widgets: params["seed"] = widgets["seed"]

            # Checkpoint Loader
            elif "CheckpointLoader" in ntype or "Loader" in ntype:
                if isinstance(widgets, list) and len(widgets) > 0 and isinstance(widgets[0], str):
                    params["model"] = str(widgets[0])
                elif isinstance(widgets, dict) and "ckpt_name" in widgets:
                    params["model"] = str(widgets["ckpt_name"])

            # Empty Latent Image
            elif "EmptyLatentImage" in ntype or ("Latent" in ntype and isinstance(widgets, list) and len(widgets) >= 2):
                if isinstance(widgets, list) and len(widgets) >= 2:
                    try:
                        params["width"] = int(widgets[0])
                        params["height"] = int(widgets[1])
                        params["generation_resolution"] = f"{params['width']}x{params['height']}"
                    except (ValueError, IndexError):
                        pass
                elif isinstance(widgets, dict) and "width" in widgets and "height" in widgets:
                    params["width"] = int(widgets["width"])
                    params["height"] = int(widgets["height"])
                    params["generation_resolution"] = f"{params['width']}x{params['height']}"

            # CLIP Text Encode
            elif "CLIPTextEncode" in ntype:
                title = str(node.get("title", "")).lower()
                text = ""
                if isinstance(widgets, list) and len(widgets) > 0 and isinstance(widgets[0], str):
                    text = str(widgets[0]).strip()
                elif isinstance(widgets, dict) and "text" in widgets:
                    text = str(widgets["text"]).strip()

                if text:
                    if "positive_prompt" not in params and ("pos" in title or "positive" in title):
                        params["positive_prompt"] = text
                    elif "negative_prompt" not in params and ("neg" in title or "negative" in title):
                        params["negative_prompt"] = text
                    elif "positive_prompt" not in params:
                        params["positive_prompt"] = text
                    elif "negative_prompt" not in params:
                        params["negative_prompt"] = text

        return params

    # Handle standard ComfyUI API graph format: {"1": {"class_type": "...", "inputs": {...}}, ...}
    ksampler_node = None
    checkpoint_node = None
    latent_node = None
    clip_nodes = []

    # Find relevant nodes
    for node_id, node in prompt_graph.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})
        if "KSampler" in class_type:
            ksampler_node = node
        elif "CheckpointLoader" in class_type or ("ckpt_name" in inputs and "Loader" in class_type):
            checkpoint_node = node
        elif (
            "EmptyLatentImage" in class_type
            or any(k in class_type for k in ["EmptyLatent", "EmptySD", "Aspect"])
            or ("width" in inputs and "height" in inputs and any(k in class_type for k in ["Latent", "Image", "Resolution"]))
            or "empty_latent_width" in inputs
        ):
            latent_node = node
        elif "CLIPTextEncode" in class_type or "CLIP" in class_type:
            clip_nodes.append((node_id, node))

    # Prioritize active KSampler's latent generator if available
    if ksampler_node:
        latent_link = ksampler_node.get("inputs", {}).get("latent_image")
        if isinstance(latent_link, list) and len(latent_link) > 0:
            curr_id = str(latent_link[0])
            visited = set()
            while curr_id in prompt_graph and curr_id not in visited:
                visited.add(curr_id)
                curr_node = prompt_graph[curr_id]
                c_inputs = curr_node.get("inputs", {})
                c_type = curr_node.get("class_type", "")
                if "width" in c_inputs or "empty_latent_width" in c_inputs or any(k in c_type for k in ["EmptyLatent", "EmptySD", "Latent", "Aspect"]):
                    latent_node = curr_node
                    break
                upstream = c_inputs.get("samples") or c_inputs.get("latent") or c_inputs.get("latent_image")
                if isinstance(upstream, list) and len(upstream) > 0:
                    curr_id = str(upstream[0])
                else:
                    break

    # Parse KSampler details
    if ksampler_node:
        inputs = ksampler_node.get("inputs", {})
        if "seed" in inputs:
            params["seed"] = inputs["seed"]
        elif "noise_seed" in inputs:
            params["seed"] = inputs["noise_seed"]

        if "steps" in inputs:
            try:
                params["steps"] = int(inputs["steps"])
            except (ValueError, TypeError):
                params["steps"] = inputs["steps"]
        if "cfg" in inputs:
            try:
                params["cfg"] = float(inputs["cfg"])
            except (ValueError, TypeError):
                params["cfg"] = inputs["cfg"]
        if "sampler_name" in inputs:
            params["sampler_name"] = str(inputs["sampler_name"])
        if "scheduler" in inputs:
            params["scheduler"] = str(inputs["scheduler"])
        if "denoise" in inputs:
            params["denoise"] = inputs["denoise"]

        # Follow positive and negative connections
        pos_link = inputs.get("positive")
        neg_link = inputs.get("negative")

        if isinstance(pos_link, list) and len(pos_link) > 0:
            pos_node_id = str(pos_link[0])
            if pos_node_id in prompt_graph:
                pos_node = prompt_graph[pos_node_id]
                p_inputs = pos_node.get("inputs", {})
                params["positive_prompt"] = p_inputs.get("text") or p_inputs.get("text_g") or p_inputs.get("text_l", "")

        if isinstance(neg_link, list) and len(neg_link) > 0:
            neg_node_id = str(neg_link[0])
            if neg_node_id in prompt_graph:
                neg_node = prompt_graph[neg_node_id]
                n_inputs = neg_node.get("inputs", {})
                params["negative_prompt"] = n_inputs.get("text") or n_inputs.get("text_g") or n_inputs.get("text_l", "")

    # Fallback for prompts if not found through KSampler link
    if "positive_prompt" not in params or "negative_prompt" not in params:
        for node_id, node in clip_nodes:
            title = str(node.get("_meta", {}).get("title", "")).lower()
            text = node.get("inputs", {}).get("text") or node.get("inputs", {}).get("text_g") or node.get("inputs", {}).get("text_l", "")
            if "positive_prompt" not in params and ("positive" in title or "pos" in title):
                params["positive_prompt"] = text
            elif "negative_prompt" not in params and ("negative" in title or "neg" in title):
                params["negative_prompt"] = text

        # If still missing and we have clip nodes, take first as positive, second as negative
        if "positive_prompt" not in params and len(clip_nodes) > 0:
            c0_inputs = clip_nodes[0][1].get("inputs", {})
            params["positive_prompt"] = c0_inputs.get("text") or c0_inputs.get("text_g") or c0_inputs.get("text_l", "")
        if "negative_prompt" not in params and len(clip_nodes) > 1:
            c1_inputs = clip_nodes[1][1].get("inputs", {})
            params["negative_prompt"] = c1_inputs.get("text") or c1_inputs.get("text_g") or c1_inputs.get("text_l", "")

    # Checkpoint model
    if checkpoint_node:
        ckpt_name = checkpoint_node.get("inputs", {}).get("ckpt_name")
        if ckpt_name:
            params["model"] = str(ckpt_name)

    # Resolution
    if latent_node:
        inputs = latent_node.get("inputs", {})
        width = inputs.get("width") or inputs.get("empty_latent_width")
        height = inputs.get("height") or inputs.get("empty_latent_height")

        # Resolve provider node if width/height is a link [provider_id, slot]
        if isinstance(width, list) and len(width) > 0 and str(width[0]) in prompt_graph:
            w_prov = prompt_graph[str(width[0])].get("inputs", {})
            width = w_prov.get("value") or w_prov.get("width")
        if isinstance(height, list) and len(height) > 0 and str(height[0]) in prompt_graph:
            h_prov = prompt_graph[str(height[0])].get("inputs", {})
            height = h_prov.get("value") or h_prov.get("height")

        if width and height:
            try:
                params["width"] = int(width)
                params["height"] = int(height)
                params["generation_resolution"] = f"{params['width']}x{params['height']}"
            except (ValueError, TypeError):
                pass

    return params


def extract_image_metadata(file_path: Path) -> Dict[str, Any]:
    """
    Extract embedded generation metadata and file stats from an image.
    Supports ComfyUI PNG text chunks, A1111 parameters, EXIF UserComment, etc.
    """
    # Calculate path relative to OUTPUT_DIR if applicable
    rel = file_path.name
    try:
        from app.config import OUTPUT_DIR
        resolved_file = file_path.resolve()
        if resolved_file.is_relative_to(OUTPUT_DIR):
            rel = resolved_file.relative_to(OUTPUT_DIR).as_posix()
    except Exception:
        pass

    metadata: Dict[str, Any] = {
        "filename": file_path.name,
        "relative_path": rel,
        "file_size": 0,
        "file_size_human": "0 KB",
        "modified_at": None,
        "modified_timestamp": 0,
        "dimensions": None,
        "format": None,
        "parsed": {
            "positive_prompt": "",
            "negative_prompt": "",
            "seed": None,
            "steps": None,
            "cfg": None,
            "sampler_name": None,
            "scheduler": None,
            "model": None,
            "width": None,
            "height": None,
        },
        "raw": {},
        "has_generation_metadata": False,
    }

    try:
        stat = file_path.stat()
        metadata["file_size"] = stat.st_size
        metadata["modified_timestamp"] = stat.st_mtime
        metadata["modified_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat()

        # Format human-readable file size
        size_kb = stat.st_size / 1024
        if size_kb < 1024:
            metadata["file_size_human"] = f"{size_kb:.1f} KB"
        else:
            metadata["file_size_human"] = f"{size_kb / 1024:.2f} MB"
    except Exception as e:
        pass

    try:
        with Image.open(file_path) as img:
            metadata["format"] = img.format
            metadata["dimensions"] = f"{img.width}x{img.height}"
            metadata["parsed"]["width"] = img.width
            metadata["parsed"]["height"] = img.height

            # 1. ComfyUI / PNG info dictionary
            raw_info: Dict[str, Any] = {}
            if hasattr(img, "info") and img.info:
                for k, v in img.info.items():
                    if isinstance(v, (str, int, float, bool, list, dict)):
                        raw_info[k] = v
                    elif isinstance(v, bytes):
                        try:
                            raw_info[k] = v.decode("utf-8", errors="ignore")
                        except Exception:
                            raw_info[k] = f"<bytes: {len(v)}>"

            # Check for ComfyUI 'prompt' chunk
            prompt_str = raw_info.get("prompt")
            if prompt_str:
                try:
                    if isinstance(prompt_str, str):
                        prompt_graph = json.loads(prompt_str)
                    else:
                        prompt_graph = prompt_str
                    metadata["raw"]["prompt"] = prompt_graph
                    parsed_comfy = parse_comfyui_prompt_graph(prompt_graph)
                    metadata["parsed"].update(parsed_comfy)
                    metadata["has_generation_metadata"] = True
                except Exception:
                    pass

            # Check for ComfyUI 'workflow' chunk
            workflow_str = raw_info.get("workflow")
            if workflow_str:
                try:
                    if isinstance(workflow_str, str):
                        metadata["raw"]["workflow"] = json.loads(workflow_str)
                    else:
                        metadata["raw"]["workflow"] = workflow_str
                except Exception:
                    pass

            # Check for A1111 / WebUI 'parameters' chunk
            params_str = raw_info.get("parameters")
            if params_str and isinstance(params_str, str):
                metadata["raw"]["parameters"] = params_str
                a1111_parsed = parse_a1111_parameters(params_str)
                for k, v in a1111_parsed.items():
                    if not metadata["parsed"].get(k):
                        metadata["parsed"][k] = v
                metadata["has_generation_metadata"] = True

            # Check EXIF for UserComment or ImageDescription if not found
            if not metadata["has_generation_metadata"] and hasattr(img, "getexif"):
                exif = img.getexif()
                if exif:
                    for tag_id, value in exif.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        if tag_name in ("UserComment", "ImageDescription") and isinstance(value, str):
                            # Try parsing as JSON first
                            try:
                                parsed_json = json.loads(value)
                                if isinstance(parsed_json, dict):
                                    metadata["raw"]["exif_json"] = parsed_json
                                    parsed_comfy = parse_comfyui_prompt_graph(parsed_json)
                                    metadata["parsed"].update(parsed_comfy)
                                    metadata["has_generation_metadata"] = True
                            except Exception:
                                a1111_parsed = parse_a1111_parameters(value)
                                if a1111_parsed.get("positive_prompt"):
                                    metadata["parsed"].update(a1111_parsed)
                                    metadata["has_generation_metadata"] = True

            # Save any other raw text chunks
            for k, v in raw_info.items():
                if k not in ("prompt", "workflow", "parameters") and k not in metadata["raw"]:
                    metadata["raw"][k] = v

    except Exception as e:
        metadata["error"] = str(e)

    return metadata
