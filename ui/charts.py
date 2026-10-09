"""Drawings on tk Canvases: hero trend lines, a sortable table whose cells can hold them, and the
hero page's trend chart. A canvas draws hundreds of shapes in a few milliseconds and needs no
images, which keeps these pages light."""

import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageTk

from ui import images
from ui.theme import COLORS, FONT, card
from ui.widgets import hide_tooltip, show_tooltip

Box = Tuple[float, float, float, float]  # x0, y0, x1, y1
TREND_SPAN = 0.05  # small trend lines are 5 win-rate points tall (more if a hero moved more), so they compare
ITEM_TREND_SPAN = 0.03  # item win rates move less from day to day


def week_label(start: float) -> str:
    """e.g. "Aug 9" for the week starting then (the API's weeks are in UTC)."""
    t = time.gmtime(start)
    return f"{time.strftime('%b', t)} {t.tm_mday}"


def trend_color(trend: Optional[Dict[str, Any]]) -> str:
    if not trend or trend.get("change") is None or trend["steady"]:
        return COLORS["dim"]
    return COLORS["win"] if trend["change"] > 0 else COLORS["loss"]


def change_text(trend: Optional[Dict[str, Any]]) -> str:
    if not trend or trend.get("change") is None:
        return "new" if trend and trend["weeks"] else "-"
    if trend["steady"]:
        return "steady"
    return f"{'▲' if trend['change'] > 0 else '▼'} {abs(trend['change']) * 100:.1f}"


# How trends describe their periods: heroes by week over 12 weeks, items by day over 14 days
HERO_WEEKS = {"recent": "last 4 weeks", "before": "9-12 weeks ago", "point": "week of ", "pick": "pick rate"}
ITEM_DAYS = {"recent": "last 7 days", "before": "8-14 days ago", "point": "", "pick": "of players bought it"}


def change_tip(trend: Optional[Dict[str, Any]], period: Dict[str, str] = HERO_WEEKS) -> Optional[str]:
    if not trend or trend.get("change") is None:
        return f"Not enough games {period['before']} to compare" if trend else None
    lines = [f"Win rate, {period['recent']}: {trend['recent']:.1%}", f"{period['before'].capitalize()}: {trend['before']:.1%}"]
    if trend["steady"]:
        lines.append("Steady: within what chance alone would give, or under half a point")
    return "\n".join(lines)


def week_tip(name: str, point: Dict[str, Any], period: Dict[str, str] = HERO_WEEKS) -> str:
    return (f"{name} · {period['point']}{week_label(point['start'])}\n"
            f"{point['win_rate']:.1%} win rate · {point['pick_rate']:.0%} {period['pick']} · {point['games']:,} games")


def trend_points(weeks: List[Dict[str, Any]], total_weeks: int, box: Box,
                 span: float = TREND_SPAN) -> List[Tuple[float, float]]:
    """Where each week's win rate goes in the box. The scale is the same for every hero, centred on
    the hero's own level, so a flat line means steady whether the hero wins 45% or 55%."""
    x0, y0, x1, y1 = box
    values = [w["win_rate"] for w in weeks]
    span = max(span, max(values) - min(values))
    top = (max(values) + min(values)) / 2 + span / 2
    step = (x1 - x0) / max(total_weeks - 1, 1)
    return [(x0 + w["index"] * step, y0 + (top - w["win_rate"]) / span * (y1 - y0)) for w in weeks]


def nearest(points: List[Tuple[float, float]], x: float) -> int:
    return min(range(len(points)), key=lambda n: abs(points[n][0] - x))


