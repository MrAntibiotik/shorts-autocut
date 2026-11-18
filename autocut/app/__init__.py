"""Вікна програми на ПК: головне вікно, майстер запису, рамки, обрізка вебки, прогрес, режим розробника."""

try:   # вигляд «термінал»: чорний фон, зелений шрифт (вимкнути — кнопка «◐ Вигляд» або config\ui_theme.txt = classic)
    from autocut.app.theme import install as _install_theme
    _install_theme()
except Exception:  # noqa: BLE001 — вигляд не має ламати програму
    pass
