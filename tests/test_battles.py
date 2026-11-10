"""Автотести логіки боїв WoT на синтетичних показаннях (без відео й OCR).

Запуск: venv\\Scripts\\python tests\\test_battles.py   (або python3 tests/test_battles.py)
Сценарій повторює реальні проблеми: таймер ховається посеред бою, хибне «1184 → 4184»,
бій з нулем шкоди, бій з одним пострілом, невидимий відлік перед боєм, шум розпізнавання.
"""
import sys, random; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
from shorts.config import load_profile
from shorts.vision_detector import analyze_readings, merge_events, split_battles, add_battle_stats, battle_label
from shorts.fusion import fuse
from shorts.selection import select_shorts
p=load_profile('wot')
def run(seed):
    random.seed(seed); R=[]; T=[0.0]
    def gap(d):
        for k in range(int(d*2)): R.append({'t':round(T[0],2),'dmg_raw':'','msg_raw':'','msg_color':'','timer_raw':'','end':''}); T[0]+=0.5
    def battle(hits, death, dur, hide=None, misread=None):
        t0=T[0]
        for k in range(int(dur*2)):
            rel=T[0]-t0
            dmg=sum(d for h,d in hits if rel>=h+0.4); s=f"{dmg:,}".replace(","," ")
            if misread and misread[0]<=rel<misread[0]+3: s=misread[1]
            if random.random()<0.04: s=s[1:] if len(s)>1 else s
            msg=next((f"{d:,} X".replace(","," ") for h,d in hits if 0<=rel-h<3),"")
            timer=""
            if 30<=rel<death+40:
                v=900-int(rel-30); timer=f"{v//60}:{v%60:02d}"
                if random.random()<0.04: timer=timer.replace(":","")
            if hide and hide[0]<=rel<hide[1]: timer=""
            R.append({'t':round(T[0],2),'dmg_raw':s,'msg_raw':msg,'msg_color':'green' if msg else '','timer_raw':timer,
                      'end':'Оцініть бій' if death+1.5<=rel<death+20 else ''}); T[0]+=0.5
    gap(30)
    battle([(60,748),(150,436),(330,716)],400,450,hide=(250,290),misread=(300,'4 184'))   # бій як ваш бій 6
    gap(60); battle([],120,180)                                                           # 0 шкоди
    gap(60); battle([(90,950)],140,200)                                                   # один постріл
    evs=merge_events(analyze_readings(R,p),p,R)
    b=split_battles(fuse(evs,[],[],p),p,0,T[0],R); add_battle_stats(b,R)
    sh=select_shorts(b,p,0,T[0])
    return b, sh


def run_real_fragment(seed):
    """Реконструкція реального відрізка (запис 2026-11-09, 43:20–48:20): повідомлення про засвіт (169)
    і заблоковане (350), постріл без повідомлення, лічильник 4184 читається як 1184."""
    random.seed(seed)
    R = []
    t = 2600.0
    fmt = lambda v: f"{v:,}".replace(",", " ")
    while t < 2900:
        dmg = 0
        for at, tot in [(2730.0, 1236), (2787.0, 2591), (2829.5, 4184)]:
            if t >= at + 0.4:
                dmg = tot
        s = fmt(dmg)
        if 2840 <= t < 2856:
            s = "1 184"
        if random.random() < 0.04:
            s = s[1:] if len(s) > 1 else s
        msg, col = "", ""
        if 2710 <= t < 2713: msg, col = "169 X", "green"
        if 2787 <= t < 2789: msg, col = "350 X", "white"
        if 2789 <= t < 2792: msg, col = "1 355 X", "green"
        if 2730 <= t < 2733: msg, col = "1 236 X", "green"
        v = int(836 - (t - 2600))
        R.append({"t": round(t, 2), "dmg_raw": s, "msg_raw": msg, "msg_color": col,
                  "timer_raw": f"{v // 60}:{v % 60:02d}", "end": ""})
        t += 0.5
    evs = merge_events(analyze_readings(R, p), p, R)
    b = split_battles(fuse(evs, [], [], p), p, 2600, 2900, R)
    add_battle_stats(b, R)
    return [int(e.value) for e in evs if e.type == "hit"], [battle_label(x)[0] for x in b]


def main():
    ok = True
    for seed in range(1, 6):
        hits, names = run_real_fragment(seed)
        good = hits == [1236, 1355, 1593] and names == ["battle_01_dmg_4184"]
        ok &= good
        print(f"реальний відрізок, seed {seed}: {'OK ' if good else 'ПОМИЛКА'} влучання {hits}, {names}")
    for seed in range(1, 11):
        b, sh = run(seed)
        names = [battle_label(x)[0] for x in b]
        expect = ["battle_01_dmg_1900", "battle_02", "battle_03_dmg_950"]
        good = names == expect and len(sh) == 2
        ok &= good
        print(f"seed {seed}: {'OK ' if good else 'ПОМИЛКА'} {names}, шортсів {len(sh)}")
    print("УСІ ТЕСТИ ПРОЙДЕНО" if ok else "Є ПОМИЛКИ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
