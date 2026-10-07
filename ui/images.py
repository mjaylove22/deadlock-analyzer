"""Turn PIL images into tkinter images: hero badges, circular avatars, rank emblems, app icon.

Every image made here is cached. tkinter only displays an image while Python still holds a
reference to it, so an image held only by a local variable would silently vanish.
"""

import io
from typing import Dict, Optional, Tuple

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

import assets
from ui.theme import ITEM_SLOT_COLORS, is_light

_cache: Dict[Tuple, object] = {}
_hero_art: Dict[str, Dict[str, str]] = {}


def set_hero_art(art: Dict[str, Dict[str, str]]) -> None:
    _hero_art.update(art)


def hero_card_url(hero: str):
    return _hero_art.get(hero, {}).get("card")


def hero_color(hero: str) -> str:
    return _hero_art.get(hero, {}).get("color", "#4a5a6a")


def shade(hex_color: str, factor: float) -> str:
    """Darken (factor < 1) or lighten towards white (factor > 1) a hex colour."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    if factor <= 1:
        r, g, b = (int(c * factor) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (factor - 1)) for c in (r, g, b))
    return f"#{min(r, 255):02x}{min(g, 255):02x}{min(b, 255):02x}"


def readable(hex_color: str) -> str:
    """The colour, adjusted until it's readable as text on the theme's cards: lightened on dark, darkened on light."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    brightness = 0.299 * r + 0.587 * g + 0.114 * b
    if is_light():
        return hex_color if brightness <= 110 else shade(hex_color, 110 / brightness)
    return hex_color if brightness >= 140 else shade(hex_color, 1 + (140 - brightness) / 255 * 1.6)


def circle_mask(size: int) -> Image.Image:
    """A smooth circular mask (drawn large, then scaled down, for anti-aliased edges)."""
    big = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    return big.resize((size, size), Image.LANCZOS)


def hero_badge_pil(hero: str, size: int) -> Optional[Image.Image]:
    """The hero's icon on a circle in their own colour (some icons are too dark for a dark theme)."""
    icon = assets.get(_hero_art.get(hero, {}).get("icon"))
    if icon is None:
        return None
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    disc = Image.new("RGBA", (size, size), shade(hero_color(hero), 0.55))
    badge.paste(disc, (0, 0), circle_mask(size))
    inner = icon.resize((int(size * 0.92), int(size * 0.92)), Image.LANCZOS)
    offset = (size - inner.width) // 2
    badge.alpha_composite(inner, (offset, offset))
    # keep the icon inside the circle
    clipped = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    clipped.paste(badge, (0, 0), circle_mask(size))
    return clipped


def hero_badge(hero: str, size: int, kind: str = "tk"):
    """kind "tk" -> ImageTk.PhotoImage (tk labels, tables); "ctk" -> CTkImage (CustomTkinter widgets)."""
    key = ("hero", hero, size, kind)
    if key not in _cache:
        pil = hero_badge_pil(hero, size)
        if pil is None:
            return None
        _cache[key] = ctk.CTkImage(pil, pil, (size, size)) if kind == "ctk" else ImageTk.PhotoImage(pil)
    return _cache[key]


def hero_card(hero: str, height: int):
    """The hero's portrait card (if downloaded), scaled to a height, as a PhotoImage."""
    key = ("card", hero, height)
    if key not in _cache:
        card = assets.get(_hero_art.get(hero, {}).get("card"))
        if card is None:
            return None
        width = round(card.width * height / card.height)
        _cache[key] = ImageTk.PhotoImage(card.resize((width, height), Image.LANCZOS))
    return _cache[key]


