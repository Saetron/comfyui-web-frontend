import asyncio
import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from PIL import Image

from app.config import OUTPUT_DIR, THUMBNAILS_DIR
from app.metadata_parser import extract_image_metadata

logger = logging.getLogger("gallery")

VALID_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class GalleryService:
    def __init__(self, output_dir: Path, thumbnails_dir: Path):
        self.output_dir = output_dir.resolve()
        self.thumbnails_dir = thumbnails_dir.resolve()
        self._images_cache: Dict[str, Dict[str, Any]] = {}
        self._watcher_task: Optional[asyncio.Task] = None
        self._broadcast_callbacks: List[Callable[[Dict[str, Any]], Any]] = []
        self._lock: Optional[asyncio.Lock] = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def register_broadcast_callback(self, cb: Callable[[Dict[str, Any]], Any]):
        self._broadcast_callbacks.append(cb)

    async def broadcast(self, event: Dict[str, Any]):
        for cb in self._broadcast_callbacks:
            try:
                res = cb(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning(f"Error in gallery broadcast callback: {e}")

    def safe_resolve(self, relative_path: str) -> Optional[Path]:
        """Safely resolve a relative path inside OUTPUT_DIR, preventing path traversal."""
        try:
            clean_str = relative_path.strip().replace("\\", "/")
            out_str = self.output_dir.as_posix()

            # If user or frontend provided absolute output path, strip the prefix
            if clean_str.startswith(out_str):
                clean_str = clean_str[len(out_str):]
            elif clean_str.lstrip("/").startswith("app/output"):
                clean_str = clean_str.lstrip("/")[len("app/output"):]
            elif clean_str.lstrip("/").startswith("output/"):
                clean_str = clean_str.lstrip("/")[len("output/"):]
            elif clean_str.lstrip("/").startswith("output"):
                clean_str = clean_str.lstrip("/")[len("output"):]

            rel = Path(clean_str.strip("/\\"))
            target = (self.output_dir / rel).resolve()
            if target.is_relative_to(self.output_dir):
                return target
        except Exception:
            pass
        return None

    def _get_thumbnail_path(self, file_path: Path) -> Path:
        """Get or generate thumbnail path for a given image file."""
        file_path = file_path.resolve()
        rel_path = file_path.relative_to(self.output_dir)
        # Use a stable hash of relative path to avoid deep nested folder issues
        hash_id = hashlib.sha1(str(rel_path).encode("utf-8")).hexdigest()
        thumb_filename = f"{hash_id}_{file_path.stem}.webp"
        return self.thumbnails_dir / thumb_filename

    def ensure_thumbnail(self, file_path: Path) -> Path:
        """Create a thumbnail if it doesn't already exist and return its path."""
        file_path = file_path.resolve()
        thumb_path = self._get_thumbnail_path(file_path)
        if thumb_path.exists() and thumb_path.stat().st_mtime >= file_path.stat().st_mtime:
            return thumb_path

        try:
            with Image.open(file_path) as img:
                # Convert RGBA to RGB with white background if saving as JPEG, or keep RGBA for WEBP
                img_copy = img.copy()
                img_copy.thumbnail((400, 400), Image.Resampling.LANCZOS)
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                img_copy.save(thumb_path, format="WEBP", quality=85)
                return thumb_path
        except Exception as e:
            logger.error(f"Failed to create thumbnail for {file_path}: {e}")
            return file_path  # Fall back to original file

    def _build_item_summary(self, file_path: Path) -> Dict[str, Any]:
        """Build summary item for gallery listing without full metadata extraction."""
        file_path = file_path.resolve()
        rel = file_path.relative_to(self.output_dir).as_posix()
        stat = file_path.stat()
        size_kb = stat.st_size / 1024
        size_human = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.2f} MB"

        return {
            "filename": file_path.name,
            "relative_path": rel,
            "subfolder": file_path.parent.relative_to(self.output_dir).as_posix() if file_path.parent != self.output_dir else "",
            "modified_timestamp": stat.st_mtime,
            "file_size": stat.st_size,
            "file_size_human": size_human,
            "extension": file_path.suffix.lower(),
        }

    async def scan(self):
        """Crawl the output directory on startup to index all images."""
        async with self.lock:
            found: Dict[str, Dict[str, Any]] = {}
            if self.output_dir.exists():
                for root, _, files in os.walk(self.output_dir):
                    for file in files:
                        p = Path(root) / file
                        if p.suffix.lower() in VALID_EXTENSIONS:
                            try:
                                summary = self._build_item_summary(p)
                                found[summary["relative_path"]] = summary
                            except Exception as e:
                                logger.warning(f"Could not read {p}: {e}")

            self._images_cache = found
            logger.info(f"Scanned output directory: found {len(self._images_cache)} images.")

    def list_images(self, query: str = "", sort: str = "newest", limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """List indexed images with search, sorting, and pagination."""
        items = list(self._images_cache.values())

        if query:
            q = query.lower()
            items = [item for item in items if q in item["filename"].lower() or q in item["relative_path"].lower()]

        if sort == "newest":
            items.sort(key=lambda x: x["modified_timestamp"], reverse=True)
        elif sort == "oldest":
            items.sort(key=lambda x: x["modified_timestamp"], reverse=False)
        elif sort == "name_asc":
            items.sort(key=lambda x: x["filename"].lower())
        elif sort == "name_desc":
            items.sort(key=lambda x: x["filename"].lower(), reverse=True)

        total = len(items)
        if limit is not None and limit > 0:
            paginated = items[offset : offset + limit]
            effective_limit = limit
        else:
            paginated = items[offset:]
            effective_limit = total

        return {
            "total": total,
            "offset": offset,
            "limit": effective_limit,
            "items": paginated,
        }

    def get_metadata(self, relative_path: str) -> Optional[Dict[str, Any]]:
        """Get full embedded generation metadata for an image."""
        file_path = self.safe_resolve(relative_path)
        if not file_path or not file_path.exists():
            return None
        return extract_image_metadata(file_path)

    async def delete_image(self, relative_path: str) -> bool:
        """Permanently delete an image and its thumbnail from the mounted volume."""
        file_path = self.safe_resolve(relative_path)
        if not file_path or not file_path.exists():
            return False

        try:
            # Delete thumbnail if present
            thumb_path = self._get_thumbnail_path(file_path)
            if thumb_path.exists():
                try:
                    thumb_path.unlink()
                except Exception:
                    pass

            rel_path = file_path.relative_to(self.output_dir)
            hash_id = hashlib.sha1(str(rel_path).encode("utf-8")).hexdigest()
            for extra in self.thumbnails_dir.glob(f"{hash_id}_*"):
                try:
                    extra.unlink()
                except Exception:
                    pass

            clean_rel = rel_path.as_posix()

            # Permanently delete file from output dir
            file_path.unlink()

            # Clean up empty parent folders within output_dir if needed
            parent = file_path.parent
            if parent != self.output_dir and parent.is_relative_to(self.output_dir):
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                except Exception:
                    pass

            # Update cache
            async with self.lock:
                self._images_cache.pop(clean_rel, None)
                self._images_cache.pop(relative_path, None)

            # Broadcast event to frontend
            await self.broadcast({
                "type": "gallery_item_deleted",
                "relative_path": clean_rel,
            })
            return True
        except Exception as e:
            logger.error(f"Failed to delete image {relative_path}: {e}")
            raise

    async def start_watcher(self):
        """Background file watcher monitoring the output directory for live updates."""
        try:
            from watchfiles import awatch, Change
        except ImportError:
            logger.warning("watchfiles not available, falling back to periodic scan.")
            return

        logger.info(f"Starting directory watcher for {self.output_dir}")
        try:
            async for changes in awatch(self.output_dir):
                for change, raw_path in changes:
                    p = Path(raw_path)
                    if p.suffix.lower() not in VALID_EXTENSIONS:
                        continue

                    rel_path = p.relative_to(self.output_dir).as_posix()

                    if change in (Change.added, Change.modified):
                        # Give file a brief moment to finish writing if newly created
                        await asyncio.sleep(0.3)
                        if p.exists():
                            try:
                                summary = self._build_item_summary(p)
                                async with self.lock:
                                    self._images_cache[rel_path] = summary
                                await self.broadcast({
                                    "type": "gallery_item_added",
                                    "item": summary,
                                })
                            except Exception as e:
                                logger.warning(f"Error handling file change for {p}: {e}")

                    elif change == Change.deleted:
                        async with self.lock:
                            self._images_cache.pop(rel_path, None)
                        await self.broadcast({
                            "type": "gallery_item_deleted",
                            "relative_path": rel_path,
                        })
        except asyncio.CancelledError:
            logger.info("Directory watcher task cancelled.")
        except Exception as e:
            logger.error(f"Directory watcher error: {e}")


# Global instance
gallery_service = GalleryService(OUTPUT_DIR, THUMBNAILS_DIR)
