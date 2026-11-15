"""Картинка з текстом для вшивання в MP4: великий білий шрифт, чорний контур, тінь, кольорові емодзі.

Рендеримо через Pillow (а не через libass у ffmpeg), бо так працюють кольорові емодзі
(шрифт Segoe UI Emoji у Windows) і не треба боротися з екрануванням шляхів у ffmpeg.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("textimg")

_EMOJI_RE = re.compile(
    "([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\U0001F1E6-\U0001F1FF]"
    "(?:[\uFE0F\u200D\U0001F3FB-\U0001F3FF]|[\U0001F000-\U0001FAFF\u2600-\u27BF])*)")

TEXT_FALLBACKS = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"]
EMOJI_FALLBACKS = ["C:/Windows/Fonts/seguiemj.ttf", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"]
_warned: set[str] = set()


@lru_cache(maxsize=32)
def _font(path: str, size: int):
    return ImageFont.truetype(path, size)


def load_font(preferred: str, size: int, fallbacks: list[str]):
    for p in [preferred] + fallbacks:
        if not p:
            continue
        try:
            return _font(p, size)
        except OSError:
            continue
    if "text" not in _warned:
        log.warning("Не знайдено шрифт %s — використовую стандартний (може бути без кирилиці).", preferred)
        _warned.add("text")
    return ImageFont.load_default()


def split_runs(line: str) -> list[tuple[str, bool]]:
    """Рядок -> [(фрагмент, це_емодзі)]."""
    out = []
    pos = 0
    for m in _EMOJI_RE.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], False))
    return out


def _emoji_font(style: dict, size: int):
    for p in [style.get("emoji_font")] + EMOJI_FALLBACKS:
        if not p or not Path(p).exists():
            continue
        try:
            return _font(p, size)
        except OSError:
            # Noto Color Emoji має лише розмір 109 — масштабуємо окремо
            try:
                return _font(p, 109)
            except OSError:
                continue
    return None


def _measure(line: str, style: dict, size: int) -> int:
    """Ширина рядка в пікселях (так само, як у _render), з полями під контур і тінь."""
    font = load_font(style.get("font", ""), size, TEXT_FALLBACKS)
    w = 0
    for frag, is_emoji in split_runs(line):
        if is_emoji:
            n = len([c for c in frag if ord(c) > 0x2000 and c not in "\uFE0F\u200D"])
            w += max(int(size * 1.15) * n, int(size * 1.15))
        else:
            w += int(font.getlength(frag))
    pad = int(style.get("outline_width", 6)) + int(style.get("shadow_offset", 5)) + 8
    return w + 2 * pad


def wrap(text: str, style: dict, size: int, max_width: int, max_lines: int = 3) -> list[str]:
    """Переносить слова на нові рядки, щоб кожен рядок влазив у max_width."""
    out = []
    for para in [p for p in text.split("\n") if p.strip()] or [" "]:
        words, cur = para.split(), ""
        for wd in words:
            test = f"{cur} {wd}".strip()
            if cur and _measure(test, style, size) > max_width:
                out.append(cur)
                cur = wd
            else:
                cur = test
        out.append(cur)
    return out if len(out) <= max_lines else out[:max_lines - 1] + [" ".join(out[max_lines - 1:])]


def render_text(text: str, style: dict, font_size: int | None = None, max_width: int = 1000,
                max_lines: int = 3) -> Image.Image:
    """Повертає RGBA-картинку з текстом, обрізану по вмісту з полями. Довгий текст переноситься на рядки
    (до max_lines); якщо й так не влазить — зменшується шрифт. Ширина НІКОЛИ не більша за max_width."""
    size = int(font_size or style.get("font_size", 66))
    img = None
    for _ in range(10):
        lines = wrap(text, style, size, max_width, max_lines)
        img = _render(lines, style, size)
        if img.width <= max_width or size <= 24:
            break
        size = int(size * 0.9)
    if img.width > max_width:                 # крайній випадок — стиснути, але не обрізати
        k = max_width / img.width
        img = img.resize((max_width, max(1, int(img.height * k))), Image.LANCZOS)
    return img


def add_box(img: Image.Image, alpha: int = 150, radius: int = 28, pad: int = 18) -> Image.Image:
    """Напівпрозора темна плашка під текстом (заклик помітніший на будь-якому фоні)."""
    W, H = img.width + 2 * pad, img.height + 2 * pad
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(out).rounded_rectangle((0, 0, W - 1, H - 1), radius=radius, fill=(0, 0, 0, int(alpha)))
    out.alpha_composite(img, (pad, pad))
    return out


def cta_image(text: str, settings: dict, frame_width: int) -> Image.Image:
    """Заклик: більший шрифт, свій колір, товстий контур, плашка; переноситься на рядки, не виходить за кадр."""
    c = settings.get("cta") or {}
    style = {**(settings.get("subtitles") or {}), "color": c.get("color", "#FFE14D"),
             "outline_width": int(c.get("outline_width", 8))}
    margin = int(c.get("margin", 70))
    box = bool(c.get("box", True))
    max_w = frame_width - 2 * margin - (36 if box else 0)
    img = render_text(text, style, font_size=int(c.get("font_size", 76)), max_width=max_w, max_lines=3)
    return add_box(img, int(c.get("box_alpha", 150))) if box else img


def _render(lines: list[str], style: dict, size: int) -> Image.Image:
    font = load_font(style.get("font", ""), size, TEXT_FALLBACKS)
    efont = _emoji_font(style, size)
    stroke = int(style.get("outline_width", 6))
    shadow = int(style.get("shadow_offset", 5))
    color = ImageColor.getrgb(style.get("color", "#FFFFFF"))
    ocolor = ImageColor.getrgb(style.get("outline_color", "#000000"))
    line_h = int(size * 1.18)
    pad = stroke + shadow + 8

    # ширина кожного рядка
    measured = []
    for line in lines:
        runs = split_runs(line)
        w = 0
        parts = []
        for frag, is_emoji in runs:
            if is_emoji and efont is not None:
                fw = int(size * 1.15) * len([c for c in frag if ord(c) > 0x2000 and c not in "\uFE0F\u200D"])
                fw = max(fw, int(size * 1.15))
            else:
                fw = int(font.getlength(frag))
            parts.append((frag, is_emoji, fw))
            w += fw
        measured.append((parts, w))
    W = max(w for _, w in measured) + 2 * pad
    H = line_h * len(lines) + 2 * pad
    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dt = ImageDraw.Draw(text_layer)
    ds = ImageDraw.Draw(shadow_layer)
    for i, (parts, w) in enumerate(measured):
        x = (W - w) / 2
        y = pad + i * line_h
        for frag, is_emoji, fw in parts:
            if is_emoji and efont is not None:
                _draw_emoji(text_layer, frag, efont, int(x), int(y), size)
            else:
                ds.text((x + shadow, y + shadow), frag, font=font, fill=(0, 0, 0, int(style.get("shadow_alpha", 170))),
                        stroke_width=stroke, stroke_fill=(0, 0, 0, int(style.get("shadow_alpha", 170))))
                dt.text((x, y), frag, font=font, fill=color + (255,), stroke_width=stroke, stroke_fill=ocolor + (255,))
            x += fw
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(max(1, shadow // 2)))
    return Image.alpha_composite(shadow_layer, text_layer)


def _draw_emoji(dst: Image.Image, frag: str, efont, x: int, y: int, size: int):
    try:
        tmp = Image.new("RGBA", (int(efont.size * 1.4 * max(1, len(frag))), int(efont.size * 1.4)), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((0, 0), frag, font=efont, embedded_color=True)
        bbox = tmp.getbbox()
        if not bbox:
            return
        tmp = tmp.crop(bbox)
        k = (size * 1.05) / max(1, tmp.height)
        tmp = tmp.resize((max(1, int(tmp.width * k)), max(1, int(tmp.height * k))), Image.LANCZOS)
        dst.alpha_composite(tmp, (x, y + int(size * 0.08)))
    except Exception as e:  # шрифт без кольорових гліфів тощо
        if "emoji" not in _warned:
            log.warning("Не вдалося намалювати емодзі (%s) — вони будуть пропущені в MP4.", e)
            _warned.add("emoji")
