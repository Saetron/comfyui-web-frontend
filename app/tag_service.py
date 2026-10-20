import csv
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import DEFAULT_TAGS_FILE, USER_TAGS_DIR

logger = logging.getLogger("tag_service")

CATEGORY_NAMES = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "meta",
}

CATEGORY_COLORS = {
    "general": "text-sky-400 bg-sky-950/70 border-sky-800/80",
    "artist": "text-rose-400 bg-rose-950/70 border-rose-800/80",
    "copyright": "text-purple-400 bg-purple-950/70 border-purple-800/80",
    "character": "text-emerald-400 bg-emerald-950/70 border-emerald-800/80",
    "meta": "text-amber-400 bg-amber-950/70 border-amber-800/80",
}


def format_count(count: int) -> str:
    """Format post counts cleanly (e.g. 1.2M, 450k, 12)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


class TagItem:
    __slots__ = (
        "name",
        "name_lower",
        "name_normalized",
        "category",
        "category_name",
        "count",
        "count_formatted",
        "aliases",
        "aliases_list",
        "_dict",
    )

    def __init__(self, name: str, category: int, count: int, aliases: str = ""):
        self.name = name
        self.name_lower = name.lower()
        self.name_normalized = self.name_lower.replace("_", " ")
        self.category = category
        self.category_name = CATEGORY_NAMES.get(category, "general")
        self.count = count
        self.count_formatted = format_count(count)
        self.aliases = aliases
        self.aliases_list = tuple(a.strip().lower() for a in aliases.split(",") if a.strip()) if aliases else ()
        self._dict: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        if self._dict is None:
            self._dict = {
                "name": self.name,
                "category": self.category,
                "category_name": self.category_name,
                "count": self.count,
                "count_formatted": self.count_formatted,
                "aliases": self.aliases,
            }
        return self._dict


class TagService:
    def __init__(self):
        self.tags: List[TagItem] = []
        self._loaded = False
        self._prefix_index: Dict[str, List[int]] = {}
        self._word_index: Dict[str, List[int]] = {}

    def load_tags(self):
        """Load default Danbooru tags and any custom user CSV tags."""
        if self._loaded:
            return

        all_tags: List[TagItem] = []
        seen_names: Set[str] = set()

        files_to_load: List[Path] = []
        if DEFAULT_TAGS_FILE.exists():
            files_to_load.append(DEFAULT_TAGS_FILE)

        # Check user custom tag CSVs in USER_TAGS_DIR
        if USER_TAGS_DIR.exists():
            for p in sorted(USER_TAGS_DIR.glob("*.csv")):
                if p not in files_to_load:
                    files_to_load.append(p)

        for csv_path in files_to_load:
            logger.info(f"Loading Danbooru tags from {csv_path}...")
            try:
                with open(csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if not row or len(row) < 3:
                            continue
                        name = row[0].strip()
                        if not name or name in seen_names:
                            continue
                        try:
                            cat = int(row[1]) if row[1].isdigit() else 0
                            count = int(row[2]) if row[2].isdigit() else 0
                            aliases = row[3].strip() if len(row) > 3 else ""
                        except Exception:
                            continue

                        all_tags.append(TagItem(name, cat, count, aliases))
                        seen_names.add(name)
            except Exception as e:
                logger.error(f"Error loading tags from {csv_path}: {e}")

        # Sort tags by post count descending
        all_tags.sort(key=lambda t: t.count, reverse=True)
        self.tags = all_tags

        # Build 2-character prefix and word/alias indices for fast lookup
        prefix_idx: Dict[str, List[int]] = {}
        word_idx: Dict[str, List[int]] = {}

        for idx, tag in enumerate(self.tags):
            name_low = tag.name_lower
            if len(name_low) >= 2:
                p2 = name_low[:2]
                prefix_idx.setdefault(p2, []).append(idx)
                p2_norm = tag.name_normalized[:2]
                if p2_norm != p2:
                    prefix_idx.setdefault(p2_norm, []).append(idx)

            # Index subsequent word tokens and aliases by their 2-char prefixes
            word_prefixes: Set[str] = set()
            if "_" in name_low:
                for part in name_low.split("_")[1:]:
                    if len(part) >= 2:
                        word_prefixes.add(part[:2])
            for alias in tag.aliases_list:
                if len(alias) >= 2:
                    word_prefixes.add(alias[:2])
                if "_" in alias:
                    for apart in alias.split("_")[1:]:
                        if len(apart) >= 2:
                            word_prefixes.add(apart[:2])

            for wp in word_prefixes:
                word_idx.setdefault(wp, []).append(idx)

        self._prefix_index = prefix_idx
        self._word_index = word_idx
        self._loaded = True
        logger.info(f"Danbooru tag service initialized with {len(self.tags):,} tags.")

    def search(self, query: str, category: Optional[int] = None, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Fast Danbooru tag search with multi-tier ranking:
        1. Exact match
        2. Exact prefix match
        3. Word-start match (e.g. after underscore)
        4. Substring in tag name
        5. Alias match
        """
        if not self._loaded:
            self.load_tags()

        q = query.strip().lower()
        if not q:
            if category is not None:
                return [t.to_dict() for t in self.tags if t.category == category][:limit]
            return [t.to_dict() for t in self.tags[:limit]]

        q_norm = q.replace("_", " ")
        q_under = q.replace(" ", "_")

        exact_matches: List[TagItem] = []
        prefix_matches: List[TagItem] = []
        word_start_matches: List[TagItem] = []
        substring_matches: List[TagItem] = []
        alias_matches: List[TagItem] = []

        if len(q_under) >= 2:
            p2 = q_under[:2]
            checked_indices: Set[int] = set()

            # 1 & 2. Check prefix index for exact and prefix matches
            for idx in self._prefix_index.get(p2, ()):
                tag = self.tags[idx]
                if category is not None and tag.category != category:
                    continue
                checked_indices.add(idx)
                name = tag.name_lower
                norm = tag.name_normalized
                if name == q_under or norm == q_norm:
                    exact_matches.append(tag)
                elif name.startswith(q_under) or norm.startswith(q_norm):
                    prefix_matches.append(tag)
                    if len(exact_matches) + len(prefix_matches) >= limit * 2:
                        break

            # 3 & 5. Check word/alias index for word-boundary and alias matches
            if len(exact_matches) + len(prefix_matches) < limit * 2:
                word_target_u = f"_{q_under}"
                word_target_n = f" {q_norm}"
                for idx in self._word_index.get(p2, ()):
                    if idx in checked_indices:
                        continue
                    tag = self.tags[idx]
                    if category is not None and tag.category != category:
                        continue
                    checked_indices.add(idx)
                    name = tag.name_lower
                    norm = tag.name_normalized
                    if word_target_u in name or word_target_n in norm:
                        word_start_matches.append(tag)
                    else:
                        for alias in tag.aliases_list:
                            if alias == q_under or alias == q_norm or alias.startswith(q_under) or f"_{q_under}" in alias:
                                alias_matches.append(tag)
                                break
                    if len(exact_matches) + len(prefix_matches) + len(word_start_matches) >= limit * 3:
                        break

            # 4. Fallback substring scan only if indexed results are below limit
            total_indexed = len(exact_matches) + len(prefix_matches) + len(word_start_matches) + len(alias_matches)
            if total_indexed < limit:
                needed = limit - total_indexed
                for idx, tag in enumerate(self.tags):
                    if idx in checked_indices:
                        continue
                    if category is not None and tag.category != category:
                        continue
                    if q_under in tag.name_lower or q_norm in tag.name_normalized:
                        substring_matches.append(tag)
                        if len(substring_matches) >= needed:
                            break
                    else:
                        for alias in tag.aliases_list:
                            if q_under in alias:
                                alias_matches.append(tag)
                                break
        else:
            # Single-character query
            word_target_u = f"_{q_under}"
            word_target_n = f" {q_norm}"
            for tag in self.tags:
                if category is not None and tag.category != category:
                    continue
                name = tag.name_lower
                norm = tag.name_normalized
                if name == q_under or norm == q_norm:
                    exact_matches.append(tag)
                elif name.startswith(q_under) or norm.startswith(q_norm):
                    prefix_matches.append(tag)
                elif word_target_u in name or word_target_n in norm:
                    word_start_matches.append(tag)
                elif q_under in name or q_norm in norm:
                    substring_matches.append(tag)
                if len(exact_matches) + len(prefix_matches) + len(word_start_matches) >= limit * 2:
                    break

        # Assemble final ranked results
        combined = exact_matches + prefix_matches + word_start_matches + substring_matches + alias_matches
        results: List[Dict[str, Any]] = []
        added_names: Set[str] = set()

        for tag in combined:
            if tag.name not in added_names:
                results.append(tag.to_dict())
                added_names.add(tag.name)
                if len(results) >= limit:
                    break

        return results

    def get_top_tags(self, limit: int = 1500) -> List[Dict[str, Any]]:
        """Return the top most frequent Danbooru tags for browser preloading."""
        if not self._loaded:
            self.load_tags()
        return [t.to_dict() for t in self.tags[:limit]]


# Global tag service instance
tag_service = TagService()

