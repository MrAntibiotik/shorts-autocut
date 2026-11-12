# -*- coding: utf-8 -*-
"""
Генерує ассети універсального opener-шаблону MR_ANTIBIOTIK (16:10, 2560x1600, 60 fps).
Кількість моментів — від 3 до 6: перший = HOOK, останній = ПІК, решта — звичайні.
Запуск: python gen_template.py   (потрібно: pip install pillow numpy scipy; ffmpeg у PATH)
"""
import json, math, random, subprocess, wave
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from scipy.signal import butter, sosfilt

W, H, FPS = 2560, 1600, 60
YEL, WHITE, BG = (245, 196, 0), (255, 255, 255), (10, 12, 15)
F_BLACK, F_MONO = "fonts/InterDisplay-Black.otf", "fonts/DejaVuSansMono-Bold.ttf"
NICK = "MR_ANTIBIOTIK"

# ---- ритм: 120 BPM, усі межі кратні 0.5 с ----
HOOK, REG, PEAK, OUTRO = 2.0, 2.5, 2.5, 3.0
MIN_M, MAX_M = 3, 6
HUD_LEN = HOOK + (MAX_M - 2) * REG + PEAK  # макс. довжина частини з моментами

def timeline(n):
    """n моментів -> список (тип, старт, кінець) + старт заставки."""
    t, out = 0.0, []
    for k in range(n):
        kind = "hook" if k == 0 else ("peak" if k == n - 1 else "moment")
        d = HOOK if kind == "hook" else (PEAK if kind == "peak" else REG)
        out.append((kind, t, t + d)); t += d
    return out, t

def ease(x): x = min(max(x, 0), 1); return 1 - (1 - x) ** 3
def font(sz, p): return ImageFont.truetype(p, sz)

def render(name, nframes, fn, opaque=False):
    if opaque:
        enc = ["-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p"]
    else:
        enc = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30",
               "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-auto-alt-ref", "0"]
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"] + enc + [name], stdin=subprocess.PIPE)
    for i in range(nframes): p.stdin.write(fn(i).tobytes())
    p.stdin.close(); p.wait()

# ================= 1. HUD (віньєтка, кути, REC, таймкод) =================
yy, xx = np.mgrid[0:H, 0:W]
d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
base = np.zeros((H, W, 4), np.uint8); base[..., 3] = (np.clip((d - 0.75) / 0.75, 0, 1) ** 1.6 * 150).astype(np.uint8)
STATIC = Image.fromarray(base, "RGBA"); del yy, xx, d, base
sd = ImageDraw.Draw(STATIC); m, L, th = 64, 130, 8
for cx, cy, sx, sy in [(m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)]:
    for (ax, ay) in [(L, th), (th, L)]:
        x0, x1 = sorted([cx, cx + sx * ax]); y0, y1 = sorted([cy, cy + sy * ay])
        sd.rectangle([x0, y0, x1, y1], fill=YEL)
f_mono = font(44, F_MONO)

def hud(i):
    tt = i / FPS; im = STATIC.copy(); dr = ImageDraw.Draw(im)
    if int(tt * 2) % 2 == 0: dr.ellipse([140, 140, 176, 176], fill=(230, 40, 40, 255))
    dr.text((196, 134), "REC  " + NICK, font=f_mono, fill=WHITE + (235,))
    tc = f"{int(tt // 60):02d}:{int(tt % 60):02d}:{int((tt % 1) * FPS):02d}"
    dr.text((W - 140 - dr.textlength(tc, font=f_mono), 134), tc, font=f_mono, fill=WHITE + (235,))
    return im

# ================= 2. HIT — перехід на склейці (3 кадри глітчу + спалах) =================
HIT_PRE = 3  # кадрів до склейки
hrng = random.Random(7)
def hit(i):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
    if i < HIT_PRE:
        for _ in range(7):
            y = hrng.randint(0, H - 60); h = hrng.randint(8, 60)
            col = hrng.choice([YEL, (0, 220, 255), WHITE])
            dr.rectangle([0, y, W, y + h], fill=col + (hrng.randint(70, 160),))
    else:
        a = int(210 * math.exp(-((i - HIT_PRE) / FPS) / 0.05))
        dr.rectangle([0, 0, W, H], fill=(255, 255, 255, a))
    return im
HIT_FRAMES = HIT_PRE + 12

