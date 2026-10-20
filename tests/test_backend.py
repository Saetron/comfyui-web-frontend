import asyncio
import tempfile
import shutil
from pathlib import Path
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from app.gallery_service import GalleryService
from app.main import app


@pytest.fixture
def temp_dirs():
    tmp_out = Path(tempfile.mkdtemp())
    tmp_thumb = Path(tempfile.mkdtemp())
    yield tmp_out, tmp_thumb
    shutil.rmtree(tmp_out, ignore_errors=True)
    shutil.rmtree(tmp_thumb, ignore_errors=True)


def create_dummy_image(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (100, 100), color="pink")
    img.save(path)


def test_safe_resolve_and_deletion(temp_dirs):
    tmp_out, tmp_thumb = temp_dirs
    svc = GalleryService(tmp_out, tmp_thumb)

    # Create dummy images
    img1 = (tmp_out / "test1.png").resolve()
    img2 = (tmp_out / "sub" / "test2.png").resolve()
    create_dummy_image(img1)
    create_dummy_image(img2)

    # Test safe_resolve variations
    assert svc.safe_resolve("test1.png") == img1
    assert svc.safe_resolve("output/test1.png") == img1
    assert svc.safe_resolve("/app/output/test1.png") == img1
    assert svc.safe_resolve("sub/test2.png") == img2
    assert svc.safe_resolve("output/sub/test2.png") == img2

    # Test thumbnail creation
    t1 = svc.ensure_thumbnail(img1)
    assert t1.exists()

    # Index
    asyncio.run(svc.scan())
    res = svc.list_images()
    assert res["total"] == 2

    # Test deletion
    deleted = asyncio.run(svc.delete_image("test1.png"))
    assert deleted is True
    assert not img1.exists()
    assert not t1.exists()

    res_after = svc.list_images()
    assert res_after["total"] == 1


def test_list_images_all(temp_dirs):
    tmp_out, tmp_thumb = temp_dirs
    svc = GalleryService(tmp_out, tmp_thumb)

    # Create 5 images
    for i in range(5):
        create_dummy_image(tmp_out / f"img_{i}.png")

    asyncio.run(svc.scan())

    # limit=2 should return 2
    res2 = svc.list_images(limit=2)
    assert len(res2["items"]) == 2
    assert res2["total"] == 5

    # limit=0 should return all 5
    res_all = svc.list_images(limit=0)
    assert len(res_all["items"]) == 5
    assert res_all["total"] == 5

    # limit=None should return all 5
    res_none = svc.list_images(limit=None)
    assert len(res_none["items"]) == 5


def test_api_images_endpoint():
    client = TestClient(app)
    response = client.get("/api/images?limit=0")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "items" in data


def test_api_delete_image_endpoint(temp_dirs):
    tmp_out, tmp_thumb = temp_dirs
    from app.gallery_service import gallery_service
    # Point gallery_service to temp dirs
    orig_out, orig_thumb = gallery_service.output_dir, gallery_service.thumbnails_dir
    try:
        gallery_service.output_dir = tmp_out.resolve()
        gallery_service.thumbnails_dir = tmp_thumb.resolve()

        test_img = (tmp_out / "del_test.png").resolve()
        create_dummy_image(test_img)
        thumb = gallery_service.ensure_thumbnail(test_img)
        assert thumb.exists()

        asyncio.run(gallery_service.scan())
        assert gallery_service.list_images()["total"] == 1

        client = TestClient(app)
        res = client.delete("/api/images/del_test.png")
        assert res.status_code == 200
        assert res.json()["success"] is True

        assert not test_img.exists()
        assert not thumb.exists()
        assert gallery_service.list_images()["total"] == 0
    finally:
        gallery_service.output_dir = orig_out
        gallery_service.thumbnails_dir = orig_thumb


def test_format_prompt_newlines():
    from app.main import _format_prompt_newlines

    assert _format_prompt_newlines("1girl, solo\nmasterpiece\nbest quality") == "1girl, solo, masterpiece, best quality"
    assert _format_prompt_newlines("1girl, solo,\nmasterpiece,\nbest quality") == "1girl, solo, masterpiece, best quality"
    assert _format_prompt_newlines("1girl, solo , \n , masterpiece") == "1girl, solo, masterpiece"
    assert _format_prompt_newlines("1girl, solo\n\n\nmasterpiece") == "1girl, solo, masterpiece"
    assert _format_prompt_newlines("1girl,,solo, ,masterpiece") == "1girl, solo, masterpiece"
    assert _format_prompt_newlines("1girl, solo,   ,   masterpiece") == "1girl, solo, masterpiece"
    assert _format_prompt_newlines("masterpiece\r\nbest quality\r\n\r\n8k") == "masterpiece, best quality, 8k"


def test_api_images_all_endpoint():
    client = TestClient(app)
    response = client.get("/api/images/all")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "items" in data


