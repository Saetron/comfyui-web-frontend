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
    """Extract standard parameters from ComfyUI API prompt JSON graph."""
    params: Dict[str, Any] = {}
    if not isinstance(prompt_graph, dict):
        return params

    ksampler_node = None
    checkpoint_node = None
    latent_node = None
    clip_nodes = []

    # Find relevant nodes
    for node_id, node in prompt_graph.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        if "KSampler" in class_type:
            ksampler_node = node
        elif "CheckpointLoader" in class_type:
            checkpoint_node = node
        elif "EmptyLatentImage" in class_type:
            latent_node = node
        elif class_type == "CLIPTextEncode":
            clip_nodes.append((node_id, node))

    # Parse KSampler details
    if ksampler_node:
        inputs = ksampler_node.get("inputs", {})
        if "seed" in inputs:
            params["seed"] = inputs["seed"]
        elif "noise_seed" in inputs:
            params["seed"] = inputs["noise_seed"]

        if "steps" in inputs:
            params["steps"] = inputs["steps"]
        if "cfg" in inputs:
            params["cfg"] = inputs["cfg"]
        if "sampler_name" in inputs:
            params["sampler_name"] = inputs["sampler_name"]
        if "scheduler" in inputs:
            params["scheduler"] = inputs["scheduler"]
        if "denoise" in inputs:
            params["denoise"] = inputs["denoise"]

        # Follow positive and negative connections
        pos_link = inputs.get("positive")
        neg_link = inputs.get("negative")

        if isinstance(pos_link, list) and len(pos_link) > 0:
            pos_node_id = str(pos_link[0])
            if pos_node_id in prompt_graph:
                pos_node = prompt_graph[pos_node_id]
                params["positive_prompt"] = pos_node.get("inputs", {}).get("text", "")

        if isinstance(neg_link, list) and len(neg_link) > 0:
            neg_node_id = str(neg_link[0])
            if neg_node_id in prompt_graph:
                neg_node = prompt_graph[neg_node_id]
                params["negative_prompt"] = neg_node.get("inputs", {}).get("text", "")

    # Fallback for prompts if not found through KSampler link
    if "positive_prompt" not in params or "negative_prompt" not in params:
        for node_id, node in clip_nodes:
            title = str(node.get("_meta", {}).get("title", "")).lower()
            text = node.get("inputs", {}).get("text", "")
            if "positive_prompt" not in params and ("positive" in title or "pos" in title):
                params["positive_prompt"] = text
            elif "negative_prompt" not in params and ("negative" in title or "neg" in title):
                params["negative_prompt"] = text

        # If still missing and we have clip nodes, take first as positive, second as negative
        if "positive_prompt" not in params and len(clip_nodes) > 0:
            params["positive_prompt"] = clip_nodes[0][1].get("inputs", {}).get("text", "")
        if "negative_prompt" not in params and len(clip_nodes) > 1:
            params["negative_prompt"] = clip_nodes[1][1].get("inputs", {}).get("text", "")

    # Checkpoint model
    if checkpoint_node:
        ckpt_name = checkpoint_node.get("inputs", {}).get("ckpt_name")
        if ckpt_name:
            params["model"] = ckpt_name

    # Resolution
    if latent_node:
        inputs = latent_node.get("inputs", {})
        width = inputs.get("width")
        height = inputs.get("height")
        if width and height:
            params["generation_resolution"] = f"{width}x{height}"
            params["width"] = width
            params["height"] = height

    return params


def extract_image_metadata(file_path: Path) -> Dict[str, Any]:
    """
    Extract embedded generation metadata and file stats from an image.
    Supports ComfyUI PNG text chunks, A1111 parameters, EXIF UserComment, etc.
    """
    metadata: Dict[str, Any] = {
        "filename": file_path.name,
        "relative_path": str(file_path.as_posix()),
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