def draw_trend(canvas: tk.Canvas, trend: Dict[str, Any], total_weeks: int, box: Box,
               focus_x: Optional[float] = None, tags: Sequence[str] = (), span: float = TREND_SPAN) -> None:
    """A hero's weekly win rate as a small line, with a dot on the latest week. focus_x: mark the
    week nearest to it (the mouse)."""
    points = trend_points(trend["weeks"], total_weeks, box, span)
    color = trend_color(trend)
    canvas.create_line(*[c for p in points for c in p], fill=color, width=2, tags=tags)
    x, y = points[-1]
    canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=color, outline="", tags=tags)
    if focus_x is not None:
        x, y = points[nearest(points, focus_x)]
        canvas.create_line(x, box[1] - 4, x, box[3] + 4, fill=COLORS["faint"], tags=tags)
        canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=COLORS["text"], outline=color, width=2, tags=tags)


@dataclass
class Column:
    key: str
    title: str
    width: int                                      # relative: columns stretch to fill the table
    text: Optional[Callable[[Dict], str]] = None    # the cell's text (default: the row's value)
    color: Optional[Callable[[Dict], str]] = None   # the text's colour (default: normal text)
    draw: Optional[Callable] = None                 # draw(canvas, row, box, focus_x, tags) instead of text
    tip: Optional[Callable[[Dict, float, Box], Optional[str]]] = None  # hover text, given the mouse's x
    align: str = "center"                           # or "w" (left)
    sort: Optional[Callable[[Dict], Any]] = None    # what to sort by (default: the row's value)