def item_icon_pil(item: Dict, size: int) -> Image.Image:
    """An item as the game shows it: its artwork in a rounded square, with its category's colour in
    the top-right corner. Items without artwork get their white symbol on the category colour, and
    an item not downloaded yet is just the coloured square."""
    big = size * 4  # drawn large, then scaled down, for smooth corners
    color = ITEM_SLOT_COLORS.get(item.get("slot"), "#4a5a6a")
    rounded = Image.new("L", (big, big), 0)
    ImageDraw.Draw(rounded).rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=255)
    rounded = rounded.resize((size, size), Image.LANCZOS)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.paste(Image.new("RGBA", (size, size), shade(color, 0.8)), (0, 0), rounded)
    picture = assets.get(item.get("image"))
    if picture is not None and item.get("symbol"):
        inner = picture.resize((round(size * 0.72),) * 2, Image.LANCZOS)
        offset = (size - inner.width) // 2
        tile.alpha_composite(inner, (offset, offset))
    elif picture is not None:
        tile.paste(picture.resize((size, size), Image.LANCZOS), (0, 0), rounded)
        corner = Image.new("RGBA", (big, big), (0, 0, 0, 0))  # the category corner, like the game's
        ImageDraw.Draw(corner).polygon([(big * 0.62, 0), (big, 0), (big, big * 0.38)], fill=color)
        corner = corner.resize((size, size), Image.LANCZOS)
        clipped = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        clipped.paste(corner, (0, 0), rounded)
        tile.alpha_composite(clipped)
    return tile


def item_icon(item: Dict, size: int, kind: str = "tk"):
    """kind "tk" -> ImageTk.PhotoImage (tk labels); "ctk" -> CTkImage (CustomTkinter widgets)."""
    key = ("item", item.get("image"), item.get("slot"), size, kind, assets.get(item.get("image")) is not None)
    if key not in _cache:
        pil = item_icon_pil(item, size)
        _cache[key] = ctk.CTkImage(pil, pil, (size, size)) if kind == "ctk" else ImageTk.PhotoImage(pil)
    return _cache[key]


def ability_icon(url: Optional[str], size: int, color: str):
    """An ability's icon on a rounded tile in a dark shade of the hero's colour, as a PhotoImage."""
    key = ("ability", url, size, color, assets.get(url) is not None)
    if key not in _cache:
        big = size * 4
        rounded = Image.new("L", (big, big), 0)
        ImageDraw.Draw(rounded).rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 4, fill=255)
        rounded = rounded.resize((size, size), Image.LANCZOS)
        tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        tile.paste(Image.new("RGBA", (size, size), shade(color, 0.4)), (0, 0), rounded)
        picture = assets.get(url)
        if picture is not None:
            inner = picture.resize((round(size * 0.8),) * 2, Image.LANCZOS)
            offset = (size - inner.width) // 2
            tile.alpha_composite(inner, (offset, offset))
        _cache[key] = ImageTk.PhotoImage(tile)
    return _cache[key]


def avatar(data: Optional[bytes], size: int, ring: Optional[str] = None, placeholder: str = "#2a303c"):
    """A circular Steam avatar with an optional coloured ring, as a PhotoImage."""
    key = ("avatar", hash(data) if data else None, size, ring)
    if key not in _cache:
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        inner = size - 6 if ring else size
        if data:
            picture = Image.open(io.BytesIO(data)).convert("RGBA").resize((inner, inner), Image.LANCZOS)
        else:
            picture = Image.new("RGBA", (inner, inner), placeholder)
        if ring:
            canvas.paste(Image.new("RGBA", (size, size), ring), (0, 0), circle_mask(size))
        offset = (size - inner) // 2
        canvas.paste(picture, (offset, offset), circle_mask(inner))
        _cache[key] = ImageTk.PhotoImage(canvas)
    return _cache[key]


def rank_emblem(tier: int, subrank: int, size: int):
    """The rank's emblem as a CTkImage, or None if unranked or not downloaded."""
    key = ("rank", tier, subrank, size)
    if key not in _cache:
        image = assets.get(assets.rank_emblem_url(tier, subrank)) if tier else None
        if image is None:
            return None
        image = image.copy()
        image.thumbnail((size * 2, size))
        _cache[key] = ctk.CTkImage(image, image, image.size)
    return _cache[key]
