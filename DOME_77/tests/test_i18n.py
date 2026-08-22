from app.core.i18n import tr


def test_menu_localized():
    assert tr("ru", "menu_title") == "Главное меню"
    assert tr("en", "menu_title") == "Main menu"
    assert tr("es", "change_languages").startswith("🌍")


def test_unknown_language_falls_back_to_english():
    assert tr("xx", "menu_title") == "Main menu"
