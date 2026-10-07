"""Patch notes: Deadlock's announcements from Steam's public news API, as plain text.

The Deadlock API's /v1/patches only has a ~300-character preview of each forum post, so the notes come
from Steam itself (ISteamNews/GetNewsForApp, no key needed), which returns each announcement in full,
with its real date. Announcements are written in Steam's BBCode: [p]paragraphs[/p], [b]bold[/b],
"\\[ General ]" section headings, [list][*]items[/list], [url=...]links[/url] and [img] pictures.
"""

import html
import re
from typing import Any, Dict, List, Optional, Tuple

import deadlock_api

NEWS_URL = ("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=1422450&count={count}&maxlength=0"
            "&feeds=steam_community_announcements")  # 1422450: Deadlock's Steam app id
NEWS_COUNT = 20
NEWS_CACHE_S = 3600  # Valve posts a few times a month: an hour old is fresh enough

Line = Tuple[str, str]  # (kind, text): kind is "heading", "item" or "text"


def notes_lines(bbcode: str) -> List[Line]:
    """An announcement's BBCode as lines of plain text. A paragraph that is only bold, a [h1]-[h3], or a
    "[ General ]"-style title is a heading; lines starting with "- " or list items are items."""
    text = re.sub(r"\[img\].*?\[/img\]|\[previewyoutube[^\]]*\].*?\[/previewyoutube\]", "", bbcode, flags=re.S | re.I)
    text = re.sub(r"\[\*\]", "\n- ", text)
    text = re.sub(r"\[/?(p|h[1-6]|list|olist)\]", lambda m: "\n\x00" if m.group(1).startswith("h") else "\n", text)
    lines: List[Line] = []
    for raw in text.split("\n"):
        heading = raw.startswith("\x00") or bool(re.fullmatch(r"\s*\[b\].*\[/b\]\s*", raw))
        clean = html.unescape(re.sub(r"\[/?[a-z0-9]+(=[^\]]*)?\]", "", raw.replace("\x00", ""), flags=re.I))
        clean = re.sub(r"\s+", " ", clean.replace("\\[", "[").replace("\\]", "]")).strip()
        if not clean:
            continue
        if heading or re.fullmatch(r"\[ .+ \]", clean):
            lines.append(("heading", clean.strip("[] ")))
        elif clean.startswith("- "):
            lines.append(("item", clean[2:]))
        else:
            lines.append(("text", clean))
    return lines


def news(count: int = NEWS_COUNT) -> List[Dict[str, Any]]:
    """The newest announcements, newest first: [{"title", "date" (unix), "url", "lines", "patch"}]; "patch"
    is True for update notes ("Minor Update - 10-05-2026"), False for other news. Kept on disk an hour."""
    def build():
        items = deadlock_api._download(NEWS_URL.format(count=count))["appnews"]["newsitems"]
        return [{"title": i["title"], "date": i["date"], "url": i["url"], "lines": notes_lines(i["contents"]),
                 "patch": bool(re.search(r"\bupdate\b", i["title"], re.I))} for i in items]
    return deadlock_api.disk_cached("steam_news", build, max_age=NEWS_CACHE_S)


def lines_about(lines: List[Line], hero: Optional[str]) -> List[Line]:
    """Only the item and text lines that mention hero (as a whole word), each under its section heading.
    All lines when hero is None."""
    if not hero:
        return lines
    pattern, kept, heading = re.compile(rf"\b{re.escape(hero)}\b", re.I), [], None
    for kind, text in lines:
        if kind == "heading":
            heading = (kind, text)
        elif pattern.search(text):
            if heading:
                kept.append(heading)
                heading = None
            kept.append((kind, text))
    return kept

