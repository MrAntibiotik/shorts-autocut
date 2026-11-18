"""Вигляд «термінал із фільму»: чорний фон, зелений моноширинний шрифт, усі етапи роботи на екрані.

Підключається само (autocut/app/__init__.py → install()): кожне вікно Tk перефарбовується, а нові
віджети підхоплюються на льоту. Кольори, які код ставить сам (зелена кнопка «Почати», помилка червоним,
підказка сірим), переводяться в палітру терміналу за змістом: зелене → яскраво-зелене, червоне → червоне,
помаранчеве → бурштинове, сіре → приглушене зелене.

Повернути звичайний вигляд: кнопка «◐ Вигляд» у вікні або файл config\\ui_theme.txt зі словом classic.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEME_FILE = ROOT / "config" / "ui_theme.txt"

BG = "#000000"          # фон
BG_FIELD = "#020d05"    # поля вводу, списки, консоль
BG_BTN = "#04170a"      # кнопки
BG_SEL = "#0b3a18"      # вибрана вкладка, рядок
FG = "#33ff66"          # основний текст
FG_HI = "#9dffb8"       # яскравий: заголовки, активний етап
DIM = "#1c8f3f"         # підказки, неактивне
DARK = "#0f5224"        # ще не почате
AMBER = "#ffb000"       # увага
ERR = "#ff4d4d"         # помилка
CYAN = "#38e0ff"        # посилання, інформація
PALETTE = {BG, BG_FIELD, BG_BTN, BG_SEL, FG, FG_HI, DIM, DARK, AMBER, ERR, CYAN,
           "#0b3d1a", "#3d2a00", "#3d0b0b", "#0b2a3d"}

FIELD_CLASSES = {"Text", "Entry", "Listbox", "Spinbox"}
_installed = False
_mono = None


# ------------------------------------------------------------------ увімкнено?
def enabled() -> bool:
    try:
        return THEME_FILE.read_text(encoding="utf-8").strip().lower() != "classic"
    except OSError:
        return True


def set_enabled(on: bool) -> None:
    THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
    THEME_FILE.write_text("terminal" if on else "classic", encoding="utf-8")


# ------------------------------------------------------------------ кольори
def _rgb(w, color: str):
    try:
        r, g, b = w.winfo_rgb(color)
        return r >> 8, g >> 8, b >> 8
    except Exception:  # noqa: BLE001
        return None


def _hue(rgb) -> str:
    """Грубий тип кольору: red / amber / green / blue / light / mid / dark."""
    r, g, b = rgb
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    if max(r, g, b) - min(r, g, b) < 40:
        return "light" if lum > 185 else "mid" if lum > 70 else "dark"
    if r > g * 1.45 and r > b * 1.45:
        return "red"
    if r >= g * 0.9 and g > b * 1.3 and r > b * 1.6:
        return "amber"
    if g > r * 1.2 and g > b * 1.15:
        return "green"
    if b > r * 1.2:
        return "blue"
    return "light" if lum > 185 else "mid" if lum > 70 else "dark"


def map_fg(w, color: str, on_accent: bool = False) -> str:
    if not color or color in PALETTE:
        return color
    rgb = _rgb(w, color)
    if rgb is None:
        return FG
    kind = _hue(rgb)
    if kind == "red":
        return ERR
    if kind == "amber":
        return AMBER
    if kind == "green":
        return FG_HI
    if kind == "blue":
        return CYAN
    if kind == "mid":
        return DIM
    return FG_HI if on_accent else FG      # чорний / білий текст


def map_bg(w, color: str, cls: str) -> str:
    if color in PALETTE:
        return color
    rgb = _rgb(w, color) if color else None
    kind = _hue(rgb) if rgb else "light"
    if cls in ("Button", "Menubutton"):
        return {"green": "#0b3d1a", "amber": "#3d2a00", "red": "#3d0b0b", "blue": "#0b2a3d"}.get(kind, BG_BTN)
    if cls in FIELD_CLASSES:
        return BG_FIELD
    if kind in ("red", "amber", "green", "blue"):      # кольорова плашка-підказка
        return {"green": "#0b3d1a", "amber": "#3d2a00", "red": "#3d0b0b", "blue": "#0b2a3d"}[kind]
    return BG


def mono() -> str:
    global _mono
    if _mono is None:
        try:
            import tkinter.font as tkfont
            fams = set(tkfont.families())
        except Exception:  # noqa: BLE001
            fams = set()
        _mono = next((f for f in ("Consolas", "Cascadia Mono", "Lucida Console", "DejaVu Sans Mono",
                                  "Liberation Mono", "Courier New") if f in fams), "Courier")
    return _mono


# ------------------------------------------------------------------ перефарбування віджетів
def _font(w, spec, cache: dict):
    key = str(spec)
    if key in cache:
        return cache[key]
    import tkinter.font as tkfont
    try:
        a = tkfont.Font(root=w, font=spec).actual()
    except Exception:  # noqa: BLE001
        cache[key] = None
        return None
    if a.get("family") == mono():
        cache[key] = None
        return None
    size = a.get("size") or 10
    new = (mono(), size, a.get("weight", "normal")) + (("italic",) if a.get("slant") == "italic" else ())
    cache[key] = new
    return new


def _dark_titlebar(w) -> None:
    """Темна смуга заголовка вікна (Windows 10/11)."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