# ================= 3. PEAK — червоний пульс рамки =================
def peak(i):
    st = i / FPS; im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
    p = 0.5 + 0.5 * math.sin(st * 2 * math.pi * 2)
    dr.rectangle([20, 20, W - 20, H - 20], outline=(230, 40, 40, int(90 + 120 * p)), width=10)
    if st < 0.18: dr.rectangle([0, 0, W, H], fill=(255, 255, 255, int(255 * math.exp(-st / 0.06))))
    return im

# ================= 4. OUTRO — нікнейм з мерехтінням екрану =================
f_logo = font(230, F_BLACK)
OUT_BG = Image.new("RGBA", (W, H), BG + (255,))
lw = ImageDraw.Draw(OUT_BG).textlength(NICK, font=f_logo); LOGO_Y = (H - 230) // 2 - 40
_r = random.Random(42); FLICK = []
for j in range(int(OUTRO * FPS)):
    lt = j / FPS
    if lt < 0.70:   on = _r.random() < 0.15 + lt * 1.1
    elif lt < 2.0:  on = _r.random() > 0.03
    elif lt < 2.45: on = _r.random() < 1.0 - (lt - 2.0) / 0.45
    else:           on = False
    FLICK.append(1 if on else 0)
orng = random.Random(3)
def outro(j):
    lt = j / FPS; im = OUT_BG.copy(); on = FLICK[j]
    unstable = lt < 0.7 or 2.0 <= lt < 2.45
    if on:
        x0 = (W - lw) / 2; jit = orng.choice([0, 0, 0, 6, -6]) if unstable else 0
        if jit:
            sp = Image.new("RGBA", (W, H), (0, 0, 0, 0)); s2 = ImageDraw.Draw(sp)
            s2.text((x0 - 10 + jit, LOGO_Y), NICK, font=f_logo, fill=(255, 40, 60, 160))
            s2.text((x0 + 10 + jit, LOGO_Y), NICK, font=f_logo, fill=(0, 220, 255, 160))
            im.alpha_composite(sp)
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).text((x0 + jit, LOGO_Y), NICK, font=f_logo, fill=YEL + (140,))
        im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(28)))
        ImageDraw.Draw(im).text((x0 + jit, LOGO_Y), NICK, font=f_logo, fill=WHITE)
    if unstable:
        fx = Image.new("RGBA", (W, H), (0, 0, 0, 0)); fd = ImageDraw.Draw(fx)
        if on and orng.random() < 0.35: fd.rectangle([0, 0, W, H], fill=(255, 255, 255, orng.randint(25, 90)))
        for _ in range(orng.randint(0, 4)):
            y = orng.randint(0, H - 40); fd.rectangle([0, y, W, y + orng.randint(4, 30)], fill=(255, 255, 255, orng.randint(15, 60)))
        im.alpha_composite(fx)
    if lt < 0.08: im.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, int(200 * (1 - lt / 0.08)))))
    return im

# ================= 5. МУЗИКА під кожну кількість моментів =================
SR = 48000; nr = np.random.default_rng(1)
def env(n, a=0.005, d=0.3): t_ = np.arange(n) / SR; return np.minimum(t_ / a, 1) * np.exp(-t_ / d)
def put(buf, sig, at):
    s = int(at * SR)
    if s < 0: sig = sig[-s:]; s = 0
    e = min(len(buf), s + len(sig)); buf[s:e] += sig[: e - s]
def kick(big=False):
    n = int(SR * (1.2 if big else 0.4)); t_ = np.arange(n) / SR; f0 = 40 if big else 48
    return np.sin(2 * np.pi * np.cumsum(f0 + (160 - f0) * np.exp(-t_ / 0.03)) / SR) * env(n, 0.002, 0.45 if big else 0.12)
def nhit(dur, d, hp=None, lp=None):
    x = nr.standard_normal(int(SR * dur))
    if hp: x = sosfilt(butter(2, hp, "hp", fs=SR, output="sos"), x)
    if lp: x = sosfilt(butter(2, lp, "lp", fs=SR, output="sos"), x)
    return x * env(len(x), 0.001, d)
def sweep(dur, f0, f1, rising=True):
    n = int(SR * dur); x = nr.standard_normal(n); out = np.zeros(n)
    for k in range(0, n, 1024):
        fr = f0 * (f1 / f0) ** (k / n)
        out[k:k + 1024] = sosfilt(butter(2, [fr * 0.7, min(fr * 1.4, SR / 2 - 100)], "bp", fs=SR, output="sos"), x[k:k + 1024])
    return out * (np.linspace(0, 1, n) ** 2 if rising else np.sin(np.linspace(0, np.pi, n)))

