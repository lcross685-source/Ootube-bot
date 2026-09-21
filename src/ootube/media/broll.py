"""B-roll sourcing.

Stock footage keyed to each section's visual query. Licensing matters as much
as looks: Pexels and Pixabay both permit commercial use without attribution,
which keeps the channel clear of copyright claims that would block
monetisation on the video.

If no provider key is configured the pipeline falls back to generated
gradient cards, so a run never fails purely for lack of footage.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..http import get, get_json

log = logging.getLogger(__name__)

PEXELS_ENDPOINT = "https://api.pexels.com/videos/search"
PIXABAY_ENDPOINT = "https://pixabay.com/api/videos/"


def _download(url: str, dest: Path) -> Path | None:
    resp = get(url, timeout=120)
    if resp is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


def _pexels(query: str, dest_dir: Path, index: int, min_width: int) -> Path | None:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return None
    data = get_json(
        PEXELS_ENDPOINT,
        params={"query": query, "per_page": 5, "orientation": "landscape"},
        headers={"Authorization": api_key},
    )
    for video in (data or {}).get("videos", []) or []:
        files = sorted(
            (f for f in video.get("video_files", []) if f.get("width")),
            key=lambda f: abs(int(f["width"]) - min_width),
        )
        for f in files:
            if f.get("link"):
                out = _download(f["link"], dest_dir / f"broll_{index:02d}.mp4")
                if out:
                    return out
    return None


def _pixabay(query: str, dest_dir: Path, index: int, min_width: int) -> Path | None:
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return None
    data = get_json(
        PIXABAY_ENDPOINT,
        params={"key": api_key, "q": query, "per_page": 5, "video_type": "film"},
    )
    for hit in (data or {}).get("hits", []) or []:
        videos = hit.get("videos", {}) or {}
        for quality in ("large", "medium", "small"):
            url = (videos.get(quality) or {}).get("url")
            if url:
                out = _download(url, dest_dir / f"broll_{index:02d}.mp4")
                if out:
                    return out
    return None


def fetch_broll(
    queries: list[str],
    dest_dir: str | Path,
    *,
    provider: str = "pexels",
    min_width: int = 1920,
) -> list[Path | None]:
    """Fetch one clip per query. ``None`` marks a query with no usable clip."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    order = [provider, "pexels", "pixabay"]
    seen: list[str] = []
    for p in order:
        if p not in seen:
            seen.append(p)

    results: list[Path | None] = []
    for i, query in enumerate(queries):
        clip: Path | None = None
        for p in seen:
            fn = {"pexels": _pexels, "pixabay": _pixabay}.get(p)
            if fn is None:
                continue
            try:
                clip = fn(query, dest, i, min_width)
            except Exception as exc:  # noqa: BLE001
                log.warning("broll provider %s failed for %r: %s", p, query, exc)
                clip = None
            if clip:
                break
        if clip is None:
            log.info("no b-roll for %r; will use a generated card", query)
        results.append(clip)
    return results
