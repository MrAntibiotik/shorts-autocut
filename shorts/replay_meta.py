"""Дані бою з файлу реплею World of Tanks (.wotreplay) — без запуску гри.

Формат: 4 байти «магії», 4 байти — кількість JSON-блоків, далі для кожного блоку: 4 байти довжини + JSON,
потім стиснуті пакети бою (їх не читаємо).
  блок 1 — інформація на початку бою: гравець, танк, карта, версія клієнта, сервер, дата;
  блок 2 — результати (є лише якщо бій дограно до кінця): шкода, фраги, засвіт, тривалість, переможець.

Використання: версія реплею (чи відкриється він на вашому клієнті), назва й опис шортса
(«Leopard 1 — 7,412 damage on Prokhorovka»), відбір найцікавіших боїв, тривалість запису.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAGIC = 0x11343212
MAX_BLOCK = 64 * 1024 * 1024


class ReplayError(Exception):
    pass


@dataclass
class ReplayInfo:
    path: str
    player: str = ""
    vehicle: str = ""            # «Leopard 1» (людська назва) або технічна, якщо людської немає
    vehicle_tag: str = ""        # «germany:G89_Leopard1»
    nation: str = ""
    map_name: str = ""           # «Prokhorovka»
    map_tag: str = ""            # «05_prohorovka»
    version: str = ""            # «1.26.0.0»
    version_tuple: tuple = ()
    server: str = ""
    region: str = ""
    date: str = ""
    battle_type: int | None = None
    complete: bool = False       # є результати бою (дограно до кінця)
    damage: int = 0
    kills: int = 0
    assist: int = 0              # засвіт + гусениці
    blocked: int = 0
    xp: int = 0
    duration_s: float = 0.0
    won: bool | None = None
    survived: bool | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["version_tuple"] = list(self.version_tuple)
        return d

    @property
    def stats_line(self) -> str:
        """«7,412 damage · 3 kills · 1,250 assist» — для опису (англійською)."""
        parts = [f"{self.damage:,} damage"]
        if self.kills:
            parts.append(f"{self.kills} kill{'s' if self.kills != 1 else ''}")
        if self.assist >= 500:
            parts.append(f"{self.assist:,} assist")
        if self.blocked >= 1000:
            parts.append(f"{self.blocked:,} blocked")
        return " · ".join(parts)


def read_blocks(path: str | Path) -> list:
    """JSON-блоки з початку файлу реплею."""
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise ReplayError("Файл занадто короткий — це не реплей")
        magic, count = struct.unpack("<II", head)
        if magic != MAGIC:
            raise ReplayError("Це не файл реплею World of Tanks (.wotreplay)")
        if not 1 <= count <= 8:
            raise ReplayError(f"Дивна кількість блоків у реплеї: {count}")
        blocks = []
        for i in range(count):
            raw = f.read(4)
            if len(raw) < 4:
                raise ReplayError("Реплей обірваний (не дочитався блок даних)")
            (size,) = struct.unpack("<I", raw)
            if size > MAX_BLOCK:
                raise ReplayError(f"Блок {i + 1} завеликий ({size} байт) — файл пошкоджений")
            data = f.read(size)
            if len(data) < size:
                raise ReplayError("Реплей обірваний (не дочитався блок даних)")
            try:
                blocks.append(json.loads(data.decode("utf-8", errors="replace")))
            except ValueError as e:
                raise ReplayError(f"Блок {i + 1} реплею не є JSON: {e}") from e
        return blocks


def parse_version(text: str) -> tuple[str, tuple]:
    """«1, 26, 0, 1» / «1.26.0.1 #1234» / «World of Tanks v.1.26.0.1 #1234» → ('1.26.0.1', (1, 26, 0, 1))."""
    nums = re.findall(r"\d+", (text or "").split("#")[0])
    if not nums:
        return "", ()
    t = tuple(int(n) for n in nums[:4])
    return ".".join(map(str, t)), t


def same_version(a: tuple, b: tuple, parts: int = 3) -> bool:
    """Реплей відкривається лише на тій самій версії клієнта (порівнюємо перші 3 числа: 1.26.0)."""
    return bool(a) and bool(b) and tuple(a[:parts]) == tuple(b[:parts])