def music(n):
    tl_, o = timeline(n); total = o + OUTRO; p0 = tl_[-1][1]
    buf = np.zeros(int(SR * total)); t = np.arange(len(buf)) / SR
    drone = (np.sin(2 * np.pi * 55 * t) + 0.5 * np.sin(2 * np.pi * 55.4 * t) + 0.25 * np.sin(2 * np.pi * 82.4 * t)) \
            * (np.clip(t / p0, 0, 1) * 0.12 + 0.05)
    drone[int((p0 - 0.08) * SR):int(p0 * SR)] = 0; drone[int((o - 0.05) * SR):] = 0; buf += drone
    put(buf, kick(True) * 1.1, 0.0)
    for k in np.arange(0.0, HOOK, 0.25): put(buf, nhit(0.03, 0.006, hp=3000) * 0.25, k)          # тікання
    for k in np.arange(HOOK, p0 - 0.1, 0.5): put(buf, kick() * 0.9, k)                         # біт
    for k in np.arange(HOOK + 0.25, p0 - 0.1, 0.5): put(buf, nhit(0.08, 0.02, hp=6000) * 0.22, k)
    rl = min(1.9, p0 - HOOK - 0.1); put(buf, sweep(rl, 300, 8000) * 0.35, p0 - 0.08 - rl)     # райзер
    put(buf, kick(True) * 1.2, p0); put(buf, nhit(1.2, 0.35, lp=5000) * 0.5, p0)               # дроп
    for k in np.arange(p0 + 0.5, o - 0.1, 0.25): put(buf, kick() * 0.8, k)
    for k in np.arange(p0 + 0.5, o - 0.1, 0.125): put(buf, nhit(0.05, 0.012, hp=7000) * 0.15, k)
    put(buf, sweep(0.9, 500, 9000) * 0.3, o - 0.95)
    for (_, s, _) in tl_[1:]: put(buf, sweep(0.32, 600, 7000, False) * 0.5, s - 0.3)             # whoosh
    put(buf, sweep(0.32, 600, 7000, False) * 0.5, o - 0.3)
    put(buf, kick(True) * 1.3, o); put(buf, nhit(1.5, 0.4, lp=3000) * 0.4, o)                   # заставка
    spf = SR // FPS; ht = np.arange(spf) / SR
    for j, on in enumerate(FLICK):
        if not on: continue
        lt = j / FPS; uns = lt < 0.7 or 2.0 <= lt < 2.45; tt = ht + lt
        put(buf, (np.sign(np.sin(2 * np.pi * 100 * tt)) * 0.5 + np.sin(2 * np.pi * 50 * tt)) * (0.05 if uns else 0.025), o + lt)
        if uns and j % 2 == 0: put(buf, nhit(0.02, 0.004, hp=2500) * 0.35, o + lt)
    put(buf, nhit(0.25, 0.06, hp=800) * 0.4, o + 2.45)
    return buf

def save_wav(path, x, peak=0.89):
    x = x / (np.max(np.abs(x)) + 1e-9) * peak; d = (np.stack([x, x], 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w: w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(d.tobytes())

if __name__ == "__main__":
    for n in range(MIN_M, MAX_M + 1): save_wav(f"music_{n}.wav", music(n)); print("music", n)
    tpl = {
        "name": "mr_antibiotik_opener_universal", "version": 2,
        "canvas": {"width": W, "height": H, "fps": FPS, "aspect": "16:10"},
        "moments": {"min": MIN_M, "max": MAX_M, "hook": HOOK, "moment": REG, "peak": PEAK,
                    "peak_freeze": 0.5, "zoom_hook_peak": 0.15, "zoom_moment": 0.10},
        "outro": {"file": "outro_16x10.mp4", "duration": OUTRO},
        "layers": {"hud": "hud_16x10.webm", "hit": "hit_16x10.webm", "hit_pre_frames": HIT_PRE,
                   "peak": "peak_16x10.webm", "music": "music_{n}.wav"},
        "audio": {"music_volume": 0.9, "clip_volume": 1.0, "duck": True, "loudness_lufs": -14},
        "caption": {"enabled": False, "x": 120, "y": 1290, "w": 1150, "h": 130, "size": 66, "color": "white",
                    "max_chars": 22, "appear_after": 0.2, "hide_before_end": 0.2, "font": F_BLACK},
    }
    json.dump(tpl, open("template.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    render("hit_16x10.webm", HIT_FRAMES, hit); print("hit")
    render("peak_16x10.webm", int(PEAK * FPS), peak); print("peak")
    render("outro_16x10.mp4", int(OUTRO * FPS), outro, opaque=True); print("outro")
    render("hud_16x10.webm", int(HUD_LEN * FPS), hud); print("hud")
