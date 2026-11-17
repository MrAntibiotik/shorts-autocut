"""Доступ до бота «Різак»: власник і запрошені люди з ролями.

  власник  — усе (оновлення й відкат програми, EU-реплеї, керування доступом /users);
  🛠 адмін  — перевірка шортсів: нові шортси з кнопками «✅ Підходить» / «🗑» / «🎬 Хайлайт», своя картка статусу;
  👁 перегляд — нові шортси й картка статусу, без кнопок (нічого не змінює).
Як дати доступ: людина пише боту /start → власнику приходить запит з кнопками «🛠 Адмін» / «👁 Перегляд» / «❌».
Список і зміна ролей — /users. Зберігається в config\\telegram.json (users).
"""

from __future__ import annotations

ROLE_NAMES = {"owner": "👑 власник", "admin": "🛠 адмін", "viewer": "👁 перегляд", "pending": "⏳ чекає рішення"}
CONTROL = ("owner", "admin")
# лише власник: оновлення/відкат програми, EU-реплеї, доступ
OWNER_CMDS = {"/update", "/replays", "/users"}
OWNER_ACTS = {"U", "B", "F", "A"}
VIEWER_CMDS = {"/start", "/help", "/status", "/clear"}


class Access:
    def __init__(self, cfg: dict, save, owner_getter):
        self.cfg = cfg
        self._save = save
        self._owner = owner_getter

    @property
    def users(self) -> dict:
        u = self.cfg.get("users")
        if not isinstance(u, dict):
            u = self.cfg["users"] = {}
        return u

    def role(self, uid) -> str | None:
        if uid is None:
            return None
        if self._owner() is not None and int(uid) == int(self._owner()):
            return "owner"
        u = self.users.get(str(int(uid)))
        return u.get("role") if u else None

    def can_control(self, uid) -> bool:
        return self.role(uid) in CONTROL

    def set(self, uid, role: str, name: str | None = None) -> None:
        u = self.users.setdefault(str(int(uid)), {})
        u["role"] = role
        if name:
            u["name"] = name
        self._save()

    def remove(self, uid) -> None:
        self.users.pop(str(int(uid)), None)
        self._save()

    def name(self, uid) -> str:
        u = self.users.get(str(int(uid))) or {}
        return u.get("name") or str(uid)

    def recipients(self) -> list[int]:
        """Кому йдуть шортси й картка: власник, адміни, глядачі."""
        out = [int(self._owner())] if self._owner() is not None else []
        out += [int(k) for k, v in self.users.items() if v.get("role") in ("admin", "viewer") and int(k) not in out]
        return out


def display_name(user: dict) -> str:
    """Ім'я з Telegram: «Ім'я Прізвище (@нік)»."""
    n = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
    if user.get("username"):
        n = f"{n} (@{user['username']})".strip()
    return n or str(user.get("id"))