class ChartTable:
    """A sortable table drawn on a canvas, so cells can hold drawings and react to the mouse.
    Click a heading to sort (again to reverse), a row for on_click(row). Rows light up under the
    mouse, and cells with a tip show it in a popup."""
    ROW, HEADER = 34, 30

    def __init__(self, parent, columns: List[Column], rows: List[Dict[str, Any]],
                 on_click: Optional[Callable[[Dict], None]] = None, height_rows: int = 17):
        self.columns, self.rows, self.on_click = columns, rows, on_click
        self.sort_key, self.reverse = None, False
        self.hover_row: Optional[int] = None
        self.focused: Optional[Tuple[int, int]] = None  # (row, column) of a drawing that marks the mouse
        self.edges: List[float] = []
        self.outer, inner = card(parent, padding=6)
        self.header = tk.Canvas(inner, height=self.HEADER, bg=COLORS["surface"], highlightthickness=0)
        self.header.pack(fill="x")
        body = tk.Frame(inner, bg=COLORS["card"])
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=COLORS["card"], highlightthickness=0, height=self.ROW * height_rows,
                                yscrollincrement=self.ROW, cursor="hand2" if on_click else "")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<Motion>", self.motion)
        self.canvas.bind("<Enter>", lambda event: self.canvas.bind_all("<MouseWheel>", self.wheel))
        self.canvas.bind("<Leave>", self.leave)
        self.canvas.bind("<ButtonRelease-1>", self.click)
        self.header.bind("<ButtonRelease-1>", self.header_click)
        self.header.configure(cursor="hand2")

    def pack(self, **options):
        self.outer.pack(**options)
        return self

    # --- drawing
    def draw(self):
        width = self.canvas.winfo_width()
        if width < 50:
            return
        total = sum(c.width for c in self.columns)
        self.edges = [0.0]
        for c in self.columns:
            self.edges.append(self.edges[-1] + c.width / total * width)
        self.draw_header()
        self.canvas.delete("all")
        for n in range(len(self.rows)):
            self.draw_row(n)
        self.canvas.configure(scrollregion=(0, 0, width, len(self.rows) * self.ROW))

    def draw_header(self):
        self.header.delete("all")
        for n, c in enumerate(self.columns):
            arrow = (" ▼" if self.reverse else " ▲") if c.key == self.sort_key else ""
            x = self.edges[n] + 10 if c.align == "w" else (self.edges[n] + self.edges[n + 1]) / 2
            self.header.create_text(x, self.HEADER / 2, text=c.title + arrow, anchor=c.align if c.align == "w" else "center",
                                    fill=COLORS["dim"], font=(FONT, 9, "bold"))

    def cell_box(self, row: int, column: int) -> Box:
        return (self.edges[column], row * self.ROW, self.edges[column + 1], (row + 1) * self.ROW)

    def draw_row(self, n: int):
        row = self.rows[n]
        self.canvas.create_rectangle(0, n * self.ROW, self.edges[-1], (n + 1) * self.ROW, outline="",
                                     fill=COLORS["button"] if n == self.hover_row else COLORS["card"], tags=("bg", f"bg{n}"))
        for c in range(len(self.columns)):
            self.draw_cell(n, c)

    def draw_cell(self, n: int, c: int, focus_x: Optional[float] = None):
        column, row, box = self.columns[c], self.rows[n], self.cell_box(n, c)
        tags = (f"cell{n}_{c}",)
        if column.draw:
            column.draw(self.canvas, row, box, focus_x, tags)
            return
        text = column.text(row) if column.text else str(row.get(column.key, ""))
        x = box[0] + 10 if column.align == "w" else (box[0] + box[2]) / 2
        self.canvas.create_text(x, (box[1] + box[3]) / 2, text=text, anchor=column.align if column.align == "w" else "center",
                                fill=column.color(row) if column.color else COLORS["text"], font=(FONT, 10), tags=tags)

    def redraw_cell(self, n: int, c: int, focus_x: Optional[float] = None):
        self.canvas.delete(f"cell{n}_{c}")
        self.draw_cell(n, c, focus_x)

    # --- mouse
    def at(self, event) -> Tuple[Optional[int], Optional[int], float]:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        n = int(y // self.ROW)
        c = next((i for i in range(len(self.columns)) if self.edges[i] <= x < self.edges[i + 1]), None)
        return (n if 0 <= n < len(self.rows) else None), c, x

    def motion(self, event):
        n, c, x = self.at(event)
        if n != self.hover_row:
            if self.hover_row is not None:
                self.canvas.itemconfigure(f"bg{self.hover_row}", fill=COLORS["card"])
            if n is not None:
                self.canvas.itemconfigure(f"bg{n}", fill=COLORS["button"])
            self.hover_row = n
        # A drawing that marks the mouse (the trend line's week) is redrawn as the mouse moves
        if self.focused and self.focused != (n, c):
            self.redraw_cell(*self.focused)
            self.focused = None
        if n is not None and c is not None and self.columns[c].draw and self.columns[c].tip:
            self.redraw_cell(n, c, x)
            self.focused = (n, c)
        column = self.columns[c] if c is not None else None
        text = column.tip(self.rows[n], x, self.cell_box(n, c)) if n is not None and column and column.tip else None
        if text:
            show_tooltip(self.canvas, text, event.x_root, event.y_root)
        else:
            hide_tooltip()

    def leave(self, event=None):
        self.canvas.unbind_all("<MouseWheel>")
        hide_tooltip()
        if self.hover_row is not None:
            self.canvas.itemconfigure(f"bg{self.hover_row}", fill=COLORS["card"])
            self.hover_row = None
        if self.focused:
            self.redraw_cell(*self.focused)
            self.focused = None

    def wheel(self, event):
        hide_tooltip(now=True)
        self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")

    def click(self, event):
        n, _, _ = self.at(event)
        if n is not None and self.on_click:
            self.on_click(self.rows[n])

    def header_click(self, event):
        c = next((i for i in range(len(self.columns)) if self.edges[i] <= event.x < self.edges[i + 1]), None)
        if c is None:
            return
        column = self.columns[c]
        value = column.sort or (lambda r: r.get(column.key))
        # First click on a number column sorts biggest first; text columns A-Z
        if self.sort_key == column.key:
            self.reverse = not self.reverse
        else:
            self.sort_key = column.key
            self.reverse = any(isinstance(value(r), (int, float)) for r in self.rows)
        present = [r for r in self.rows if value(r) is not None]
        missing = [r for r in self.rows if value(r) is None]  # always last, whichever way
        self.rows[:] = sorted(present, key=value, reverse=self.reverse) + missing
        self.hover_row, self.focused = None, None
        self.draw()


# --- cells for hero tables
def hero_cell(key: str = "hero"):
    def draw(canvas, row, box, focus_x, tags):
        x0, y0, x1, y1 = box
        middle = (y0 + y1) / 2
        badge = images.hero_badge(row[key], 24)
        if badge:
            canvas.create_image(x0 + 8, middle, image=badge, anchor="w", tags=tags)
        canvas.create_text(x0 + 40, middle, text=row[key], anchor="w", fill=COLORS["text"], font=(FONT, 10), tags=tags)
    return draw


def trend_box(box: Box) -> Box:
    x0, y0, x1, y1 = box
    return (x0 + 12, y0 + 8, x1 - 12, y1 - 8)


def trend_cell(total_weeks: int, span: float = TREND_SPAN):
    def draw(canvas, row, box, focus_x, tags):
        trend = row.get("trend")
        if not trend or len(trend["weeks"]) < 2:
            canvas.create_text((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, text="new" if trend else "-",
                               fill=COLORS["faint"], font=(FONT, 9), tags=tags)
            return
        draw_trend(canvas, trend, total_weeks, trend_box(box), focus_x, tags, span)
    return draw


def trend_tip(total_weeks: int, name_key: str = "hero", period: Dict[str, str] = HERO_WEEKS, span: float = TREND_SPAN):
    def tip(row, x, box):
        trend = row.get("trend")
        if not trend or len(trend["weeks"]) < 2:
            return None
        points = trend_points(trend["weeks"], total_weeks, trend_box(box), span)
        return week_tip(row[name_key], trend["weeks"][nearest(points, x)], period)
    return tip


def item_cell():
    def draw(canvas, row, box, focus_x, tags):
        x0, y0, x1, y1 = box
        middle = (y0 + y1) / 2
        canvas.create_image(x0 + 8, middle, image=images.item_icon(row, 24), anchor="w", tags=tags)
        canvas.create_text(x0 + 40, middle, text=row["name"], anchor="w", fill=COLORS["text"], font=(FONT, 10), tags=tags)
    return draw


# --- matchups
ADVANTAGE_SCALE = 0.06  # an advantage bar is full at 6 points better (or worse) than the hero's average


def verdict(shift: float) -> Tuple[str, str]:
    """(word, MATCHUP_COLORS key) for how a whole matchup shifts the hero's average win rate."""
    if shift >= 0.01:
        return "FAVOURABLE", "good"
    if shift <= -0.01:
        return "TOUGH", "bad"
    return "EVEN", "even"


def advantage_bar(parent, shift: float, bg: str = "card", width: int = 150, height: int = 12,
                  faded: bool = False) -> tk.Canvas:
    """A bar out from the middle line (the hero's own average): right and green when better than
    usual, left and red when worse. Faded when it rests on few games."""
    canvas = tk.Canvas(parent, width=width, height=height, bg=COLORS[bg], highlightthickness=0)
    middle = width / 2
    canvas.create_rectangle(0, 2, width, height - 2, fill=COLORS["button"], outline="")
    end = middle + max(-1.0, min(1.0, shift / ADVANTAGE_SCALE)) * (middle - 1)
    canvas.create_rectangle(min(middle, end), 1, max(middle, end), height - 1, outline="",
                            fill=COLORS["win"] if shift >= 0 else COLORS["loss"], stipple="gray50" if faded else "")
    canvas.create_line(middle, 0, middle, height, fill=COLORS["text"])
    return canvas


def score_color(good: Optional[float]) -> str:
    """Green for a strong stat or game (70+ of 100), red for a weak one (30 or less), else plain."""
    if good is None:
        return COLORS["dim"]
    return COLORS["win"] if good >= 70 else COLORS["loss"] if good <= 30 else COLORS["accent"]


def percentile_bar(parent, good: Optional[float], percentile: float, bg: str = "card", width: int = 200,
                   height: int = 12) -> tk.Canvas:
    """How many players on this hero did worse: right = better than more of them, with a mark at the
    middle (a typical game). A stat that isn't good or bad (good=None) shows its plain percentile, grey."""
    canvas = tk.Canvas(parent, width=width, height=height, bg=COLORS[bg], highlightthickness=0)
    position = percentile if good is None else good
    canvas.create_rectangle(0, 2, width, height - 2, fill=COLORS["button"], outline="")
    canvas.create_rectangle(0, 2, max(2, position / 100 * width), height - 2, fill=score_color(good), outline="")
    canvas.create_line(width / 2, 0, width / 2, height, fill=COLORS["text"])
    return canvas


def death_map(parent, image, radius: float, deaths: List[Dict[str, Any]], size: int = 280, bg: str = "card",
              on_click: Optional[Callable[[Dict[str, Any]], None]] = None,
              tip: Callable[[Dict[str, Any]], str] = lambda death: "") -> tk.Label:
    """The minimap recoloured for the app's theme (its grey streets on a card, buildings left out), with a
    dot per death: red when no teammate was near, amber when one was. deaths: dicts with world "x", "y" and
    "alone". With on_click, hovering a dot shows tip(death) and clicking it calls on_click(death)."""
    shade, alpha = image.convert("LA").split()
    streets = ImageChops.multiply(shade.point(lambda v: 255 if v < 150 else 0), alpha)  # dark and not transparent
    picture = Image.new("RGB", image.size, COLORS[bg])
    picture.paste(Image.new("RGB", image.size, COLORS["map_streets"]), mask=streets)
    picture = picture.resize((size, size), Image.LANCZOS)
    draw = ImageDraw.Draw(picture)
    spots = []  # (x, y in pixels, death), drawn order: the last is on top
    for d in sorted(deaths, key=lambda d: d["alone"]):  # alone last, so red dots stay on top
        px, py = (d["x"] + radius) / (2 * radius) * size, (1 - (d["y"] + radius) / (2 * radius)) * size
        draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=COLORS["loss"] if d["alone"] else "#f5b942", outline=COLORS[bg])
        spots.append((px, py, d))
    photo = ImageTk.PhotoImage(picture)
    # No border or padding, so mouse positions are picture pixels
    widget = tk.Label(parent, image=photo, bg=COLORS[bg], bd=0, padx=0, pady=0, highlightthickness=0)
    widget.image = photo  # tkinter forgets images nobody holds
    if on_click:
        def under(event) -> Optional[Dict[str, Any]]:  # the top dot within 6 px of the mouse
            near = [d for px, py, d in spots if (px - event.x) ** 2 + (py - event.y) ** 2 <= 36]
            return near[-1] if near else None

        def motion(event):
            death = under(event)
            widget.configure(cursor="hand2" if death else "")
            if death:
                show_tooltip(widget, tip(death), event.x_root, event.y_root)
            else:
                hide_tooltip()

        def click(event):
            death = under(event)
            if death:
                hide_tooltip(now=True)
                on_click(death)
        widget.bind("<Motion>", motion)
        widget.bind("<Leave>", lambda event: hide_tooltip())
        widget.bind("<Button-1>", click)
    return widget


def form_chart(parent, series: List[Dict[str, Any]], recent: int, on_open: Callable[[Dict[str, Any]], None],
               bg: str = "card", width: int = 300, height: int = 44) -> tk.Canvas:
    """Souls per minute in the matches Your form compares, oldest left: a dot per match (green win, red loss),
    the average of the older and the newer `recent` as two lines (what the text compares) and a divider
    between them. Hovering a dot shows its match; clicking opens it."""
    canvas = tk.Canvas(parent, width=width, height=height, bg=COLORS[bg], highlightthickness=0, cursor="hand2")
    values = [s["souls_per_min"] for s in series]
    low, high, pad = min(values), max(values), 5
    x_of = lambda n: pad + n * (width - 2 * pad) / max(len(series) - 1, 1)  # noqa: E731
    y_of = lambda v: pad + (high - v) / ((high - low) or 1) * (height - 2 * pad)  # noqa: E731
    split = len(series) - recent
    canvas.create_line(x_of(split - 0.5), 0, x_of(split - 0.5), height, fill=COLORS["faint"], dash=(2, 3))
    for start, end, color in ((0, split, COLORS["dim"]), (split, len(series), COLORS["accent"])):
        y = y_of(sum(values[start:end]) / (end - start))
        canvas.create_line(x_of(start), y, x_of(end - 1), y, fill=color, width=2)
    for n, s in enumerate(series):
        x, y = x_of(n), y_of(s["souls_per_min"])
        canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, outline="", fill=COLORS["win"] if s["won"] else COLORS["loss"])

    def under(event) -> Dict[str, Any]:
        return series[min(range(len(series)), key=lambda n: abs(x_of(n) - event.x))]

    def motion(event):
        s = under(event)
        show_tooltip(canvas, f"{time.strftime('%b %d', time.localtime(s['start_time'])).replace(' 0', ' ')} · {s['hero']} · "
                             f"{'win' if s['won'] else 'loss'}\n{s['souls_per_min']:,.0f} souls/min · click to open the match",
                     event.x_root, event.y_root)

    canvas.bind("<Motion>", motion)
    canvas.bind("<Leave>", lambda event: hide_tooltip())
    canvas.bind("<Button-1>", lambda event: (hide_tooltip(now=True), on_open(under(event))))
    return canvas