def test_tag_service_prefix_word_and_alias_search(monkeypatch):
    import app.tag_service as ts_mod
    from app.tag_service import TagService

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write("eyes_closed,0,1000,closed_eyes\n")
        f.write("blue_eyes,0,50000,azure_eyes\n")
        f.write("red_eyes,0,30000,crimson_eyes\n")
        f.write("hatsune_miku,4,80000,miku\n")
        csv_path = Path(f.name)

    try:
        monkeypatch.setattr(ts_mod, "DEFAULT_TAGS_FILE", csv_path)
        monkeypatch.setattr(ts_mod, "USER_TAGS_DIR", Path("/nonexistent_user_tags_dir"))
        svc = TagService()
        svc.load_tags()

        # Searching "eyes" should return both prefix match ("eyes_closed") and word-boundary matches ("blue_eyes", "red_eyes")
        results = svc.search("eyes", limit=10)
        names = [r["name"] for r in results]
        assert "eyes_closed" in names
        assert "blue_eyes" in names
        assert "red_eyes" in names
        # Exact prefix match ("eyes_closed") should rank before word-boundary matches
        assert names[0] == "eyes_closed"

        # Searching alias "miku" should find "hatsune_miku"
        miku_res = svc.search("miku", limit=5)
        assert any(r["name"] == "hatsune_miku" for r in miku_res)

        # Searching alias "azure_eyes" should find "blue_eyes"
        alias_res = svc.search("azure", limit=5)
        assert any(r["name"] == "blue_eyes" for r in alias_res)
    finally:
        csv_path.unlink(missing_ok=True)


def test_metadata_parser_and_gallery_metadata_cache(temp_dirs):
    from app.metadata_parser import format_file_size, parse_comfyui_prompt_graph, parse_a1111_parameters

    assert format_file_size(512) == "0.5 KB"
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(5 * 1024 * 1024) == "5.00 MB"

    graph = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 28,
                "cfg": 6.5,
                "sampler_name": "euler_ancestral",
                "scheduler": "karras",
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "anime_v1.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl, masterpiece"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres, bad anatomy"}},
    }
    parsed = parse_comfyui_prompt_graph(graph)
    assert parsed["positive_prompt"] == "1girl, masterpiece"
    assert parsed["negative_prompt"] == "lowres, bad anatomy"
    assert parsed["seed"] == 42
    assert parsed["steps"] == 28
    assert parsed["cfg"] == 6.5
    assert parsed["width"] == 832
    assert parsed["height"] == 1216
    assert parsed["model"] == "anime_v1.safetensors"

    a1111 = parse_a1111_parameters(
        "1girl, solo\nNegative prompt: bad hands\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 999, Size: 512x768, Model: model_v2"
    )
    assert a1111["positive_prompt"] == "1girl, solo"
    assert a1111["negative_prompt"] == "bad hands"
    assert a1111["seed"] == 999
    assert a1111["size"] == "512x768"

    # Gallery metadata cache verification
    tmp_out, tmp_thumb = temp_dirs
    svc = GalleryService(tmp_out, tmp_thumb)
    img_path = (tmp_out / "cached.png").resolve()
    create_dummy_image(img_path)
    asyncio.run(svc.scan())
    assert svc.total_images == 1

    meta1 = svc.get_metadata("cached.png")
    meta2 = svc.get_metadata("cached.png")
    assert meta1 is meta2  # Served directly from mtime-validated cache

    asyncio.run(svc.delete_image("cached.png"))
    assert svc.total_images == 0
    assert "cached.png" not in svc._metadata_cache


def test_comfy_service_inject_and_extract(monkeypatch, tmp_path):
    import app.comfy_service as cs_mod
    from app.comfy_service import ComfyService
    from app.metadata_parser import parse_comfyui_prompt_graph

    monkeypatch.setattr(cs_mod, "WORKFLOW_FILE", tmp_path / "workflow_api.json")
    svc = ComfyService("http://localhost:8188")
    workflow = svc.load_workflow_template()
    params = parse_comfyui_prompt_graph(workflow)
    assert "steps" in params
    assert "cfg" in params

    injected, resolved = svc.inject_workflow(
        workflow=workflow,
        positive_prompt="1girl, sakura",
        negative_prompt="bad quality",
        seed=123456,
        randomize_seed=False,
        extra_params={"steps": 30, "cfg": 8.0, "width": 1024, "height": 1024},
    )
    assert resolved["seed"] == 123456
    assert resolved["steps"] == 30
    assert resolved["cfg"] == 8.0
    assert resolved["width"] == 1024
    assert resolved["height"] == 1024


def test_version_and_config():
    from app import __version__
    assert __version__ == "1.1.1"

    client = TestClient(app)
    cfg = client.get("/api/config").json()
    assert cfg["version"] == "1.1.1"

    # Verify dynamic version injection in root index.html
    resp = client.get("/")
    assert resp.status_code == 200
    assert "v1.1.1 🌸" in resp.text
    assert "{{VERSION}}" not in resp.text
    assert "v1.2" not in resp.text
