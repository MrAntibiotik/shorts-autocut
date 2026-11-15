"""Картка статусу бота — одне постійне повідомлення внизу чату, що оновлюється на місці.

Показує: мій канал (на перевірці / схвалено / видалено), що зараз обробляється на ПК, EU-канал, версію.
"""

from __future__ import annotations

import time
from pathlib import Path


def short_counts(settings: dict) -> dict:
    """{pending: [(ключ, job_dir, шортс)] новіші першими, approved: N, deleted: N}."""
    from autocut.bot.telegram_bot import Marks, job_shorts, list_jobs, short_key
    marks = Marks(settings)
    pending = []
    for j in reversed(list_jobs(settings)):
        for s in job_shorts(j):
            key = short_key(j, s["i"])
            if s["deleted"] or marks.published(key):
                continue
            pending.append((key, j, s))
    approved = sum(1 for p in marks.data.get("published", {}).values() if p and Path(p).exists())
    deleted = sum(1 for e in marks.deleted().values() if Path(e.get("mp4") or e.get("moved") or "").exists())
    return {"pending": pending, "approved": approved, "deleted": deleted}


def processing_line(settings: dict) -> str:
    from autocut.core.resolve_export import read_status
    from autocut.core.util import fmt_time
    st = read_status(settings) or {}
    s = st.get("status")
    age = time.time() - float(st.get("heartbeat") or 0)
    if s == "running" and age < 120:
        return f"⏳ Обробка: {st.get('stage', '')} — {st.get('percent', 0)} %"
    if s == "running":
        return f"⚠️ Обробка, схоже, зупинилась ({fmt_time(age, 0)} без вістей)"
    if s == "error":
        return f"❌ Остання обробка з помилкою: {str(st.get('message', ''))[:120]}"
    if s == "done":
        return "✅ Обробка: остання завершена"
    return "💤 Обробка: нічого не йде"


def eu_lines(bot) -> list[str]:
    rb = getattr(bot, "replays", None)
    if rb is None:
        return []
    out = ["", "🇬🇧 EU-канал (реплеї)"]
    try:
        st = rb.state()
        from autocut.eu.replay_state import busy
        running = (rb.thread is not None and rb.thread.is_alive()) or busy(Path(st["replays_dir"]))
        out.append("  ⏺ Іде запис / нарізка на ПК" if running else f"  📥 Зібрано до запису: {rb.pending(st)}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from autocut.eu.poster_link import channel_ready
        d = channel_ready("en")
        if d is not None and d.is_dir():
            n = sum(1 for p in d.rglob("*.mp4"))
            out.append(f"  📤 Чекають у Poster EU: {n}")
    except Exception:  # noqa: BLE001
        pass
    return out


def card_text(bot, counts: dict | None = None) -> str:
    from autocut.cli import update
    c = counts or short_counts(bot.settings)
    n = len(c["pending"])
    shown = len(bot.chat.data["shorts"])
    lines = [f"📊 Різак · версія {update.code_version()} · {time.strftime('%H:%M')}",
             "",
             "🎬 Мій канал",
             f"  ⬜ На перевірці: {n}" + (f" (у чаті: {shown})" if n and shown < n else ""),
             f"  ✅ Схвалено (до публікації): {c['approved']}",
             f"  🗑 Видалено (можна відновити): {c['deleted']}",
             "",
             processing_line(bot.settings)]
    lines += eu_lines(bot)
    if n:
        lines += ["", "Непідтверджені шортси — вище: «✅ Підходить» або «🗑 Видалити»."]
    return "\n".join(lines)


def card_markup(bot, counts: dict) -> dict:
    n, shown = len(counts["pending"]), len(bot.chat.data["shorts"])
    rows = []
    if n > shown:
        rows.append([{"text": f"📋 Показати ще непідтверджені ({n - shown})", "callback_data": "C|more"}])
    return {"inline_keyboard": rows}
