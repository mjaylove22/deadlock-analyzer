"""Hero and rank images: downloaded once, kept in a disk cache (cache/images, gitignored).

Loading is meant for worker threads (it may download); afterwards images come from memory.
Images are returned as PIL images; ui/images.py turns them into tkinter images.
"""

import hashlib
import io
import logging
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, Optional

from PIL import Image

import deadlock_api
import paths

logger = logging.getLogger(__name__)

CACHE_DIR = paths.data("cache", "images")
ICON_MAX_SIDE = 96      # hero icons and rank emblems are shown at 18-26 px; 96 keeps them sharp
ITEM_MAX_SIDE = 48     # item icons are shown at up to 26 px; twice that stays sharp on high-DPI screens
PORTRAIT_MAX_SIDE = 240  # hero portraits are shown 110 px tall

_images: Dict[str, Image.Image] = {}  # url -> image, once loaded
_lock = threading.Lock()


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".png")


def load(url: str, max_side: int = ICON_MAX_SIDE) -> Optional[Image.Image]:
    """The image at url, from memory, then disk, then the network. None if it can't be had.
    Images are stored no bigger than max_side pixels: the originals are up to 512 px (220 KB per
    rank emblem) but are shown at 18-26 px, so full size would only waste disk and memory."""
    if not url:
        return None
    with _lock:
        if url in _images:
            return _images[url]
    path = _cache_path(url)
    try:
        if os.path.exists(path):
            image = Image.open(path).convert("RGBA")
            if max(image.size) > max_side:  # saved before images were shrunk: shrink it now
                image.thumbnail((max_side, max_side), Image.LANCZOS)
                image.save(path, optimize=True)
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "deadlock-analyzer (learning project)"})
            with urllib.request.urlopen(request, timeout=10) as response:
                image = Image.open(io.BytesIO(response.read())).convert("RGBA")
            image.thumbnail((max_side, max_side), Image.LANCZOS)
            os.makedirs(CACHE_DIR, exist_ok=True)
            image.save(path, optimize=True)
    except Exception as e:
        logger.warning(f"Could not load image {url}: {e}")
        return None
    with _lock:
        _images[url] = image
    return image


def get(url: str) -> Optional[Image.Image]:
    """Only what's already loaded (never downloads), so it's safe on the main thread."""
    with _lock:
        return _images.get(url)


def load_many(urls: Iterable[str], max_side: int = ICON_MAX_SIDE, workers: int = 8) -> None:
    """Load several images in parallel (worker thread). Fewer workers for big batches: 8 at once for
    ~170 images made the image server drop some connections."""
    wanted = [u for u in set(urls) if u and get(u) is None]
    if wanted:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda url: load(url, max_side), wanted))


def hero_art() -> Dict[str, Dict[str, str]]:
    """Hero name -> {"icon": small icon url, "card": portrait url, "color": the hero's hex colour}."""
    return {h["name"]: {"icon": h["icon"], "card": h["card"], "color": h["color"]} for h in deadlock_api.fetch_hero_assets()}


def rank_emblem_url(tier: int, subrank: int) -> Optional[str]:
    """URL of the small emblem for a rank, e.g. Emissary 2. None for unranked."""
    if not tier:
        return None
    for rank in deadlock_api.fetch_rank_assets():
        if rank["tier"] == tier:
            return rank["emblems"].get(str(subrank)) or rank["emblems"].get("1")
    return None


def preload() -> None:
    """Worker thread, at startup: every hero icon and rank emblem, so pages can show them right away."""
    art = hero_art()
    urls = [a["icon"] for a in art.values()]
    for rank in deadlock_api.fetch_rank_assets():
        urls += [url for url in rank["emblems"].values() if url]
    load_many(urls)
