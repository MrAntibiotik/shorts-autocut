# -*- coding: utf-8 -*-
"""
Підставляє 3–6 моментів в універсальний opener-шаблон і рендерить ролик.
Перший момент = HOOK, останній = ПІК (дроп музики + freeze), решта — звичайні.
Запуск:  python fill_template.py moments.json
Потрібно: Python 3.9+, ffmpeg і ffprobe у PATH.
"""
import json, os, subprocess, sys, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

def die(msg): print("ПОМИЛКА:", msg); sys.exit(1)

def has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(r.stdout.strip())

def pick_encoder():
    t = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
    if t.returncode == 0:
        return (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"],
                ["-c:v", "h264_nvenc", "-preset", "p1", "-rc", "constqp", "-qp", "12"])
    print("NVENC недоступний — використовую libx264 (повільніше).")
    return (["-c:v", "libx264", "-preset", "medium", "-crf", "18"],
            ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12"])

def _graph_opt():
    """ffmpeg 7+: «-/filter_complex файл»; старіші — «-filter_complex_script файл»."""
    try:
        v = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split()[2]
        major = int("".join(ch for ch in v.split(".")[0] if ch.isdigit()) or 0)
        if v.startswith("N-") or major >= 7:      # нічні збірки (BtbN «master») — нові
            return "-/filter_complex"
    except Exception:
        pass
    return "-filter_complex_script"

def run(cmd, what):
    if subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + cmd).returncode != 0: die(what)