# --- the hero page's chart
def trend_chart(parent, hero: str, trend: Dict[str, Any], total_weeks: int, bg: str = "card",
                height: int = 92) -> tk.Canvas:
    """A hero's weekly win rate, on a real scale with a 50% line when it's in range. Hovering marks
    the nearest week and shows its numbers."""
    canvas = tk.Canvas(parent, height=height, bg=COLORS[bg], highlightthickness=0, width=260)
    weeks = trend["weeks"]
    values = [w["win_rate"] for w in weeks]
    low, high = min(values) - 0.01, max(values) + 0.01
    if high - low < 0.04:  # at least 4 points tall, so a steady hero looks steady
        middle = (high + low) / 2
        low, high = middle - 0.02, middle + 0.02
    state = {"points": []}

    def draw(focus_x: Optional[float] = None):
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < 80:
            return
        left, right, top, bottom = 38, width - 8, 6, height - 18
        y_of = lambda v: top + (high - v) / (high - low) * (bottom - top)  # noqa: E731
        step = (right - left) / max(total_weeks - 1, 1)
        points = [(left + w["index"] * step, y_of(w["win_rate"])) for w in weeks]
        state["points"] = points
        for value in (high, low):
            canvas.create_text(left - 6, y_of(value), text=f"{value:.0%}" if high - low > 0.06 else f"{value:.1%}",
                               anchor="e", fill=COLORS["faint"], font=(FONT, 8))
        if low < 0.5 < high:
            canvas.create_line(left, y_of(0.5), right, y_of(0.5), fill=COLORS["faint"], dash=(2, 3))
        canvas.create_text(left, height - 6, text=week_label(weeks[0]["start"]), anchor="w", fill=COLORS["faint"], font=(FONT, 8))
        canvas.create_text(right, height - 6, text=week_label(weeks[-1]["start"]), anchor="e", fill=COLORS["faint"], font=(FONT, 8))
        color = trend_color(trend)
        canvas.create_line(*[c for p in points for c in p], fill=color, width=2)
        for x, y in points:
            canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=color, outline="")
        if focus_x is not None:
            x, y = points[nearest(points, focus_x)]
            canvas.create_line(x, top, x, bottom, fill=COLORS["faint"])
            canvas.create_oval(x - 4.5, y - 4.5, x + 4.5, y + 4.5, fill=COLORS["text"], outline=color, width=2)

    def motion(event):
        if not state["points"]:
            return
        draw(event.x)
        show_tooltip(canvas, week_tip(hero, weeks[nearest(state["points"], event.x)]), event.x_root, event.y_root)

    def leave(event):
        draw()
        hide_tooltip()

    canvas.bind("<Configure>", lambda event: draw())
    canvas.bind("<Motion>", motion)
    canvas.bind("<Leave>", leave)
    return canvas
