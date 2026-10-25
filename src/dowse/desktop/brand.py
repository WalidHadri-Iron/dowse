"""The shared identity of Dowse's tray, native window and Explorer integration."""

from pathlib import Path

APP_ID = "Dowse.App"
ICON = Path(__file__).with_name("dowse.ico")
APP_PNG = Path(__file__).with_name("dowse.png")   # Linux's: windows, notifications, the tray, .desktop files
MENU_BAR_ICON = Path(__file__).with_name("menubar.png")   # a Mac's: the glyph alone, a template (macOS colours it)