def style_widget(w, state: dict) -> None:
    cls = w.winfo_class()
    if cls.startswith("T") and cls not in ("Text", "Toplevel", "Tk"):
        return                                        # ttk — через ttk.Style
    keys = set(w.keys())
    first = str(w) not in state["seen"]
    state["seen"].add(str(w))
    ch = {}
    accent = False
    if "background" in keys:
        cur = str(w.cget("background"))
        new = map_bg(w, cur, cls)
        accent = new in ("#0b3d1a", "#3d2a00", "#3d0b0b", "#0b2a3d")
        if new != cur:
            ch["background"] = new
    if "foreground" in keys:
        cur = str(w.cget("foreground"))
        new = map_fg(w, cur, accent)
        if new != cur:
            ch["foreground"] = new
    if "font" in keys:
        nf = _font(w, w.cget("font"), state["fonts"])
        if nf:
            ch["font"] = nf
    if first:
        common = {"highlightbackground": DARK, "highlightcolor": FG, "activebackground": FG,
                  "activeforeground": BG, "disabledforeground": DARK, "insertbackground": FG,
                  "selectbackground": FG, "selectforeground": BG, "troughcolor": BG_FIELD,
                  "readonlybackground": BG_FIELD, "disabledbackground": BG_FIELD}
        if cls in ("Checkbutton", "Radiobutton"):
            common.update(selectcolor=BG_FIELD, activebackground=BG, activeforeground=FG_HI)
        if cls in ("Checkbutton", "Radiobutton", "Label", "Frame", "Scale", "Labelframe"):
            ch["highlightthickness"] = 0
        if cls in ("Button", "Menubutton"):
            ch.update(relief="flat", borderwidth=0, highlightthickness=1, cursor="hand2")
        if cls in FIELD_CLASSES:
            ch.update(relief="flat", highlightthickness=1)
        if cls == "Menu":
            common.update(activebackground=FG, activeforeground=BG)
        for k, v in common.items():
            if k in keys:
                ch[k] = v
        if cls in ("Toplevel", "Tk"):
            _dark_titlebar(w)
    if ch:
        try:
            w.configure(**ch)
        except Exception:  # noqa: BLE001
            pass


def _walk(w, state: dict) -> None:
    try:
        style_widget(w, state)
        kids = w.winfo_children()
    except Exception:  # noqa: BLE001 — віджет уже знищено
        return
    for c in kids:
        _walk(c, state)


