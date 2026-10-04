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

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "images")

_images: Dict[str, Image.Image] = {}  # url -> image, once loaded
_lock = threading.Lock()


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".png")


def load(url: str) -> Optional[Image.Image]:
    """The image at url, from memory, then disk, then the network. None if it can't be had."""
    if not url:
        return None
    with _lock:
        if url in _images:
            return _images[url]
    path = _cache_path(url)
    try:
        if os.path.exists(path):
            image = Image.open(path).convert("RGBA")
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "deadlock-analyzer (learning project)"})
            with urllib.request.urlopen(request, timeout=10) as response:
                image = Image.open(io.BytesIO(response.read())).convert("RGBA")
            os.makedirs(CACHE_DIR, exist_ok=True)
            image.save(path)
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


def load_many(urls: Iterable[str]) -> None:
    """Load several images in parallel (worker thread)."""
    wanted = [u for u in set(urls) if u and get(u) is None]
    if wanted:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(load, wanted))


def hero_art() -> Dict[str, Dict[str, str]]:
    """Hero name -> {"icon": small icon url, "card": portrait url, "color": the hero's hex colour}."""
    art = {}
    for hero in deadlock_api.fetch_hero_assets():
        images = hero.get("images") or {}
        art[hero["name"]] = {
            "icon": images.get("icon_image_small"),
            "card": images.get("icon_hero_card"),
            "color": (hero.get("colors") or {}).get("style_hex") or "#4a5a6a",
        }
    return art


def rank_emblem_url(tier: int, subrank: int) -> Optional[str]:
    """URL of the small emblem for a rank, e.g. Emissary 2. None for unranked."""
    if not tier:
        return None
    for rank in deadlock_api.fetch_rank_assets():
        if rank["tier"] == tier:
            images = rank.get("images") or {}
            return images.get(f"small_subrank{subrank}") or images.get("small_subrank1") or images.get("large")
    return None


def preload() -> None:
    """Worker thread, at startup: every hero icon and rank emblem, so pages can show them right away."""
    art = hero_art()
    urls = [a["icon"] for a in art.values()]
    for rank in deadlock_api.fetch_rank_assets():
        urls += [url for key, url in (rank.get("images") or {}).items() if key.startswith("small_subrank") and not key.endswith("webp")]
    load_many(urls)