def _human(tag: str) -> str:
    """«G89_Leopard1» → «Leopard1»; «R04_T-34» → «T-34»; «05_prohorovka» → «Prohorovka»."""
    t = tag.split(":")[-1]
    t = re.sub(r"^[A-Za-z]{0,3}\d+[a-z]?_", "", t)
    return t.replace("_", " ").strip()


def _personal(results: dict) -> dict:
    p = results.get("personal") or {}
    if not isinstance(p, dict):
        return {}
    for k, v in p.items():  # ключ — числовий опис танка; «avatar» — загальні дані гравця
        if k != "avatar" and isinstance(v, dict) and "damageDealt" in v:
            return v
    return p.get("avatar") if isinstance(p.get("avatar"), dict) else {}


def parse(path: str | Path) -> ReplayInfo:
    blocks = read_blocks(path)
    first = blocks[0] if blocks and isinstance(blocks[0], dict) else {}
    info = ReplayInfo(path=str(path))
    info.player = str(first.get("playerName") or "")
    tag = str(first.get("playerVehicle") or "")          # «germany-G89_Leopard1»
    if "-" in tag:
        info.nation, short = tag.split("-", 1)
        info.vehicle_tag = f"{info.nation}:{short}"
    else:
        short = tag
        info.vehicle_tag = tag
    for v in (first.get("vehicles") or {}).values():      # людська назва — у списку учасників
        if isinstance(v, dict) and v.get("name") == info.player and v.get("vehicleType"):
            info.extra["vehicle_type"] = v["vehicleType"]
            break
    info.vehicle = _human(short) if short else ""
    info.map_tag = str(first.get("mapName") or "")
    info.map_name = str(first.get("mapDisplayName") or "") or _human(info.map_tag).title()
    info.version, info.version_tuple = parse_version(
        str(first.get("clientVersionFromXml") or first.get("clientVersionFromExe") or ""))
    info.server = str(first.get("serverName") or "")
    info.region = str(first.get("regionCode") or "")
    info.date = str(first.get("dateTime") or "")
    bt = first.get("battleType")
    info.battle_type = int(bt) if isinstance(bt, (int, float)) else None

    if len(blocks) >= 2:
        second = blocks[1]
        results = second[0] if isinstance(second, list) and second else second
        if isinstance(results, dict) and (results.get("personal") or results.get("common")):
            info.complete = True
            me = _personal(results)
            info.damage = int(me.get("damageDealt") or 0)
            info.kills = int(me.get("kills") or 0)
            info.assist = int(me.get("damageAssistedRadio") or 0) + int(me.get("damageAssistedTrack") or 0) \
                + int(me.get("damageAssistedStun") or 0)
            info.blocked = int(me.get("damageBlockedByArmor") or 0)
            info.xp = int(me.get("xp") or 0)
            common = results.get("common") or {}
            info.duration_s = float(common.get("duration") or 0)
            team, winner = me.get("team"), common.get("winnerTeam")
            if team is not None and winner is not None:
                info.won = winner == team if winner else None   # 0 — нічия
            if "deathReason" in me:
                info.survived = int(me.get("deathReason")) == -1
    return info


def nice_vehicle(info: ReplayInfo, names: dict | None = None) -> str:
    """Людська назва танка: з таблиці names (тег → назва), інакше — з тегу («Leopard1» → «Leopard1»)."""
    if names:
        for key in (info.vehicle_tag, info.vehicle_tag.split(":")[-1]):
            if key in names:
                return names[key]
    return info.vehicle


def title_en(info: ReplayInfo, vehicle: str | None = None) -> str:
    """Назва шортса англійською: «Leopard1 — 7,412 damage on Prokhorovka 🔥»."""
    veh = vehicle or info.vehicle or "Tank"
    t = f"{veh} — {info.damage:,} damage" if info.damage else f"{veh} gameplay"
    if info.map_name:
        t += f" on {info.map_name}"
    if info.kills >= 5:
        t += f", {info.kills} kills"
    return (t + " 🔥")[:100]


def is_interesting(info: ReplayInfo, min_damage: int = 0, min_kills: int = 0) -> bool:
    return info.complete and info.damage >= min_damage and info.kills >= min_kills


__all__ = ["ReplayError", "ReplayInfo", "is_interesting", "nice_vehicle", "parse", "parse_version",
           "read_blocks", "same_version", "title_en"]