def style_ttk(root) -> None:
    from tkinter import ttk
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass
    f = (mono(), 10)
    s.configure(".", background=BG, foreground=FG, fieldbackground=BG_FIELD, bordercolor=DARK,
                lightcolor=BG_BTN, darkcolor=BG, troughcolor=BG_FIELD, selectbackground=FG,
                selectforeground=BG, insertcolor=FG, arrowcolor=FG, focuscolor=FG, font=f)
    s.map(".", foreground=[("disabled", DARK)])
    s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(2, 4, 2, 0))
    s.configure("TNotebook.Tab", background=BG_BTN, foreground=DIM, padding=(14, 5), bordercolor=DARK,
                lightcolor=BG_BTN, font=(mono(), 10, "bold"))
    s.map("TNotebook.Tab", background=[("selected", BG_SEL), ("active", "#082a12")],
          foreground=[("selected", FG_HI), ("active", FG)])
    s.configure("TProgressbar", background=FG, troughcolor=BG_FIELD, bordercolor=DARK, lightcolor=FG_HI,
                darkcolor="#16a344", thickness=14)
    s.configure("TCombobox", fieldbackground=BG_FIELD, foreground=FG, background=BG_BTN, arrowcolor=FG,
                selectbackground=BG_FIELD, selectforeground=FG_HI)
    s.map("TCombobox", fieldbackground=[("readonly", BG_FIELD)], foreground=[("readonly", FG)],
          background=[("active", BG_SEL)])
    s.configure("TButton", background=BG_BTN, foreground=FG, bordercolor=DARK, padding=(10, 4))
    s.map("TButton", background=[("active", FG)], foreground=[("active", BG)])
    s.configure("TCheckbutton", background=BG, foreground=FG, indicatorbackground=BG_FIELD,
                indicatorforeground=FG)
    s.configure("TRadiobutton", background=BG, foreground=FG, indicatorbackground=BG_FIELD,
                indicatorforeground=FG)
    s.map("TCheckbutton", background=[("active", BG)], foreground=[("active", FG_HI)])
    s.map("TRadiobutton", background=[("active", BG)], foreground=[("active", FG_HI)])
    s.configure("TScrollbar", background=BG_BTN, troughcolor=BG, arrowcolor=FG, bordercolor=DARK)
    s.configure("Treeview", background=BG_FIELD, fieldbackground=BG_FIELD, foreground=FG)
    s.configure("Treeview.Heading", background=BG_BTN, foreground=FG_HI)
    s.configure("TEntry", fieldbackground=BG_FIELD, foreground=FG, insertcolor=FG)
    s.configure("TSpinbox", fieldbackground=BG_FIELD, foreground=FG, arrowcolor=FG)
    s.configure("TLabelframe", background=BG, bordercolor=DARK)
    s.configure("TLabelframe.Label", background=BG, foreground=FG_HI)
    for o, v in (("*TCombobox*Listbox.background", BG_FIELD), ("*TCombobox*Listbox.foreground", FG),
                 ("*TCombobox*Listbox.selectBackground", FG), ("*TCombobox*Listbox.selectForeground", BG),
                 ("*TCombobox*Listbox.font", f)):
        root.option_add(o, v)


def apply(root) -> None:
    """Перефарбувати вікно й надалі підхоплювати нові віджети (раз на 0,6 с)."""
    import tkinter.font as tkfont
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                 "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont", "TkFixedFont"):
        try:
            tkfont.nametofont(name, root=root).configure(family=mono())
        except Exception:  # noqa: BLE001
            pass
    for o, v in (("*Background", BG), ("*Foreground", FG), ("*selectBackground", FG),
                 ("*selectForeground", BG), ("*insertBackground", FG), ("*activeBackground", FG),
                 ("*activeForeground", BG), ("*highlightBackground", DARK), ("*Menu.background", BG_FIELD)):
        root.option_add(o, v, 30)
    try:
        style_ttk(root)
    except Exception:  # noqa: BLE001
        pass
    state = {"seen": set(), "fonts": {}}
    root.configure(background=BG)

    def tick():
        try:
            if not root.winfo_exists():
                return
            _walk(root, state)
            root.after(600, tick)
        except Exception:  # noqa: BLE001
            pass
    tick()


def install() -> None:
    """Кожне нове вікно Tk — у вигляді терміналу (якщо не вимкнено)."""
    global _installed
    if _installed or not enabled():
        return
    try:
        import tkinter as tk
    except Exception:  # noqa: BLE001
        return
    _installed = True
    orig = tk.Tk.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        try:
            apply(self)
        except Exception:  # noqa: BLE001
            pass
    tk.Tk.__init__ = init


def on() -> bool:
    return _installed


# ------------------------------------------------------------------ термінальні віджети
def bar(frac: float, width: int = 28) -> str:
    frac = max(0.0, min(1.0, frac or 0.0))
    full = int(round(frac * width))
    return "█" * full + "░" * (width - full)


