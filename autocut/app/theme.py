"""Сучасний темний вигляд: графітовий фон, м'який світлий текст, зелений — лише акценти; етапи роботи на екрані.

Підключається само (autocut/app/__init__.py → install()): кожне вікно Tk перефарбовується, а нові
віджети підхоплюються на льоту. Кольори, які код ставить сам (зелена кнопка «Почати», помилка червоним,
підказка сірим), переводяться в палітру за змістом: зелене → зелений акцент, червоне → червоне,
помаранчеве → бурштинове, сіре → приглушене.

Вікно підлаштовується під екран (16:10, 16:9, широкий): розмір — частка екрана, по центру.
Повернути звичайний вигляд: кнопка «◐ Вигляд» у вікні або файл config\\ui_theme.txt зі словом classic.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEME_FILE = ROOT / "config" / "ui_theme.txt"

BG = "#0f1419"          # фон вікна
BG_FIELD = "#161c24"    # поля вводу, списки, журнал
BG_BTN = "#212833"      # кнопки
BG_HOVER = "#2c3542"    # кнопка під курсором
BG_SEL = "#1d2a22"      # вибраний рядок / активний етап
BORDER = "#2d3640"      # рамки
FG = "#d5dbe3"          # основний текст (не білий — не ріже очі)
FG_HI = "#f2f5f8"       # заголовки
DIM = "#8a96a3"         # підказки
DARK = "#4b5562"        # ще не почате / пропущено
ACCENT = "#3fb96b"      # зелений акцент
ACCENT_BG = "#23864a"   # зелена кнопка
AMBER = "#e0a536"       # увага
AMBER_BG = "#8a5a0c"
ERR = "#f26d6d"         # помилка
ERR_BG = "#a83232"
CYAN = "#6cb6ff"        # інформація
CYAN_BG = "#1f5fbf"
ACCENT_BGS = {ACCENT_BG, AMBER_BG, ERR_BG, CYAN_BG}
PALETTE = {BG, BG_FIELD, BG_BTN, BG_HOVER, BG_SEL, BORDER, FG, FG_HI, DIM, DARK, ACCENT, AMBER, ERR, CYAN,
           "#ffffff"} | ACCENT_BGS

FIELD_CLASSES = {"Text", "Entry", "Listbox", "Spinbox"}
_installed = False
_fonts: dict = {}


# ------------------------------------------------------------------ увімкнено?
def enabled() -> bool:
    try:
        return THEME_FILE.read_text(encoding="utf-8").strip().lower() != "classic"
    except OSError:
        return True


def set_enabled(on: bool) -> None:
    THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
    THEME_FILE.write_text("modern" if on else "classic", encoding="utf-8")


# ------------------------------------------------------------------ шрифти
def _family(cands) -> str:
    key = tuple(cands)
    if key not in _fonts:
        try:
            import tkinter.font as tkfont
            fams = set(tkfont.families())
        except Exception:  # noqa: BLE001
            fams = set()
        _fonts[key] = next((f for f in cands if f in fams), cands[-1])
    return _fonts[key]


def ui() -> str:
    return _family(("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", "TkDefaultFont"))


def mono() -> str:
    return _family(("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Courier New", "Courier"))


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
    if on_accent:
        return "#ffffff"
    rgb = _rgb(w, color)
    if rgb is None:
        return FG
    return {"red": ERR, "amber": AMBER, "green": ACCENT, "blue": CYAN, "mid": DIM}.get(_hue(rgb), FG)


def map_bg(w, color: str, cls: str) -> str:
    if cls in ("Button", "Menubutton") and color in (BG, BG_FIELD):
        return BG_BTN                       # кнопка не має зливатися з фоном
    if cls in FIELD_CLASSES and color == BG:
        return BG_FIELD
    if color in PALETTE:
        return color
    rgb = _rgb(w, color) if color else None
    kind = _hue(rgb) if rgb else "light"
    colored = {"green": ACCENT_BG, "amber": AMBER_BG, "red": ERR_BG, "blue": CYAN_BG}
    if cls in ("Button", "Menubutton"):
        return colored.get(kind, BG_BTN)
    if cls in FIELD_CLASSES:
        return BG_FIELD
    if kind in colored:                     # кольорова плашка-підказка — приглушена
        return {"green": BG_SEL, "amber": "#2e2513", "red": "#2e1717", "blue": "#15233a"}[kind]
    return BG


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
        accent = new in ACCENT_BGS
        if new != cur:
            ch["background"] = new
    if "foreground" in keys:
        cur = str(w.cget("foreground"))
        new = map_fg(w, cur, accent)
        if new != cur:
            ch["foreground"] = new
    if first:
        common = {"highlightbackground": BORDER, "highlightcolor": ACCENT, "activebackground": BG_HOVER,
                  "activeforeground": FG_HI, "disabledforeground": DARK, "insertbackground": FG,
                  "selectbackground": ACCENT_BG, "selectforeground": "#ffffff", "troughcolor": BG_FIELD,
                  "readonlybackground": BG_FIELD, "disabledbackground": BG_FIELD}
        if cls in ("Checkbutton", "Radiobutton"):
            common.update(selectcolor=BG_FIELD, activebackground=BG, activeforeground=FG_HI)
        if cls in ("Checkbutton", "Radiobutton", "Label", "Frame", "Scale", "Labelframe"):
            ch["highlightthickness"] = 0
        if cls in ("Button", "Menubutton"):
            ch.update(relief="flat", borderwidth=0, highlightthickness=0, cursor="hand2", padx=12, pady=5)
            if accent:
                common["activebackground"] = ch.get("background", w.cget("background"))
        if cls in FIELD_CLASSES:
            ch.update(relief="flat", borderwidth=0, highlightthickness=1)
        if cls == "Labelframe":
            ch.update(relief="solid", borderwidth=1)
        if cls == "Menu":
            common.update(activebackground=ACCENT_BG, activeforeground="#ffffff", relief="flat")
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
    f = (ui(), 10)
    s.configure(".", background=BG, foreground=FG, fieldbackground=BG_FIELD, bordercolor=BORDER,
                lightcolor=BG_FIELD, darkcolor=BG_FIELD, troughcolor=BG_FIELD, selectbackground=ACCENT_BG,
                selectforeground="#ffffff", insertcolor=FG, arrowcolor=DIM, focuscolor=BG, font=f)
    s.map(".", foreground=[("disabled", DARK)])
    s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(8, 6, 8, 0))
    s.configure("TNotebook.Tab", background=BG, foreground=DIM, padding=(16, 7), borderwidth=0,
                bordercolor=BG, lightcolor=BG, font=(ui(), 10, "bold"))
    s.map("TNotebook.Tab", background=[("selected", BG_FIELD), ("active", BG_FIELD)],
          foreground=[("selected", FG_HI), ("active", FG)],
          lightcolor=[("selected", ACCENT)], bordercolor=[("selected", BG_FIELD)])
    s.configure("TProgressbar", background=ACCENT, troughcolor=BG_BTN, bordercolor=BG_BTN, lightcolor=ACCENT,
                darkcolor=ACCENT, thickness=8)
    s.configure("TCombobox", fieldbackground=BG_FIELD, foreground=FG, background=BG_BTN, arrowcolor=DIM,
                bordercolor=BORDER, selectbackground=BG_FIELD, selectforeground=FG_HI, padding=4)
    s.map("TCombobox", fieldbackground=[("readonly", BG_FIELD)], foreground=[("readonly", FG)],
          background=[("active", BG_HOVER)], bordercolor=[("focus", ACCENT)])
    s.configure("TButton", background=BG_BTN, foreground=FG, bordercolor=BG_BTN, padding=(12, 5))
    s.map("TButton", background=[("active", BG_HOVER)], foreground=[("active", FG_HI)])
    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=BG, foreground=FG, indicatorbackground=BG_FIELD, indicatorforeground=ACCENT)
        s.map(w, background=[("active", BG)], foreground=[("active", FG_HI)])
    s.configure("TScrollbar", background=BG_BTN, troughcolor=BG, arrowcolor=DIM, bordercolor=BG,
                lightcolor=BG_BTN, darkcolor=BG_BTN)
    s.map("TScrollbar", background=[("active", BG_HOVER)])
    s.configure("Treeview", background=BG_FIELD, fieldbackground=BG_FIELD, foreground=FG, bordercolor=BORDER)
    s.configure("Treeview.Heading", background=BG_BTN, foreground=FG_HI)
    s.configure("TEntry", fieldbackground=BG_FIELD, foreground=FG, insertcolor=FG, bordercolor=BORDER)
    s.configure("TSpinbox", fieldbackground=BG_FIELD, foreground=FG, arrowcolor=DIM, bordercolor=BORDER)
    s.configure("TLabelframe", background=BG, bordercolor=BORDER)
    s.configure("TLabelframe.Label", background=BG, foreground=FG_HI)
    for o, v in (("*TCombobox*Listbox.background", BG_FIELD), ("*TCombobox*Listbox.foreground", FG),
                 ("*TCombobox*Listbox.selectBackground", ACCENT_BG), ("*TCombobox*Listbox.selectForeground", "#ffffff"),
                 ("*TCombobox*Listbox.font", f)):
        root.option_add(o, v)


def fit_window(root, want_w: int = 1040, want_h: int = 860, min_w: int = 760, min_h: int = 560) -> None:
    """Розмір вікна під екран (16:10, 16:9, широкий): не більше 92 % висоти й 80 % ширини, по центру."""
    try:
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = int(max(min_w, min(want_w, sw * 0.8)))
        h = int(max(min_h, min(want_h, sh * 0.88)))
        root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        root.minsize(min(min_w, w), min(min_h, h))
    except Exception:  # noqa: BLE001
        pass


def apply(root) -> None:
    """Перефарбувати вікно й надалі підхоплювати нові віджети (раз на 0,6 с)."""
    import tkinter.font as tkfont
    for name, fam in (("TkDefaultFont", ui()), ("TkTextFont", ui()), ("TkMenuFont", ui()),
                      ("TkHeadingFont", ui()), ("TkCaptionFont", ui()), ("TkFixedFont", mono())):
        try:
            tkfont.nametofont(name, root=root).configure(family=fam)
        except Exception:  # noqa: BLE001
            pass
    for o, v in (("*Background", BG), ("*Foreground", FG), ("*selectBackground", ACCENT_BG),
                 ("*selectForeground", "#ffffff"), ("*insertBackground", FG), ("*activeBackground", BG_HOVER),
                 ("*activeForeground", FG_HI), ("*highlightBackground", BORDER), ("*Menu.background", BG_FIELD)):
        root.option_add(o, v, 30)
    try:
        style_ttk(root)
    except Exception:  # noqa: BLE001
        pass
    state = {"seen": set()}
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


def _dpi_aware() -> None:
    """Чіткий текст на Windows із масштабом 125–200 % (інакше Windows розтягує вікно й текст розмитий)."""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def install() -> None:
    """Кожне нове вікно Tk — у сучасному темному вигляді (якщо не вимкнено)."""
    global _installed
    if _installed or not enabled():
        return
    try:
        import tkinter as tk
    except Exception:  # noqa: BLE001
        return
    _installed = True
    _dpi_aware()
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


# ------------------------------------------------------------------ віджети: шапка, журнал, етапи
def bar(frac: float, width: int = 28) -> str:
    frac = max(0.0, min(1.0, frac or 0.0))
    full = int(round(frac * width))
    return "█" * full + "░" * (width - full)


def mmss(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


STATUS_COLORS = {"ok": ACCENT_BG, "busy": CYAN_BG, "warn": AMBER_BG, "err": ERR_BG, "idle": BG_BTN}


def banner(parent, title: str, subtitle: str = "", status: str = "Готово до роботи"):
    """Шапка: назва, підзаголовок, «таблетка» стану й годинник. Повертає set_status(текст, вид)
    — вид: idle / busy / ok / warn / err."""
    import tkinter as tk
    f = tk.Frame(parent, bg=BG)
    left = tk.Frame(f, bg=BG)
    left.pack(side="left", padx=(16, 0), pady=(10, 8))
    tk.Label(left, text="●", bg=BG, fg=ACCENT, font=(ui(), 13)).pack(side="left")
    tk.Label(left, text=f" {title}", bg=BG, fg=FG_HI, font=(ui(), 13, "bold")).pack(side="left")
    if subtitle:
        tk.Label(left, text=f"   {subtitle}", bg=BG, fg=DIM, font=(ui(), 10)).pack(side="left", pady=(3, 0))
    clock = tk.Label(f, text="", bg=BG, fg=DIM, font=(ui(), 10))
    clock.pack(side="right", padx=16)
    pill = tk.Label(f, text="", bg=BG_BTN, fg="#ffffff", font=(ui(), 9, "bold"), padx=10, pady=2)
    pill.pack(side="right")
    st = {"status": status, "kind": "idle"}

    def tick():
        if not f.winfo_exists():
            return
        pill.config(text=st["status"].upper(), bg=STATUS_COLORS.get(st["kind"], BG_BTN))
        clock.config(text=time.strftime("%H:%M:%S   %d.%m.%Y"))
        f.after(1000, tick)

    def set_status(text: str, kind: str = "busy"):
        st["status"], st["kind"] = text, kind
        try:
            pill.config(text=text.upper(), bg=STATUS_COLORS.get(kind, BG_BTN))
        except Exception:  # noqa: BLE001
            pass
    f.pack(fill="x")
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x")
    tick()
    return set_status


def console(parent, height: int = 12):
    """Журнал: рядки з часом, помилки червоним, увага — бурштиновим, етапи — зеленим. Повертає (рамка, append)."""
    import tkinter as tk
    frame = tk.Frame(parent, bg=BG_FIELD, highlightthickness=1, highlightbackground=BORDER)
    txt = tk.Text(frame, height=height, bg=BG_FIELD, fg=FG, insertbackground=FG, relief="flat", borderwidth=0,
                  highlightthickness=0, font=(mono(), 9), wrap="word", state="disabled", padx=10, pady=8,
                  spacing1=1, spacing3=1)
    from tkinter import ttk
    sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    for tag, col in (("t", DARK), ("err", ERR), ("warn", AMBER), ("stage", ACCENT), ("ok", FG), ("info", CYAN)):
        txt.tag_configure(tag, foreground=col)

    def append(text: str):
        if not txt.winfo_exists():
            return
        txt.configure(state="normal")
        for ln in str(text).splitlines() or [""]:
            low = ln.lower()
            tag = ("err" if ("помилк" in low or "error" in low or "traceback" in low or "не вдал" in low) else
                   "warn" if ("[увага]" in low or "warning" in low or "попередж" in low) else
                   "stage" if ln.startswith(("— ", ">> ", "▶", "✓")) else
                   "info" if ln.startswith(("Лог-файл", "Запис:", "Реплей")) else "ok")
            txt.insert("end", time.strftime("%H:%M:%S  "), "t")
            txt.insert("end", ln + "\n", tag)
        txt.see("end")
        txt.configure(state="disabled")
    return frame, append


# Етапи обробки запису (autocut/core/pipeline.py: stage(назва, від, до)) — для списку етапів.
STAGES = [
    ("Аналіз запису", "Аналіз запису", 0.0, 0.01),
    ("Розпізнавання екрану", "Скан екрану · шкода, засвіт, таймер", 0.01, 0.45),
    ("Підготовка звуку", "Звук · відділення голосу від гри", 0.45, 0.62),
    ("Звуки гри", "Звуки гри і голос", 0.62, 0.66),
    ("Відбір моментів", "Відбір моментів", 0.66, 0.68),
    ("Субтитри", "Субтитри", 0.68, 0.80),
    ("Рендер MP4", "Рендер MP4 · 1080×1920", 0.80, 0.99),
    ("Звіт і маркери", "Звіт і маркери", 0.99, 1.0),
]
_STAGE_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")
MARKS = {"ok": "✓", "run": "▶", "wait": "○", "skip": "–", "fail": "✕"}


class StageBoard:
    """Список етапів: кожен запис × кожен етап — ✓ готово / ▶ іде / ○ попереду / – пропущено / ✕ помилка,
    час етапу й смуга поточного."""

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
            for p, s in list(self.status.items()):
                if s == "run":
                    self.status[p] = "ok"
                    self.t1[p] = now
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

    def rows(self, now: float | None = None) -> list[dict]:
        """Рядки для показу: {kind: head|stage, state, title, time, frac}."""
        now = now or time.time()
        out = []
        for i, rec in enumerate(self.recs):
            states = [self.status.get((i, k)) or "wait" for k in range(len(STAGES))]
            head = "ok" if all(s in ("ok", "skip") for s in states) else \
                "fail" if "fail" in states else "run" if "run" in states else "wait"
            out.append({"kind": "head", "state": head, "title": f"Запис {i + 1} з {len(self.recs)} · {Path(rec).name}",
                        "time": "", "frac": None})
            for k, (_key, title, *_r) in enumerate(STAGES):
                s, pos = states[k], (i, k)
                t = ""
                if s in ("ok", "fail"):
                    t = mmss(self.t1.get(pos, now) - self.t0.get(pos, now))
                elif s == "run":
                    t = mmss(now - self.t0.get(pos, now))
                out.append({"kind": "stage", "state": s, "title": title + (" · пропущено" if s == "skip" else ""),
                            "time": t, "frac": self.local if s == "run" else None})
        return out


def board_widget(parent, board: StageBoard, height: int = 14):
    """Text, який раз на 0,5 с перемальовує список етапів. Повертає рамку."""
    import tkinter as tk
    frame = tk.Frame(parent, bg=BG_FIELD, highlightthickness=1, highlightbackground=BORDER)
    txt = tk.Text(frame, height=height, bg=BG_FIELD, fg=FG, relief="flat", borderwidth=0, highlightthickness=0,
                  font=(ui(), 10), wrap="none", state="disabled", cursor="arrow", padx=12, pady=8,
                  spacing1=3, spacing3=3, tabs=("22p", "240p"))
    txt.pack(fill="both", expand=True)
    tm = (mono(), 9)
    for tag, col in (("m_ok", ACCENT), ("m_run", CYAN), ("m_wait", DARK), ("m_skip", DARK), ("m_fail", ERR),
                     ("t_ok", FG), ("t_run", FG_HI), ("t_wait", DIM), ("t_skip", DARK), ("t_fail", ERR),
                     ("time", DIM), ("bar_on", ACCENT), ("bar_off", BG_HOVER), ("pct", DIM)):
        txt.tag_configure(tag, foreground=col)
    txt.tag_configure("time", font=tm)
    txt.tag_configure("pct", font=tm)
    txt.tag_configure("head", foreground=FG_HI, font=(ui(), 10, "bold"), spacing1=8)
    txt.tag_configure("runline", background=BG_SEL)

    def draw():
        if not txt.winfo_exists():
            return
        y = txt.yview()[0]
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        run_line = None
        for n, r in enumerate(board.rows(), start=1):
            s = r["state"]
            if r["kind"] == "head":
                txt.insert("end", MARKS.get(s, "○") + "  ", f"m_{s}")
                txt.insert("end", r["title"] + "\n", "head")
                continue
            start = txt.index("end-1c")
            txt.insert("end", "     " + MARKS.get(s, "○"), f"m_{s}")
            txt.insert("end", "\t" + r["title"], f"t_{s}")
            txt.insert("end", "\t" + (r["time"] or ""), "time")
            txt.insert("end", "\n")
            if r["frac"] is not None:              # смуга поточного етапу — окремим рядком під ним
                full = int(round(r["frac"] * 30))
                txt.insert("end", "\t" + "━" * full, "bar_on")
                txt.insert("end", "━" * (30 - full), "bar_off")
                txt.insert("end", f"  {int(r['frac'] * 100)}%\n", "pct")
                run_line = n
            if s == "run":
                txt.tag_add("runline", start, txt.index("end-1c"))
        txt.configure(state="disabled")
        if run_line is not None:
            txt.see(f"{min(run_line + 3, n)}.0")
        else:
            txt.yview_moveto(y)
        txt.after(500, draw)
    draw()
    return frame