def main():
    for st in (sys.stdout, sys.stderr):        # Windows: вивід у cp1251 падає на «→» — пишемо UTF-8
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if len(sys.argv) < 2: die("вкажи файл моментів: python fill_template.py moments.json")
    if shutil.which("ffmpeg") is None: die("ffmpeg не знайдено в PATH")
    mpath = os.path.abspath(sys.argv[1]); mdir = os.path.dirname(mpath)
    os.chdir(HERE)
    tpl = json.load(open("template.json", encoding="utf-8")); job = json.load(open(mpath, encoding="utf-8"))
    # розмір кадру можна задати в moments.json ("canvas": {"width": 2304, "height": 1440}) — під запис гри;
    # HUD, переходи, пік, заставка і підписи масштабуються від базових 2560×1600
    BASE_W = tpl["canvas"]["width"]
    if job.get("canvas"):
        tpl["canvas"].update({k: int(v) for k, v in job["canvas"].items() if k in ("width", "height", "fps")})
    W, H, FPS = (tpl["canvas"][k] for k in ("width", "height", "fps"))
    BASE_H = json.load(open("template.json", encoding="utf-8"))["canvas"]["height"]
    KS = min(W / BASE_W, H / BASE_H)
    CX = int(round((W - BASE_W * KS) / 2))          # зсув підписів, якщо кадр ширший за 16:10
    if abs(W / H - BASE_W / BASE_H) < 0.02:
        SC = f"scale={W}:{H}"
        OUTRO_VF = SC
    else:   # кадр іншої пропорції (напр. 21:9) — шари шаблону вписуються по висоті, по центру, без розтягування
        SC = f"scale=-2:{H},format=yuva420p,pad={W}:{H}:(ow-iw)/2:0:color=black@0"
        OUTRO_VF = (f"split[ob][of];[ob]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    f"boxblur=40:2,eq=brightness=-0.15[obb];[of]scale=-2:{H}[off];[obb][off]overlay=(W-w)/2:0")
    M, Lr, A = tpl["moments"], tpl["layers"], tpl["audio"]
    moms = job["moments"]; n = len(moms)
    if not M["min"] <= n <= M["max"]: die(f"моментів має бути від {M['min']} до {M['max']}, а передано {n}")

    # таймлайн
    tl, t = [], 0.0
    for k in range(n):
        kind = "hook" if k == 0 else ("peak" if k == n - 1 else "moment")
        d = M["hook"] if kind == "hook" else (M["peak"] if kind == "peak" else M["moment"])
        tl.append((kind, t, t + d)); t += d
    o = t; total = o + tpl["outro"]["duration"]
    print(f"Моментів: {n} → opener {total:g} с")

    final_enc, fast = pick_encoder()
    tmp = tempfile.mkdtemp(prefix="opener_", dir=HERE); rel_tmp = os.path.relpath(tmp, HERE).replace("\\", "/")
    segs = []
    # 1) кожен момент — окремий проміжний файл
    for k, ((kind, a, b), m) in enumerate(zip(tl, moms)):
        src = m["src"] if os.path.isabs(m["src"]) else os.path.join(mdir, m["src"])
        if not os.path.exists(src): die(f"файл не знайдено: {src}")
        dur = b - a; frz = M["peak_freeze"] if kind == "peak" else 0.0
        z = M["zoom_moment"] if kind == "moment" else M["zoom_hook_peak"]
        v = (f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
             f"scale=w='{W}*(1+{z}*max(0\\,1-t/0.25))':h=-2:eval=frame,crop={W}:{H}")
        if frz: v += f",tpad=stop_mode=clone:stop_duration={frz + 0.2}"
        v += f",trim=duration={dur},setpts=PTS-STARTPTS,format=yuv420p[v]"
        vol = A["clip_volume"] * m.get("volume", 1.0)
        au = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},apad,atrim=duration={dur},asetpts=PTS-STARTPTS[a]"
              if has_audio(src) else f"anullsrc=r=48000:cl=stereo,atrim=duration={dur}[a]")
        seg = os.path.join(tmp, f"seg{k}.mkv"); print(f"Момент {k + 1}/{n} ({kind})…")
        run(["-ss", str(m["start"]), "-t", str(dur - frz + 0.1), "-i", src, "-filter_complex", v + ";" + au,
             "-map", "[v]", "-map", "[a]", "-r", str(FPS)] + fast + ["-c:a", "pcm_s16le", seg], f"момент {k + 1}")
        segs.append(seg)
    seg = os.path.join(tmp, "seg_outro.mkv")
    run(["-i", tpl["outro"]["file"], "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", f"[0:v]{OUTRO_VF},format=yuv420p[ov]",
         "-map", "[ov]", "-map", "1:a",
         "-t", str(tpl["outro"]["duration"]), "-r", str(FPS), "-pix_fmt", "yuv420p"] + fast + ["-c:a", "pcm_s16le", seg], "заставка")
    segs.append(seg)
    lst = os.path.join(tmp, "list.txt")
    open(lst, "w", encoding="utf-8").write("".join(f"file '{p.replace(os.sep, '/')}'\n" for p in segs))

    # 2) фінальний прохід
    pre = Lr["hit_pre_frames"] / FPS
    cuts = [s for (_, s, _) in tl[1:]] + [o]
    vp9 = ["-c:v", "libvpx-vp9"]
    inputs = ["-f", "concat", "-safe", "0", "-i", lst, *vp9, "-i", Lr["hud"], *vp9, "-i", Lr["hit"],
              *vp9, "-i", Lr["peak"], "-i", Lr["music"].format(n=n)]
    g = [f"[1:v]{SC},setpts=PTS-STARTPTS[hud]",
         f"[2:v]{SC},split={len(cuts) + 1}" + "".join(f"[h{i}]" for i in range(len(cuts) + 1)),
         f"[0:v][hud]overlay=format=auto:eof_action=pass:enable='lt(t\\,{o})'[x0]",
         f"[h0]trim=start_frame={Lr['hit_pre_frames']},setpts=PTS-STARTPTS[hs]",
         "[x0][hs]overlay=format=auto:eof_action=pass[x1]"]
    last = "x1"
    for i, c in enumerate(cuts):
        g.append(f"[h{i + 1}]setpts=PTS-STARTPTS+{c - pre}/TB[hh{i}]")
        g.append(f"[{last}][hh{i}]overlay=format=auto:eof_action=pass[y{i}]"); last = f"y{i}"
    g.append(f"[3:v]{SC},setpts=PTS-STARTPTS+{tl[-1][1]}/TB[pk]")
    g.append(f"[{last}][pk]overlay=format=auto:eof_action=pass[z]"); last = "z"

    # підписи (вимкнені за замовчуванням; "captions": true у moments.json)
    c = dict(tpl["caption"]); dt = []
    for key in ("x", "y", "w", "h", "size"):
        c[key] = int(round(c[key] * KS))
    c["x"] += CX
    if job.get("captions", c["enabled"]):
        for k, ((kind, a, b), m) in enumerate(zip(tl, moms)):
            txt = m.get("caption", "").strip()[: c["max_chars"]]
            if not txt: continue
            fn = f"{rel_tmp}/cap{k}.txt"; open(os.path.join(HERE, fn), "w", encoding="utf-8").write(txt)
            a2, b2 = a + c["appear_after"], b - c["hide_before_end"]
            sl = f"pow(1-min(1\\,(t-{a2})/0.3)\\,3)*600"; en = f"enable='between(t\\,{a2}\\,{b2})'"
            dt += [f"drawbox=x={c['x']}-{sl}:y={c['y']}:w={c['w']}:h={c['h']}:color=0x0A0C0F@0.78:t=fill:{en}",
                   f"drawbox=x={c['x']}-{sl}:y={c['y']}:w=14:h={c['h']}:color=0xF5C400:t=fill:{en}",
                   f"drawtext=fontfile={c['font']}:textfile={fn}:fontsize={c['size']}:fontcolor={c['color']}:"
                   f"x={c['x']}+50-{sl}:y={c['y']}+({c['h']}-text_h)/2:{en}"]
    g.append(f"[{last}]" + (",".join(dt) + "," if dt else "") + "format=yuv420p[vout]")

    g.append(f"[4:a]volume={A['music_volume']}[mus]")
    g.append("[0:a]asplit=2[cl1][cl2]")
    g.append("[mus][cl2]sidechaincompress=threshold=0.04:ratio=5:attack=15:release=300[md]" if A["duck"]
             else "[cl2]anullsink;[mus]anull[md]")
    g.append(f"[md][cl1]amix=inputs=2:normalize=0:duration=first,loudnorm=I={A['loudness_lufs']}:TP=-1.5:LRA=11,aresample=48000[aout]")

    out = job.get("output", "opener.mp4"); out = out if os.path.isabs(out) else os.path.join(mdir, out)
    gs = os.path.join(tmp, "graph.txt"); open(gs, "w", encoding="utf-8").write(";\n".join(g))
    print("Рендер…")
    run(inputs + [_graph_opt(), gs, "-map", "[vout]", "-map", "[aout]", "-r", str(FPS), "-t", str(total)]
        + final_enc + ["-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", out], "фінальний рендер")
    shutil.rmtree(tmp, ignore_errors=True)
    print("Готово:", out)

if __name__ == "__main__":
    main()