def mmss(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def banner(parent, title: str, status: str = "СИСТЕМА ГОТОВА"):
    """Смуга зверху: «█ РІЗАК // …  ·  статус  ·  годинник», курсор блимає. Повертає функцію set_status(текст, колір)."""
    import tkinter as tk
    f = tk.Frame(parent, bg=BG)
    left = tk.Label(f, text="", bg=BG, fg=FG_HI, font=(mono(), 11, "bold"), anchor="w")
    left.pack(side="left", padx=(10, 0), pady=(6, 2))
    clock = tk.Label(f, text="", bg=BG, fg=DIM, font=(mono(), 10), anchor="e")
    clock.pack(side="right", padx=10)
    st = {"status": status, "color": FG, "blink": True}
    stat = tk.Label(f, text="", bg=BG, fg=FG, font=(mono(), 10), anchor="e")
    stat.pack(side="right", padx=10)
    line = tk.Frame(parent, bg=DARK, height=1)

    def tick():
        if not f.winfo_exists():
            return
        st["blink"] = not st["blink"]
        left.config(text=f"█ {title}{' _' if st['blink'] else '  '}")
        stat.config(text=f"[ {st['status']} ]", fg=st["color"])
        clock.config(text=time.strftime("%d.%m.%Y  %H:%M:%S"))
        f.after(500, tick)

    def set_status(text: str, color: str = FG):
        st["status"], st["color"] = text, color
    f.pack(fill="x")
    line.pack(fill="x", padx=10, pady=(0, 4))
    tick()
    return set_status


def console(parent, height: int = 12):
    """Консоль журналу: рядки з часом, помилки червоним, попередження бурштиновим, етапи — яскраво.
    Повертає (віджет, append(текст))."""
    import tkinter as tk
    frame = tk.Frame(parent, bg=BG)
    txt = tk.Text(frame, height=height, bg=BG_FIELD, fg=FG, insertbackground=FG, relief="flat",
                  highlightthickness=1, highlightbackground=DARK, font=(mono(), 9), wrap="word", state="disabled")
    sb = tk.Scrollbar(frame, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    for tag, col in (("t", DARK), ("err", ERR), ("warn", AMBER), ("stage", FG_HI), ("ok", FG), ("info", CYAN)):
        txt.tag_configure(tag, foreground=col)

    def append(text: str):
        if not txt.winfo_exists():
            return
        txt.configure(state="normal")
        for ln in str(text).splitlines() or [""]:
            low = ln.lower()
            tag = ("err" if ("помилк" in low or "error" in low or "traceback" in low or "не вдал" in low) else
                   "warn" if ("[увага]" in low or "warning" in low or "попередж" in low) else
                   "stage" if ln.startswith(("— ", ">> ", "▶")) else
                   "info" if ln.startswith(("Лог-файл", "Запис:", "Реплей")) else "ok")
            txt.insert("end", time.strftime("%H:%M:%S "), "t")
            txt.insert("end", ln + "\n", tag)
        txt.see("end")
        txt.configure(state="disabled")
    return frame, append


# Етапи обробки запису (autocut/core/pipeline.py: stage(назва, від, до)) — для дошки етапів.
STAGES = [
    ("Аналіз запису", "АНАЛІЗ ЗАПИСУ", 0.0, 0.01),
    ("Розпізнавання екрану", "СКАН ЕКРАНУ · шкода · засвіт · таймер", 0.01, 0.45),
    ("Підготовка звуку", "ЗВУК · відділення голосу від гри", 0.45, 0.62),
    ("Звуки гри", "ЗВУКИ ГРИ І ГОЛОС", 0.62, 0.66),
    ("Відбір моментів", "ВІДБІР МОМЕНТІВ", 0.66, 0.68),
    ("Субтитри", "СУБТИТРИ", 0.68, 0.80),
    ("Рендер MP4", "РЕНДЕР MP4 · 1080×1920", 0.80, 0.99),
    ("Звіт і маркери", "ЗВІТ І МАРКЕРИ", 0.99, 1.0),
]
_STAGE_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")


class StageBoard:
    """Дошка етапів: кожен запис × кожен етап — [ OK ] / [ >> ] / [ .. ] / [ -- ] / [FAIL], час і смуга."""

    def __init__(self, recordings: list[str]):
        self.recs = list(recordings) or ["запис"]
        self.status: dict = {}           # (i, k) -> ok / run / skip / fail
        self.t0: dict = {}
        self.t1: dict = {}
        self.cur = None                  # (i, k)
        self.local = 0.0
        self.frac = 0.0

    @staticmethod
    def match(name: str):
        m = _STAGE_RE.match(name or "")
        i, rest = (int(m.group(1)) - 1, m.group(3)) if m else (0, name or "")
        for k, (key, *_r) in enumerate(STAGES):
            if rest.startswith(key):
                return i, k
        return None

    def update(self, name: str, frac: float, now: float | None = None) -> None:
        now = now or time.time()
        self.frac = frac
        pos = self.match(name)
        if pos is None:
            return
        i, k = pos
        if pos != self.cur:
            for (ii, kk), s in list(self.status.items()):
                if s == "run":
                    self.status[(ii, kk)] = "ok"
                    self.t1[(ii, kk)] = now
            for ii in range(len(self.recs)):         # пропущені (вимкнені) етапи до поточного
                for kk in range(len(STAGES)):
                    if (ii, kk) < (i, k) and (ii, kk) not in self.status:
                        self.status[(ii, kk)] = "skip"
            self.status[pos] = "run"
            self.t0[pos] = now
            self.cur = pos
        a, b = STAGES[k][2], STAGES[k][3]
        local = frac * len(self.recs) - i
        self.local = max(0.0, min(1.0, (local - a) / (b - a))) if b > a else 0.0

    def finish(self, ok: bool, now: float | None = None) -> None:
        now = now or time.time()
        for pos, s in list(self.status.items()):
            if s == "run":
                self.status[pos] = "ok" if ok else "fail"
                self.t1[pos] = now
        if ok:
            self.frac = 1.0
            for ii in range(len(self.recs)):
                for kk in range(len(STAGES)):
                    self.status.setdefault((ii, kk), "skip")

    def lines(self, blink: bool = True, now: float | None = None) -> list[tuple[str, str]]:
        """[(текст, тег)] — тег: head / ok / run / wait / skip / fail."""
        now = now or time.time()
        out = []
        for i, rec in enumerate(self.recs):
            states = [self.status.get((i, k)) for k in range(len(STAGES))]
            done = all(s in ("ok", "skip") for s in states)
            mark = "✓" if done else ("▶" if any(s == "run" for s in states) else "·")
            out.append((f"{mark} ЗАПИС {i + 1}/{len(self.recs)}  {Path(rec).name}", "head"))
            for k, (_key, title, *_r) in enumerate(STAGES):
                s = states[k]
                pos = (i, k)
                if s == "ok":
                    out.append((f"   [ OK ]  {mmss(self.t1.get(pos, now) - self.t0.get(pos, now))}  {title}", "ok"))
                elif s == "run":
                    tag = "[ >> ]" if blink else "[ >  ]"
                    out.append((f"   {tag}  {mmss(now - self.t0.get(pos, now))}  {title}   "
                                f"{bar(self.local, 20)} {int(self.local * 100):3d}%", "run"))
                elif s == "fail":
                    out.append((f"   [FAIL]  {mmss(self.t1.get(pos, now) - self.t0.get(pos, now))}  {title}", "fail"))
                elif s == "skip":
                    out.append((f"   [ -- ]         {title} · пропущено", "skip"))
                else:
                    out.append((f"   [ .. ]         {title}", "wait"))
        return out


def board_widget(parent, board: StageBoard, height: int = 14):
    """Text, який раз на 0,5 с перемальовує дошку етапів. Повертає віджет."""
    import tkinter as tk
    txt = tk.Text(parent, height=height, bg=BG_FIELD, fg=FG, relief="flat", highlightthickness=1,
                  highlightbackground=DARK, font=(mono(), 10), wrap="none", state="disabled", cursor="arrow")
    for tag, col in (("head", FG_HI), ("ok", FG), ("run", FG_HI), ("wait", DARK), ("skip", DARK),
                     ("fail", ERR)):
        txt.tag_configure(tag, foreground=col)
    txt.tag_configure("run", background=BG_SEL)
    st = {"blink": True}

    def draw():
        if not txt.winfo_exists():
            return
        st["blink"] = not st["blink"]
        y = txt.yview()[0]
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for text, tag in board.lines(st["blink"]):
            txt.insert("end", text + "\n", tag)
        txt.configure(state="disabled")
        run = next((n for n, (_t, tag) in enumerate(board.lines(True)) if tag == "run"), None)
        if run is not None:
            txt.see(f"{run + 1}.0")
        else:
            txt.yview_moveto(y)
        txt.after(500, draw)
    draw()
    return txt
