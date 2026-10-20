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
