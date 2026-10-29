"""Monday's look in Qt (2026-10-19): the tokens and shared widgets of Monday's window (core/qtwindow.py) and notch (core/qtnotch.py).

The owner's screen recording of Coucou set the look ("the same application shown in the recording, adapted to Monday"): a black
rail, a near-black panel, charcoal cards on hairline borders, orange for the one primary action, green switches, monospace key
chips, and every page opening on a banner of stars in its own tint with Monday's pals on its floor. Qt (PySide6, in the pylib
folder beside the app: E:/monday/pylib here) so the same code runs on Windows, macOS and Linux (docs/adr/0002-qt-all-platforms.md).

Sizes are logical pixels (Qt scales them per screen); colours and sizes were measured from the recording at 125 %
(E:/monday/design-ref/PLAN.md). Widgets are painted here (QPainter); the few standard inputs Qt draws itself (fields, dropdowns,
scroll bars, menus, sliders) take one small Qt style sheet built from the same constants (`STYLE`). Every control carries an
accessible name; `named()` finds one that doesn't.

Check: `python -m core.qtkit --check` (and `--shot <png>` writes a sheet of every widget for a look).
"""
from __future__ import annotations

import math
import os
import random
import re
import subprocess
import sys
import time

PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pylib")
if os.path.isdir(PYLIB) and PYLIB not in sys.path:  # E:\monday\pylib here, <install>\pylib for an installed Monday
    sys.path.append(PYLIB)

from PySide6.QtCore import (QByteArray, QEasingCurve, QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer,  # noqa: E402
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QIcon,  # noqa: E402
                           QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF, QRadialGradient, QTransform)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,  # noqa: E402
                               QLineEdit, QPlainTextEdit, QSlider, QVBoxLayout, QWidget)

from .notch import (BAD as BAD_PET, DIZZY_S, DP_K, DP_PRESETS, DP_SHAPES, DP_Y, EAT_S, HATS, POKE_WINDOW_S, POKES,  # noqa: E402
                    REACT_S, WARN as WARN_PET, prefs as pet_prefs, wear_now)

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# -- colours (measured from the recording; text contrast checked in --check) ---------------------------------------------
RAIL = "#000000"          # the sidebar
PANEL = "#0e0e11"         # the main panel behind the pages
PANEL_EDGE = "#1c1c1f"
CARD = "#111516"
CARD_UP = "#15191a"       # a stat card
EDGE = "#1f2427"          # a card's hairline
DIVIDER = "#1b2023"
NAV_ON = "#161616"
FIELD = "#1d2125"         # a dropdown, a multi-line box
INPUT = "#242428"         # a one-line text field (the recording's Integrations)
SEG, SEG_ON = "#242428", "#444444"  # a segmented choice and its lit one
FIELD_EDGE = "#2b3034"
BUTTON = "#24282c"
BUTTON_HOVER = "#2e3338"
CHIP = "#232428"
CHIP_EDGE = "#333438"
CHIP_OFF = "#181c1c"
TEXT = "#f5f5f6"
TEXT_NAV = "#c8cacd"     # a rail row not on show (the recording's grey; the lit one is white)
TEXT_2 = "#a8acb1"        # descriptions
TEXT_3 = "#80848a"        # section labels, hints
TEXT_OFF = "#5d6166"
ICON = "#b9bcc2"
ORANGE = "#f08c40"
ORANGE_HOVER = "#f59d58"
ON_ORANGE = "#1b1208"
GREEN = "#2cdc60"         # a switch that is on
GREEN_TEXT = "#3ecf6e"
RED = "#f0445a"           # a dot that says not set / failed
RED_TEXT = "#ff6b7a"
RED_BG = "#341418"
AMBER = "#f0a040"
AMBER_BG = "#3a2a14"
BLUE = "#5aa9ff"          # a link
OFF = "#3a3a3c"           # a switch that is off
KNOB = "#ffffff"
FOCUS = "#f08c40"

# a page banner's tint, the colour its right side glows in (sampled at its far right in the recording)
TINTS = {"warm": "#382210", "indigo": "#1c1c44", "violet": "#201444", "ember": "#402418", "steel": "#142c44",
         "amber": "#4c340c", "grey": "#262628", "cool": "#282c30", "magenta": "#3c1c30", "gold": "#3c2c0c",
         "lilac": "#2c2440", "green": "#144830", "teal": "#0f3a3a"}
# Monday's pals (the banners' and the notch's little ones): body colours
PALS = {"white": "#f6f7f9", "orange": "#f0954a", "pink": "#e865c0", "green": "#3ddc84", "purple": "#8b6cf0", "red": "#f2556a",
        "blue": "#4aa3f0", "cyan": "#3cd6e0", "yellow": "#f5c542", "grey": "#9aa0a6"}

# -- sizes (logical px) ---------------------------------------------------------------------------------------------
RADIUS_CARD = 14
RADIUS_PANEL = 18
RAIL_W = 228
NAV_H = 32
ROW_H = 46                 # a card's setting row at least (with its hairline and the card's spacing: the recording's ~58 apart)
DROPDOWN_W = (120, 280)   # a dropdown sized to its choices: at least, at most
HERO_H = 170
TOGGLE = (36, 20)
KEY_PX = 9.5              # a button's shortcut in its little box ("Deny [Ctrl+Alt+N]")
GLIDE_MS = 140            # a switch's knob
PAL_FPS = 30              # the banners' pals while they move (none while the window is hidden or motion is off)

# -- type ------------------------------------------------------------------------------------------------------------
_FAMILY = None
_MONO = None
MONO_PREFER = ("Consolas", "Cascadia Mono", "SF Mono", "Menlo", "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono")


def _families():
    global _FAMILY, _MONO
    if _FAMILY is None:  # the platform's own UI face: Segoe UI on Windows (the recording's), SF on macOS, the desktop's on Linux
        _FAMILY = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        have = set(QFontDatabase.families())
        _MONO = next((f for f in MONO_PREFER if f in have), QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())
    return _FAMILY, _MONO


def font(px: float, weight: int = 400, mono: bool = False, spacing: float = 0.0) -> QFont:
    """A font at px logical pixels (fractions kept: 13.5 is the recording's nav), weight 400 / 600 / 700."""
    family, fixed = _families()
    f = QFont(fixed if mono else family)
    screen = QGuiApplication.primaryScreen()
    dpi = screen.logicalDotsPerInch() if screen else 96.0
    f.setPointSizeF(px * 72.0 / dpi)
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def mix(a, b, t: float) -> QColor:
    a, b = QColor(a), QColor(b)
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


def alpha(c, a: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(a)
    return c


def contrast(fg, bg) -> float:
    """WCAG contrast ratio of two opaque colours."""
    def lum(c):
        c = QColor(c)
        ch = [v / 255 for v in (c.red(), c.green(), c.blue())]
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# -- motion ------------------------------------------------------------------------------------------------------------
_REDUCED = None
PAL_MOTION = True         # the owner's pets choice ("Still" turns it off); the window sets it from Core's prefs


def reduced_motion() -> bool:
    """The OS's animations-off / reduce-motion setting (read once), or MONDAY_REDUCED_MOTION=1: then things jump, never glide."""
    global _REDUCED
    if os.environ.get("MONDAY_REDUCED_MOTION") == "1":
        return True
    if _REDUCED is None:
        _REDUCED = False
        try:
            if sys.platform == "win32":
                import ctypes
                on = ctypes.c_int(1)
                ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
                _REDUCED = not on.value
            elif sys.platform == "darwin":
                out = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                                     capture_output=True, text=True, timeout=2).stdout.strip()
                _REDUCED = out == "1"
            else:  # GNOME and its kin; another desktop without gsettings keeps motion on
                out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                                     capture_output=True, text=True, timeout=2).stdout.strip()
                _REDUCED = out == "false"
        except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
            pass
    return _REDUCED


# -- icons (Lucide's outlines, ISC licence: THIRD_PARTY_NOTICES.md) --------------------------------------------------------
ICONS = {
    "home": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 '
            '5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "gear": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 '
            '2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 '
            '2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 '
            '2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-'
            '.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "volume": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 '
              '1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/>'
              '<path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>',
    "mute": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 '
            '1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/>',
    "mic": '<path d="M12 19v3"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><rect x="9" y="2" width="6" height="13" rx="3"/>',
    "stats": '<path d="M5 21v-6"/><path d="M10 21V9"/><path d="M15 21v-9"/><path d="M20 21V5"/>',
    "book": '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20"/>',
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 '
           '10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "sliders": '<path d="M21 4h-7"/><path d="M10 4H3"/><path d="M21 12h-9"/><path d="M8 12H3"/><path d="M21 20h-5"/>'
               '<path d="M12 20H3"/><path d="M14 2v4"/><path d="M8 10v4"/><path d="M16 18v4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/>'
           '<path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/>'
           '<path d="m19.07 4.93-1.41 1.41"/>',
    "grid": '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/>'
            '<rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
    "terminal": '<path d="m7 11 2-2-2-2"/><path d="M11 13h4"/><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>',
    "wave": '<path d="M2 10v3"/><path d="M6 6v11"/><path d="M10 3v18"/><path d="M14 8v7"/><path d="M18 5v13"/><path d="M22 10v3"/>',
    "puzzle": '<path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 '
              '3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 '
              '0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 '
              '8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z"/>',
    "keyboard": '<path d="M10 8h.01"/><path d="M12 12h.01"/><path d="M14 8h.01"/><path d="M16 12h.01"/><path d="M18 8h.01"/>'
                '<path d="M6 8h.01"/><path d="M7 16h10"/><path d="M8 12h.01"/><rect width="20" height="16" x="2" y="4" rx="2"/>',
    "bell": '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 '
            '12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
    "dot": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/>',
    "tasks": '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
    "activity": '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 '
                '8.36A2 2 0 0 1 4.49 12H2"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 '
              '1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "bot": '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/>'
           '<path d="M15 13v2"/><path d="M9 13v2"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>'
              '<circle cx="12" cy="13" r="3"/>',
    "flask": '<path d="M14 2v6a2 2 0 0 0 .245.96l5.51 10.08A2 2 0 0 1 18 22H6a2 2 0 0 1-1.755-2.96l5.51-10.08A2 2 0 0 0 10 8V2"/>'
             '<path d="M6.453 15h11.094"/><path d="M8.5 2h7"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 '
              '2 0 0 0 2 2Z"/>',
    "up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "stop": '<rect width="14" height="14" x="5" y="5" rx="2"/>',
    "pause": '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "hand": '<path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2"/><path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2"/><path d="M10 '
            '10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 '
            '2 0 0 1 2.83-2.82L7 15"/>',
    "open": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 '
               '9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "brain": '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 '
             '1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 '
             '1-3 4"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 '
            '0 7.07 7.07l1.71-1.71"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 '
              '1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "chevron_left": '<path d="m15 18-6-6 6-6"/>',  # (Lucide's chevron-left: the GitHub card's "‹ Activity", "‹ My PRs")
    "right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "pin": '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 '
           '2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
    # the island's (2026-10-24): its edit badges, Collapse and Power, the music's transport, the widgets' own
    "minus": '<path d="M5 12h14"/>',
    "resize": '<path d="M15 3h6v6"/><path d="m21 3-7 7"/><path d="m3 21 7-7"/><path d="M9 21H3v-6"/>',
    "collapse": '<circle cx="12" cy="12" r="10"/><path d="m8 14 4-4 4 4"/>',
    "power": '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
    "back": '<polygon points="19 20 9 12 19 4 19 20"/><path d="M5 19V5"/>',
    "forward": '<polygon points="5 4 15 12 5 20 5 4"/><path d="M19 5v14"/>',
    "timer": '<path d="M10 2h4"/><path d="m12 14 3-3"/><circle cx="12" cy="14" r="8"/>',
    "clipboard": '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 '
                 '0 0 1 2-2h2"/>',
    "note": '<path d="M15.5 3H5a2 2 0 0 0-2 2v14c0 1.1.9 2 2 2h14a2 2 0 0 0 2-2V8.5L15.5 3Z"/><path d="M15 3v6h6"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 '
             '0 0 0-1.79 1.11z"/>',
    "undo": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
}
_ICON_CACHE: dict = {}


def icon_renderer(name: str, color: str = ICON, width: float = 2.0) -> QSvgRenderer:
    key = (name, QColor(color).name(QColor.NameFormat.HexArgb), width)
    if key not in _ICON_CACHE:
        c = QColor(color)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c.name()}" '
               f'stroke-opacity="{c.alphaF():.3f}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">'
               f'{ICONS[name]}</svg>')
        _ICON_CACHE[key] = QSvgRenderer(QByteArray(svg.encode()))
    return _ICON_CACHE[key]


def paint_icon(p: QPainter, name: str, rect: QRectF, color: str = ICON, width: float = 2.0):
    icon_renderer(name, color, width).render(p, QRectF(rect))


def icon(name: str, color: str = ICON, size: int = 16) -> QIcon:
    """A QIcon (for menus and the tray) from the same outlines."""
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    pix = QPixmap(int(size * ratio), int(size * ratio))
    pix.fill(Qt.GlobalColor.transparent)
    pix.setDevicePixelRatio(ratio)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_icon(p, name, QRectF(0, 0, size, size), color)
    p.end()
    return QIcon(pix)


# -- Monday's pet: notch.py's Pet (its 100 x 100 box, curves, faces and motion), drawn with QPainter instead of GDI+ -------------
def _tone(c, k: float) -> QColor:
    """A colour lighter (k > 0, towards white) or darker (k < 0): notch.Pet._tone."""
    c = QColor(c)
    return QColor(*(int(v + (255 - v) * k) if k > 0 else int(v * (1 + k)) for v in (c.red(), c.green(), c.blue())))


def _arc(p: QPainter, x, y, w, h, start, sweep):
    """GDI+'s DrawArc (degrees, clockwise from 3 o'clock) on a QPainter (sixteenths, anticlockwise)."""
    p.drawArc(QRectF(x, y, w, h), int(-start * 16), int(-sweep * 16))


def _star_points(x, y, r):
    return QPolygonF([QPointF(x + math.cos(i * math.pi / 4) * (r if i % 2 == 0 else r * 0.35),
                              y + math.sin(i * math.pi / 4) * (r if i % 2 == 0 else r * 0.35)) for i in range(8)])


PUMPKIN = "#F28C28"        # the wardrobe's pumpkin: the pet's body in its orange


class PetArt:
    """One pet's look and motion, as notch.py's Pet draws it (the owner's witch by default): draw(p, rect, now, mood) paints a frame
    into a square; poke() and eat() start motions; look_at() turns its eyes towards the pointer. The kinds, colours and the owner's
    choices are notch.py's (PETS, prefs), so both notches and the window show the same pet."""

    def __init__(self, p=None):
        self.set(p or {})
        self.reaction, self.react_at, self.pokes, self.dizzy_at, self.eat_at = None, -99.0, [], -99.0, -99.0
        self.blink_at, self.motion, self.look = time.monotonic() + 2.0, not reduced_motion(), (0.0, 0.0)

    def set(self, p):
        self.p = pet_prefs(p)
        self.body, self.accent, self.ink = QColor(self.p["body"]), QColor(self.p["accent"]), QColor("#16181B")

    def look_at(self, dx, dy):
        """Its eyes towards the pointer (dx, dy: the pointer's px from its middle): a little way, all the way once it is ~160 px off."""
        d = math.hypot(dx, dy)
        k = min(1.0, d / 160.0) / (d or 1.0)
        self.look = (dx * k * 2.6, dy * k * 1.8) if self.motion else (0.0, 0.0)

    def poke(self, now=None):
        """A click on the pet: its reaction; POKES of them within POKE_WINDOW_S: dizzy. -> True when it got dizzy now."""
        now = now if now is not None else time.monotonic()
        self.pokes = [t for t in self.pokes if now - t < POKE_WINDOW_S] + [now]
        self.reaction, self.react_at = self.p["interact"], now
        if len(self.pokes) >= POKES:
            self.pokes, self.dizzy_at = [], now
            return True
        return False

    def eat(self, now=None):
        self.eat_at = now if now is not None else time.monotonic()

    def dizzy(self, now):
        return now - self.dizzy_at < DIZZY_S

    def draw(self, p: QPainter, rect: QRectF, now: float, mood: str, scale=None):
        """scale: the pet in its square (the owner's Size unless given)."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        amp = self.p["intensity"] if self.motion else 0.0
        if self.dizzy(now):
            mood = "dizzy"
        elif now - self.eat_at < EAT_S:
            mood = "eating"
        dy, rot, scale, wave = 0.0, 0.0, self.p["size"] if scale is None else scale, 0.0
        if self.p["idle"] == "bob":
            dy += math.sin(now * 2.1) * 2.2 * amp
        elif self.p["idle"] == "breathe":
            scale *= 1 + math.sin(now * 1.7) * 0.03 * amp
        if mood == "working":
            dy += math.sin(now * 6.0) * 1.2 * amp
        elif mood == "hearing":
            dy -= abs(math.sin(now * 5.0)) * 2.0 * amp
        r = (now - self.react_at) / REACT_S
        if 0 <= r < 1 and amp:
            if self.reaction == "bounce":
                dy -= math.sin(r * math.pi) * 10 * amp
            elif self.reaction == "spin":
                rot += 360 * (1 - (1 - r) ** 3)
            elif self.reaction == "wave":
                wave = math.sin(r * math.pi * 3)
        if mood == "dizzy" and amp:
            rot += math.sin(now * 9) * 9
        p.translate(rect.topLeft())
        k = rect.width() / 100.0  # everything is drawn on a 100 x 100 box
        p.scale(k, k)
        p.save()
        p.translate(50.0, 56.0 + dy)
        p.rotate(rot)
        p.scale(scale, scale)
        p.setPen(Qt.PenStyle.NoPen)
        body = self.body if mood != "offline" else QColor(128, 133, 138)
        pumpkin = self.wearing() == "pumpkin"
        if pumpkin and mood != "offline":  # the wardrobe's pumpkin: the pet is one, ridges and all (the 2026-10-26 recording)
            body = QColor(PUMPKIN)
        (self._pal if self._parts() else getattr(self, "_" + self.p["kind"], self._blob))(p, body)
        if pumpkin:
            self._ridges(p)
        self._face(p, now, mood)
        self._wear(p)
        if wave:
            self._paw(p, body, wave)
        p.restore()
        self._extras(p, now, mood, dy)
        p.restore()

    # bodies, centred on (0, 0), about 70 px across
    @staticmethod
    def _round(x, y, w, h, r) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), r, r)
        return path

    def _blob(self, p, body):
        p.fillPath(self._round(-34.0, -28.0, 68.0, 56.0, 24.0), body)
        p.fillPath(self._round(-26.0, -24.0, 30.0, 12.0, 6.0), QColor(255, 255, 255, 40))  # a soft highlight

    def _witch(self, p, body):
        """The owner's reference mascot (2026-10-17), in Monday's own drawing: a soft white marshmallow under a big purple witch's hat
        seen a little from above (a wide brim with its darker underside showing, a broad cone whose tip curls over, an orange band with
        a gold buckle low on the brim). The hat is the accent. Its face sits right of centre: it looks along the notch."""
        hat = self.accent
        path = self._round(-32.5, -22.0, 65.0, 48.0, 19.5)
        shade = QLinearGradient(QPointF(-32.0, -10.0), QPointF(20.0, 30.0))
        shade.setColorAt(0.0, _tone(body, 0.5))
        shade.setColorAt(1.0, _tone(body, -0.06))
        p.fillPath(path, shade)
        under = QLinearGradient(QPointF(0.0, -4.0), QPointF(0.0, 7.0))
        under.setColorAt(0.0, QColor(40, 30, 70, 46))
        under.setColorAt(1.0, QColor(40, 30, 70, 0))
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(-32.5, -3.9, 65.0, 10.8), under)  # the brim's shadow on it
        p.restore()
        p.setBrush(_tone(hat, -0.45))
        p.drawEllipse(QRectF(-48.0, -27.0, 96.0, 25.0))  # the brim's underside, showing in front
        lit = QRadialGradient(QPointF(-2.0, -13.0), 48.0)  # lit in front, darker towards its rim (GDI+'s path gradient)
        lit.setColorAt(0.0, _tone(hat, 0.2))
        lit.setColorAt(1.0, _tone(hat, -0.1))
        brush = QBrush(lit)
        brush.setTransform(QTransform().translate(-2.0, -13.0).scale(1.0, 0.5).translate(2.0, 13.0))
        p.setBrush(brush)
        p.drawEllipse(QRectF(-48.0, -30.0, 96.0, 24.0))
        p.save()  # the cone leans a little to the right, as the reference's does
        p.translate(0.0, -16.0)
        p.rotate(5.0)
        p.translate(0.0, 16.0)
        cone = QPainterPath(QPointF(-17.0, -18.0))  # up its concave left side, over the curl, back under it, down its right side, along the base
        cone.cubicTo(QPointF(-12.0, -26.0), QPointF(-6.0, -40.0), QPointF(2.0, -48.0))
        cone.cubicTo(QPointF(8.0, -53.5), QPointF(17.0, -53.5), QPointF(21.5, -47.5))
        cone.cubicTo(QPointF(15.5, -49.0), QPointF(11.0, -46.0), QPointF(9.0, -41.0))
        cone.cubicTo(QPointF(11.0, -32.0), QPointF(14.0, -24.0), QPointF(18.0, -18.0))
        cone.cubicTo(QPointF(8.0, -12.0), QPointF(-7.0, -12.0), QPointF(-17.0, -18.0))
        cone.closeSubpath()
        side = QLinearGradient(QPointF(-17.0, 0.0), QPointF(19.0, 0.0))
        side.setColorAt(0.0, _tone(hat, 0.14))
        side.setColorAt(1.0, _tone(hat, -0.3))
        p.fillPath(cone, side)
        band = QPainterPath(QPointF(-15.4, -23.5))  # the band, along the cone's base
        band.cubicTo(QPointF(-7.0, -17.5), QPointF(8.0, -17.5), QPointF(16.4, -23.5))
        band.lineTo(QPointF(18.0, -18.0))
        band.cubicTo(QPointF(8.0, -12.0), QPointF(-7.0, -12.0), QPointF(-17.0, -18.0))
        band.closeSubpath()
        p.fillPath(band, QColor("#F97114"))
        p.fillRect(QRectF(9.2, -19.6, 3.6, 3.6), QColor("#7A3A12"))
        p.setPen(QPen(QColor("#FFC954"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(8.5, -20.3, 5.0, 5.0))  # the buckle
        p.setPen(Qt.PenStyle.NoPen)
        p.restore()
        p.translate(16.5, 13.5)

    def _bot(self, p, body):
        p.setPen(QPen(body, 3.0))
        p.drawLine(QPointF(0.0, -30.0), QPointF(0.0, -41.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.accent)
        p.drawEllipse(QRectF(-5.0, -48.0, 10.0, 10.0))
        p.fillPath(self._round(-34.0, -30.0, 68.0, 58.0, 12.0), body)
        p.fillPath(self._round(-27.0, -22.0, 54.0, 40.0, 9.0), QColor(30, 38, 46))  # its screen

    def _ears(self, p, body, inner, outer, tip_h):
        for s in (-1, 1):
            p.setBrush(body)
            p.drawPolygon(QPolygonF([QPointF(s * outer, -8.0), QPointF(s * (outer - 2), -tip_h), QPointF(s * 7.0, -24.0)]))
            p.setBrush(inner)
            p.drawPolygon(QPolygonF([QPointF(s * (outer - 6), -15.0), QPointF(s * (outer - 3), -(tip_h - 8)), QPointF(s * 13.0, -24.0)]))

    def _cat(self, p, body):
        self._ears(p, body, self.accent, 31.0, 40.0)
        p.setBrush(body)
        p.drawEllipse(QRectF(-34.0, -28.0, 68.0, 56.0))
        p.setPen(QPen(QColor(40, 40, 40, 150), 1.2))
        for s in (-1, 1):
            for dy in (-1.0, 3.0):
                p.drawLine(QPointF(s * 20.0, 10.0 + dy), QPointF(s * 36.0, 7.0 + dy * 1.8))
        p.setPen(Qt.PenStyle.NoPen)

    def _fox(self, p, body):
        self._ears(p, body, QColor(40, 30, 28), 33.0, 46.0)
        p.setBrush(body)
        p.drawEllipse(QRectF(-34.0, -28.0, 68.0, 56.0))
        p.setBrush(self.accent)
        p.drawEllipse(QRectF(-24.0, 2.0, 48.0, 26.0))  # the muzzle

    def _gem(self, p, body):
        p.setBrush(body)
        p.drawPolygon(QPolygonF([QPointF(0.0, -36.0), QPointF(34.0, -6.0), QPointF(22.0, 28.0), QPointF(-22.0, 28.0), QPointF(-34.0, -6.0)]))
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawPolygon(QPolygonF([QPointF(0.0, -36.0), QPointF(34.0, -6.0), QPointF(0.0, -10.0)]))

    def _parts(self):
        """(shape, eyes, top) of a pet drawn from Dotpals' builder parts, or None for Monday's own pets."""
        k = self.p["kind"]
        return DP_PRESETS.get(k) or ((self.p["shape"], self.p["eyes"], self.p["top"]) if k == "pal" else None)

    @staticmethod
    def _svg(d) -> QPainterPath:
        """An SVG path's absolute M L C Q Z commands (what Dotpals' shapes and tops use)."""
        path, cur, start = QPainterPath(), (0.0, 0.0), (0.0, 0.0)
        toks = re.findall(r"[MLCQZ]|-?\d+(?:\.\d+)?", d)
        i, cmd = 0, None
        while i < len(toks):
            if toks[i] in "MLCQZ":
                cmd, i = toks[i], i + 1
                if cmd == "Z":
                    path.closeSubpath()
                    cur = start
                    continue
            n = {"M": 2, "L": 2, "C": 6, "Q": 4}[cmd]
            v = [float(x) for x in toks[i:i + n]]
            i += n
            if cmd == "M":
                path.moveTo(v[0], v[1])
                cur = start = (v[0], v[1])
                cmd = "L"  # more pairs after an M are lines
            elif cmd == "L":
                path.lineTo(v[0], v[1])
                cur = (v[0], v[1])
            elif cmd == "C":
                path.cubicTo(v[0], v[1], v[2], v[3], v[4], v[5])
                cur = (v[4], v[5])
            else:
                path.quadTo(v[0], v[1], v[2], v[3])
                cur = (v[2], v[3])
        return path

    def _pal(self, p, body):
        """A body from Dotpals' builder (or one of its characters), in Dotpals' own 200x200 coordinates."""
        shape, _, top = self._parts()
        _words, outline, t, ((lx, ly), (rx, ry)), _, _, _ = DP_SHAPES[shape]
        p.save()
        p.scale(DP_K, DP_K)
        p.translate(-100.0, -DP_Y)
        fill = QLinearGradient(QPointF(0.0, float(t)), QPointF(0.0, 262.0))
        fill.setColorAt(0.0, _tone(body, 0.22))
        fill.setColorAt(1.0, _tone(body, -0.12))
        figs = [outline]
        if top == "ears":  # part of the body: its colour, its fur
            figs.append(f"M{lx - 16} {ly + 16} L{lx - 10} {ly - 30} L{lx + 20} {ly - 2} Z M{rx + 16} {ry + 16} L{rx + 10} {ry - 30} L{rx - 20} {ry - 2} Z")
        if self.p["kind"] == "webby":  # its eight legs, behind it, and the silk it hangs from
            leg = QPen(QColor(42, 35, 64), 7.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(leg)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(4):
                for sgn in (-1, 1):
                    x0 = 100 + sgn * 70
                    lp = QPainterPath(QPointF(x0, 150.0 + i * 16))
                    lp.cubicTo(100 + sgn * 112, 120.0 + i * 18, 100 + sgn * 124, 170.0 + i * 20, 100 + sgn * 128, 205.0 + i * 16)
                    p.drawPath(lp)
            p.setPen(QPen(QColor(220, 220, 230, 160), 2.5))
            p.drawLine(QPointF(100.0, float(t)), QPointF(100.0, -80.0))
            p.setPen(Qt.PenStyle.NoPen)
        rim = _tone(body, 0.45)
        rim.setAlpha(110)
        for f in figs:
            path = self._svg(f)
            p.fillPath(path, fill)
            if self.p.get("fur", True) or self.p["kind"] != "pal":  # Dotpals' fur: a soft, lighter rim
                pen = QPen(rim, 7.0)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.strokePath(path, pen)
        if shape == "bean":  # the frog's two eye bumps
            p.setBrush(fill)
            for cx in (64.0, 136.0):
                p.drawEllipse(QRectF(cx - 36.0, 64.0, 72.0, 72.0))
        self._top(p, "none" if self.wearing() in HATS | {"pumpkin"} else top, t, (lx, ly), (rx, ry))  # (a hat from the wardrobe, or the
        # pumpkin's own stem, in place of its own top)
        p.restore()

    def _top(self, p, top, t, left, right):
        """What a builder pal has on top, from Dotpals' own drawing (custom.js TOPS), in its coordinates."""
        (lx, ly), (rx, ry) = left, right

        def shape(d, fill=None, line=None, width=2.0, alpha=255):
            path = self._svg(d)
            if fill:
                c = QColor(fill)
                c.setAlpha(alpha)
                p.fillPath(path, c)
            if line:
                p.strokePath(path, QPen(QColor(line), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if top == "horns":
            shape(f"M{lx + 2} {ly + 6} C{lx - 12} {ly - 6} {lx - 14} {ly - 22} {lx - 6} {ly - 34} C{lx - 2} {ly - 20} {lx + 8} {ly - 10} {lx + 16} {ly - 2} Z "
                  f"M{rx - 2} {ry + 6} C{rx + 12} {ry - 6} {rx + 14} {ry - 22} {rx + 6} {ry - 34} C{rx + 2} {ry - 20} {rx - 8} {ry - 10} {rx - 16} {ry - 2} Z",
                  "#F3E6C8", "#C9B58C")
        elif top == "antenna":
            shape(f"M100 {t + 4} C100 {t - 12} 104 {t - 24} 100 {t - 34}", line="#2A2A33", width=5.0)
            p.setBrush(QColor("#FFE066"))
            p.setPen(QPen(QColor("#FFB000"), 2.0))
            p.drawEllipse(QRectF(90.0, float(t - 50), 20.0, 20.0))
            p.setPen(Qt.PenStyle.NoPen)
        elif top == "sprout":
            shape(f"M100 {t + 4} C100 {t - 8} 100 {t - 16} 100 {t - 22}", line="#3E8E2E", width=4.5)
            shape(f"M100 {t - 20} C88 {t - 38} 70 {t - 36} 66 {t - 26} C78 {t - 18} 92 {t - 16} 100 {t - 20} Z "
                  f"M100 {t - 20} C112 {t - 40} 132 {t - 40} 136 {t - 30} C124 {t - 20} 110 {t - 16} 100 {t - 20} Z", "#6FCF4A")
        elif top == "sparkle":
            shape(f"M146 {t - 38} Q149 {t - 24} 162 {t - 21} Q149 {t - 18} 146 {t - 4} Q143 {t - 18} 130 {t - 21} Q143 {t - 24} 146 {t - 38} Z", "#FFF3A6")
            shape(f"M52 {t - 12} Q53.5 {t - 6} 60 {t - 4.5} Q53.5 {t - 3} 52 {t + 3} Q50.5 {t - 3} 44 {t - 4.5} Q50.5 {t - 6} 52 {t - 12} Z", "#FFF3A6",
                  alpha=204)
        elif top == "bow":
            p.save()
            p.translate(float(rx - 18), float(ry - 4))
            p.rotate(18.0)
            shape("M0 0 L-24 -14 C-30 -4 -30 6 -24 14 Z M0 0 L24 -14 C30 -4 30 6 24 14 Z", "#FF4D8D")
            p.setBrush(QColor("#E0306F"))
            p.drawEllipse(QRectF(-7.0, -7.0, 14.0, 14.0))
            p.restore()
        elif top == "crown":
            shape(f"M72 {t + 6} L68 {t - 26} L86 {t - 10} L100 {t - 34} L114 {t - 10} L132 {t - 26} L128 {t + 6} Z", "#FFC933", "#D99A00", 2.5)
            p.setBrush(QColor("#FF4D7E"))
            p.drawEllipse(QRectF(95.5, float(t - 10.5), 9.0, 9.0))
        elif top == "beret":
            felt = QColor("#1A1A1A")
            for cx, cy, w, h in ((100, t + 8, 74, 20), (96, t - 6, 60, 28)):
                p.save()
                p.translate(float(cx), float(cy))
                p.rotate(-6.0)
                p.setBrush(felt)
                p.drawEllipse(QRectF(-float(w), -float(h), 2.0 * w, 2.0 * h))
                p.restore()
            p.setBrush(felt)
            p.drawEllipse(QRectF(80.0, float(t - 42), 16.0, 16.0))

    def _pal_eyes(self, p, ink, ex, ey):
        """A builder pal's own eyes at rest (Dotpals' EYES), in Monday's units around the face's origin."""
        style, k = self._parts()[1], DP_K
        white, shine = QColor(255, 255, 255), QColor(255, 255, 255, 230)
        for sgn in (-1, 1):
            x = sgn * ex
            if style == "dots":
                p.setBrush(ink)
                p.drawEllipse(QRectF(x - 7.5 * k, ey - 11.5 * k, 15 * k, 23 * k))
                p.setBrush(shine)
                p.drawEllipse(QRectF(x - 2 * k - 2.2 * k, ey - 5 * k - 2.2 * k, 4.4 * k, 4.4 * k))
            elif style == "round":
                p.setBrush(ink)
                p.drawEllipse(QRectF(x - 10.5 * k, ey - 10.5 * k, 21 * k, 21 * k))
                p.setBrush(shine)
                p.drawEllipse(QRectF(x - 3 * k - 3 * k, ey - 4 * k - 3 * k, 6 * k, 6 * k))
            elif style == "googly":
                big = (22 if ex / k > 30 else 17) * k
                p.setBrush(white)
                p.drawEllipse(QRectF(x - big, ey - big, 2 * big, 2 * big))
                pupil = (11.5 if ex / k > 30 else 9) * k
                p.setBrush(ink)
                p.drawEllipse(QRectF(x - pupil, ey - pupil, 2 * pupil, 2 * pupil))
                p.setBrush(shine)
                p.drawEllipse(QRectF(x - 4 * k - 3 * k, ey - 4 * k - 3 * k, 6 * k, 6 * k))
            elif style == "pixel":
                p.fillRect(QRectF(x - 9 * k, ey - 9 * k, 18 * k, 18 * k), ink)
                p.fillRect(QRectF(x - 6 * k, ey - 6 * k, 5 * k, 5 * k), white)
            elif style == "visor":
                p.fillRect(QRectF(x - 8.5 * k, ey - 14 * k, 17 * k, 28 * k), QColor("#DFF4FF"))
            elif style == "shades":
                p.setBrush(shine)
                p.drawEllipse(QRectF(x - 7 * k - 3 * k, ey - 7 * k - 3 * k, 6 * k, 6 * k))
        if self.p["kind"] == "webby":  # its two small eyes above the big ones
            p.setBrush(ink)
            for sgn in (-1, 1):
                p.drawEllipse(QRectF(sgn * 7.0 - 1.8, ey - 9.5, 3.6, 3.6))

    def _pal_mask(self, p, ex, ey):
        """What a builder pal wears over its eyes in every mood: Grok's dark visor, Lovi's sunglasses (Dotpals' visor and shades)."""
        style, k = self._parts()[1], DP_K
        if style == "visor":
            w = (2 * ex / k + 68) * k
            p.fillRect(QRectF(-w / 2, ey - 27 * k, w, 54 * k), QColor("#0D1117"))
        elif style == "shades":
            p.setPen(QPen(QColor("#0B0B12"), 4.5 * k))
            p.setBrush(Qt.BrushStyle.NoBrush)
            _arc(p, -6 * k, ey - 10 * k, 12 * k, 12 * k, 200.0, 140.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#101014"))
            for sgn in (-1, 1):
                p.drawEllipse(QRectF(sgn * ex - 19 * k, ey - 19 * k, 38 * k, 38 * k))

    def _maus(self, p, body):
        """OpenMausBot's Maus: a mouse pointer's arrow with rounded corners, in a 3-stop diagonal gradient of its colour; its face sits
        in the arrow's wide part; its eyes are the accent."""
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(-24.0, -38.0), QPointF(28.0, 10.0), QPointF(6.0, 12.0), QPointF(18.0, 36.0), QPointF(8.0, 41.0),
                                   QPointF(-3.0, 17.0), QPointF(-24.0, 33.0)]))
        path.closeSubpath()
        g = QLinearGradient(QPointF(-24.0, -38.0), QPointF(18.0, 40.0))
        g.setColorAt(0.0, _tone(body, 0.35))
        g.setColorAt(0.5, QColor(body))
        g.setColorAt(1.0, _tone(body, -0.28))
        p.fillPath(path, g)
        pen = QPen(QBrush(g), 6.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, pen)
        p.translate(-6.0, 2.0)

    def _paw(self, p, body, wave):
        p.save()
        p.translate(36.0, 4.0)
        p.rotate(-40.0 + wave * 35.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body)
        p.drawEllipse(QRectF(-5.0, -16.0, 10.0, 16.0))
        p.restore()

    def _face(self, p, now, mood):
        """Eyes, mouth, cheeks: the state, as a face (notch.Pet._face)."""
        bot = self.p["kind"] in ("bot", "maus")  # a robot's screen and a Maus show their face in the accent
        ink = QColor(self.accent if bot else self.ink)
        dark = QColor(40, 20, 24)
        witch = self.p["kind"] == "witch"  # its eyes closer, upright ovals; at rest no mouth (the owner's reference)
        ey, ex = (-6.0 if self.p["kind"] == "gem" else -4.0), (10.0 if witch else 13.0)
        parts = self._parts()
        my_pal = 0.0
        if parts:  # Dotpals' builder: the eyes, mouth and cheeks where its shape puts them
            (eye_y, spread), mouth_y, cheek = DP_SHAPES[parts[0]][4], DP_SHAPES[parts[0]][5], DP_SHAPES[parts[0]][6]
            ey, ex, my_pal = (eye_y - DP_Y) * DP_K, spread * DP_K, (mouth_y - DP_Y) * DP_K
            self._pal_mask(p, ex, ey)
            if parts[1] in ("visor", "shades"):
                ink = QColor("#DFF4FF" if parts[1] == "visor" else "#FFFFFF")
        pen = QPen(ink, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        temper = self.p["temper"]
        if mood == "idle" and temper == "sleepy":
            mood = "sleepy"

        def line(x1, y1, x2, y2):
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.setPen(Qt.PenStyle.NoPen)

        def arc(x, y, w, h, a, s):
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            _arc(p, x, y, w, h, a, s)
            p.setPen(Qt.PenStyle.NoPen)

        def dots(r=4.6, dx=0.0, dy=0.0):
            dx, dy = dx + self.look[0], dy + self.look[1]  # on the pointer
            if parts:
                p.save()
                p.translate(dx, dy)
                self._pal_eyes(p, ink, ex, ey)
                p.restore()
                return
            p.setBrush(ink)
            for s in (-1, 1):
                if witch:  # upright ovals, the far one narrower: its face turned towards what the notch shows
                    wd = r * (1.36 if s < 0 else 1.1)
                    p.drawEllipse(QRectF(s * ex - wd / 2 + dx, ey - r + dy, wd, r * 2))
                else:
                    p.drawEllipse(QRectF(s * ex - r + dx, ey - r + dy, r * 2, r * 2))

        if mood == "offline":
            for s in (-1, 1):
                line(s * ex - 3.5, ey - 3.5, s * ex + 3.5, ey + 3.5)
                line(s * ex - 3.5, ey + 3.5, s * ex + 3.5, ey - 3.5)
        elif mood == "dizzy":
            for s in (-1, 1):
                for i, rr in enumerate((6.0, 3.5)):
                    arc(s * ex - rr, ey - rr, rr * 2, rr * 2, (now * 400 + i * 120) % 360, 270.0)
        elif mood in ("happy", "eating"):
            for s in (-1, 1):
                arc(s * ex - 5.0, ey - 4.0, 10.0, 8.0, 180.0, 180.0)   # ^ ^
        elif mood == "sleepy":
            for s in (-1, 1):
                arc(s * ex - 5.0, ey - 4.0, 10.0, 8.0, 0.0, 180.0)     # closed, content
        elif mood in ("idle", "listening", "working", "speaking") and self._blinking(now):
            for s in (-1, 1):
                line(s * ex - 4.0, ey, s * ex + 4.0, ey)
        elif mood == "flat":
            for s in (-1, 1):
                line(s * ex - 4.5, ey, s * ex + 4.5, ey)
        elif mood == "sad":
            for s in (-1, 1):
                line(s * ex + s * 4.0, ey + 1.0, s * ex - s * 4.0, ey - 2.0)   # worried
        elif mood == "working":
            dots(dx=math.sin(now * 2.4) * 3.0)                               # looking along the work
        elif mood == "hearing":
            dots(r=4.8, dx=2.0, dy=-2.0)
        elif mood in ("listening", "waiting"):
            dots(r=5.6)
        elif mood == "idle" and temper == "calm":
            p.setBrush(ink)
            for s in (-1, 1):
                p.drawPie(QRectF(s * ex - 4.6, ey - 4.6, 9.2, 9.2), 0, -180 * 16)   # half-lidded
        elif parts:
            self._pal_eyes(p, ink, ex, ey)  # Dotpals' own eyes at rest
        else:
            dots()
        my = my_pal if parts else 5.0 if witch else 10.0  # (the witch's face is lower: its hat)
        if mood == "eating":
            t = (now - self.eat_at) / EAT_S
            opened = max(0.0, math.sin(t * math.pi * 4)) * 9.0 if 0 <= t < 1 else 0.0
            p.setBrush(dark)
            p.drawEllipse(QRectF(-7.0, my - 2.0, 14.0, 4.0 + opened))
        elif mood == "speaking":
            h = 3.0 + abs(math.sin(now * 11)) * 5.0
            p.setBrush(dark)
            p.drawEllipse(QRectF(-5.0, my - 1.0, 10.0, h))
        elif mood in ("hearing", "waiting"):
            p.setBrush(dark)
            p.drawEllipse(QRectF(-3.0, my, 6.0, 6.0))
        elif mood == "sad":
            arc(-6.0, my + 2.0, 12.0, 8.0, 200.0, 140.0)
        elif mood == "happy" or (mood == "idle" and temper == "cheerful" and not witch):
            arc(-5.0, my - 4.0, 10.0, 7.0, 20.0, 140.0)
        elif mood == "dizzy":
            arc(-6.0, my, 6.0, 5.0, 180.0, 180.0)
            arc(0.0, my, 6.0, 5.0, 0.0, 180.0)
        if not bot and (mood in ("happy", "eating") or (mood == "idle" and temper == "cheerful" and not witch)):
            cx = (parts and DP_SHAPES[parts[0]][6] * DP_K) or (15.0 if witch else 22.0)
            p.setBrush(QColor(255, 120, 150, 110))
            for s in (-1, 1):
                p.drawEllipse(QRectF(s * cx - 5.0, (my - 4.0) if parts else ey + 6.0, 10.0, 6.0))

    def wearing(self):
        """What it has on now: the owner's pick ("auto": the season's, notch.wear_now); a hat never on the witch's own hat."""
        w = wear_now(self.p["wear"])
        return "none" if self.p["kind"] == "witch" and w in HATS else w

    def _head_top(self):
        """Where its head ends (the pet's 100 x 100 box, its middle at 0, 0): a hat sits there."""
        parts = self._parts()
        if parts:
            return (DP_SHAPES[parts[0]][2] - DP_Y) * DP_K
        return -36.0 if self.p["kind"] == "gem" else -28.0

    def _eyes_at(self):
        """(y, half the distance between them) of its eyes: glasses go there."""
        if self._parts():
            (eye_y, spread) = DP_SHAPES[self._parts()[0]][4]
            return (eye_y - DP_Y) * DP_K, spread * DP_K
        return (-6.0 if self.p["kind"] == "gem" else -4.0), (10.0 if self.p["kind"] == "witch" else 13.0)

    def _ridges(self, p):
        """The pumpkin's ridges, on its body under its face."""
        p.save()
        p.setPen(QPen(QColor(150, 64, 10, 120), 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for dx in (-17.0, 0.0, 17.0):
            p.drawArc(QRectF(dx - 9.0, -26.0, 18.0, 56.0), 90 * 16, 180 * 16 if dx <= 0 else -180 * 16)
        p.restore()

    def _wear(self, p):
        """What it wears: Dotpals' black beret and round glasses, and the 2026-10-26 recording's wardrobe (a party hat, a beanie, a crown,
        sunglasses, a scarf, a witch hat, a pumpkin's stem, a Santa hat, bunny ears), drawn: never a picture or a glyph."""
        wear, top = self.wearing(), self._head_top()
        ey, ex = self._eyes_at()
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        if wear == "beret" and not (self._parts() and self._parts()[2] == "beret"):  # (Blu's own beret is its top)
            p.rotate(-8.0)
            p.setBrush(QColor(24, 24, 27))
            p.drawEllipse(QRectF(-31.0, top - 8.0, 60.0, 22.0))
            p.drawEllipse(QRectF(-3.5, top - 14.0, 7.0, 8.0))
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawEllipse(QRectF(-20.0, top - 5.0, 22.0, 6.0))  # a sheen
        elif wear == "party":
            p.translate(4.0, top + 4.0)
            p.rotate(10.0)
            p.setBrush(QColor("#FF6FA8"))
            p.drawPolygon(QPolygonF([QPointF(-14.0, 0.0), QPointF(14.0, 0.0), QPointF(0.0, -32.0)]))
            p.setBrush(QColor(255, 255, 255, 200))
            for y0 in (-9.0, -19.0):  # its stripes, inside the cone
                w0, w1 = 14.0 * (1 + y0 / 32.0), 14.0 * (1 + (y0 - 4.0) / 32.0)
                p.drawPolygon(QPolygonF([QPointF(-w0, y0), QPointF(w0, y0), QPointF(w1, y0 - 4.0), QPointF(-w1, y0 - 4.0)]))
            p.setBrush(QColor("#FFD54A"))
            p.drawEllipse(QPointF(0.0, -32.0), 4.5, 4.5)
        elif wear == "beanie":
            p.setBrush(QColor("#2F6FDB"))
            dome = QPainterPath()
            dome.moveTo(-31.0, top + 9.0)
            dome.cubicTo(-31.0, top - 22.0, 31.0, top - 22.0, 31.0, top + 9.0)
            dome.closeSubpath()
            p.drawPath(dome)
            p.setBrush(QColor("#1F52AE"))
            p.drawRoundedRect(QRectF(-33.0, top + 2.0, 66.0, 10.0), 5.0, 5.0)  # its folded band
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(QPointF(0.0, top - 16.0), 6.5, 6.5)
        elif wear == "crown":
            p.setBrush(QColor("#F5C542"))
            p.drawPolygon(QPolygonF([QPointF(-21.0, top + 4.0), QPointF(21.0, top + 4.0), QPointF(23.0, top - 16.0), QPointF(11.0, top - 6.0),
                                     QPointF(0.0, top - 20.0), QPointF(-11.0, top - 6.0), QPointF(-23.0, top - 16.0)]))
            p.setBrush(QColor("#D9542B"))
            p.drawEllipse(QPointF(0.0, top - 2.0), 3.2, 3.2)
            p.setBrush(QColor("#3FA9F5"))
            for x in (-12.0, 12.0):
                p.drawEllipse(QPointF(x, top - 1.0), 2.2, 2.2)
        elif wear == "sunglasses":
            p.setBrush(QColor(18, 18, 22))
            for sgn in (-1, 1):
                p.drawRoundedRect(QRectF(sgn * ex - 9.5, ey - 6.5, 19.0, 13.0), 5.0, 5.0)
            p.setPen(QPen(QColor(18, 18, 22), 2.6))
            p.drawLine(QPointF(-ex + 9.0, ey - 2.0), QPointF(ex - 9.0, ey - 2.0))  # the bridge
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 60))
            for sgn in (-1, 1):
                p.drawRoundedRect(QRectF(sgn * ex - 6.5, ey - 4.5, 6.0, 3.0), 1.5, 1.5)  # a glint
        elif wear == "glasses" or self.p["kind"] == "sunny":  # Sunny always wears hers (Dotpals')
            p.setPen(QPen(QColor(30, 30, 34), 2.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for s in (-1, 1):
                p.drawEllipse(QRectF(s * ex - 8.5, ey - 8.5, 17.0, 17.0))
            p.drawLine(QPointF(-ex + 8.5, ey - 1.5), QPointF(ex - 8.5, ey - 1.5))   # the bridge
        elif wear == "scarf":
            p.setBrush(QColor("#E5484D"))
            p.drawRoundedRect(QRectF(-33.0, 14.0, 66.0, 10.0), 5.0, 5.0)
            p.drawRoundedRect(QRectF(14.0, 18.0, 10.0, 20.0), 4.0, 4.0)  # its end, hanging
            p.setBrush(QColor(255, 255, 255, 220))
            for x in (-22.0, -6.0, 10.0):
                p.drawRect(QRectF(x, 14.0, 4.0, 10.0))
            p.drawRect(QRectF(14.0, 29.0, 10.0, 3.0))
        elif wear == "witch":
            p.setBrush(QColor("#6A35C9"))
            p.drawEllipse(QRectF(-36.0, top - 6.0, 72.0, 14.0))  # its brim
            cone = QPainterPath()
            cone.moveTo(-17.0, top)
            cone.cubicTo(-12.0, top - 18.0, -2.0, top - 32.0, 10.0, top - 38.0)
            cone.cubicTo(6.0, top - 28.0, 12.0, top - 14.0, 17.0, top)
            cone.closeSubpath()
            p.drawPath(cone)
            p.setBrush(QColor("#F28C28"))
            p.drawRect(QRectF(-16.0, top - 6.0, 32.0, 5.0))  # its band
            p.setBrush(QColor("#FFC93C"))
            p.drawRect(QRectF(-3.0, top - 7.0, 6.0, 7.0))
        elif wear == "pumpkin":  # its stem (the body is the pumpkin)
            p.setBrush(QColor("#3B7D2B"))
            p.drawRoundedRect(QRectF(-3.0, top - 9.0, 6.0, 11.0), 2.5, 2.5)
            p.setBrush(QColor("#5DAA3C"))
            p.drawEllipse(QRectF(2.0, top - 9.0, 11.0, 6.0))
        elif wear == "santa":
            p.setBrush(QColor("#D9363E"))
            hat = QPainterPath()
            hat.moveTo(-26.0, top + 6.0)
            hat.cubicTo(-22.0, top - 24.0, 12.0, top - 30.0, 34.0, top - 6.0)
            hat.lineTo(26.0, top + 6.0)
            hat.closeSubpath()
            p.drawPath(hat)
            p.setBrush(QColor(250, 250, 250))
            p.drawRoundedRect(QRectF(-30.0, top + 1.0, 60.0, 10.0), 5.0, 5.0)  # its white trim
            p.drawEllipse(QPointF(35.0, top - 5.0), 5.5, 5.5)
        elif wear == "bunny":
            for sgn in (-1, 1):
                p.save()
                p.translate(sgn * 11.0, top + 2.0)
                p.rotate(sgn * 12.0)
                p.setBrush(QColor(250, 250, 252))
                p.drawEllipse(QRectF(-6.5, -34.0, 13.0, 36.0))
                p.setBrush(QColor(255, 160, 185))
                p.drawEllipse(QRectF(-3.5, -29.0, 7.0, 25.0))
                p.restore()
        p.restore()

    def _blinking(self, now):
        if now > self.blink_at + 0.14:
            self.blink_at = now + 2.5 + (int(now * 7) % 30) / 10.0
        return self.blink_at <= now <= self.blink_at + 0.14

    def _extras(self, p, now, mood, dy):
        """Around the pet: sparkles when done, z's when asleep, a ? when waiting, a file going in, the sound it hears."""
        if mood == "happy":
            for i, (x, y) in enumerate(((14.0, 22.0), (84.0, 18.0), (88.0, 60.0))):
                p.setBrush(QColor(255, 230, 140, int(255 * max(0.0, math.sin(now * 3 + i * 2.1)))))
                p.drawPolygon(_star_points(x, y, 5.0))
        elif mood == "sleepy":
            f = QFont(_families()[0])
            f.setPixelSize(12)
            f.setBold(True)
            p.setFont(f)
            for i in range(2):
                t = (now * 0.6 + i * 0.5) % 1.0
                p.setPen(QColor(180, 190, 200, int(200 * (1 - t))))
                p.drawText(QRectF(70.0 + t * 12, 26.0 - t * 22, 30, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "z")
            p.setPen(Qt.PenStyle.NoPen)
        elif mood == "waiting":
            f = QFont(_families()[0])
            f.setPixelSize(17)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(WARN_PET))
            p.drawText(QRectF(72.0, 4.0 + math.sin(now * 4) * 2, 30, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "?")
            p.setPen(Qt.PenStyle.NoPen)
        elif mood == "eating" and (now - self.eat_at) < 0.6:  # a page with a red label, falling into the mouth
            y = 4.0 + (now - self.eat_at) / 0.6 * 52.0
            p.fillRect(QRectF(44.0, y, 12.0, 15.0), QColor(250, 250, 250))
            p.fillRect(QRectF(46.0, y + 9.0, 8.0, 3.0), QColor(BAD_PET))
        elif mood in ("listening", "hearing"):
            p.setPen(QPen(QColor(85, 224, 240, 170 if mood == "hearing" else 70), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(3 if mood == "hearing" else 2):
                r = 8.0 + i * 6.0 + (math.sin(now * 6) * 1.5 if mood == "hearing" else 0.0)
                _arc(p, 86.0 - r, 50.0 + dy - r, r * 2, r * 2, -40.0, 80.0)
            p.setPen(Qt.PenStyle.NoPen)


def pal(color: str, **more) -> PetArt:
    """One of the banners' little pals: Monday's blob in a colour."""
    return PetArt({"kind": "blob", "body": QColor(PALS.get(color, color)).name().upper(), **more})


# Claude Code's own mascot, as its start screen draws it in block characters (" ▐▛███▜▌" / "▝▜█████▛▘" / "  ▘▘ ▝▝"): each character two
# of these cells wide and two tall, a cell twice as tall as it is wide. "#" is its body, "." its eyes (holes), " " nothing.
CLAWD = ("   ############   ",
         "   ##.######.##   ",
         " ################ ",
         "   ############   ",
         "    # #    # #    ")
CLAWD_COLOR = "#D77757"   # Claude's orange, as Claude Code draws it


class Clawd:
    """Claude Code's mascot in the pet's place while Claude Code asks (the owner, 2026-10-20: "the mascot should be of the Claude when
    the question is from Claude"). Drawn as PetArt is (draw(p, rect, now, mood)), with the pets' gentle bob unless motion is off."""

    def draw(self, p: QPainter, rect: QRectF, now: float, mood: str, scale=None):
        cols, rows = len(CLAWD[0]), len(CLAWD)
        cw = rect.width() * 0.86 / cols
        ch = cw * 2
        bob = math.sin(now * 2.1) * rect.height() * 0.025 if PAL_MOTION and not reduced_motion() else 0.0
        x0, y0 = rect.center().x() - cw * cols / 2, rect.center().y() - ch * rows / 2 + bob
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CLAWD_COLOR))
        for r, line in enumerate(CLAWD):
            for c, cell in enumerate(line):
                if cell == "#":
                    p.drawRect(QRectF(x0 + c * cw, y0 + r * ch, cw + 0.4, ch + 0.4))  # (+0.4: no seams between cells)
        p.restore()


class LogoPal:
    """Another tool's own logo (its program's icon, from the OS) in the pet's place while it asks: "that IDE's logo type of mascot"."""

    def __init__(self, icon: QIcon):
        self.icon = icon

    def draw(self, p: QPainter, rect: QRectF, now: float, mood: str, scale=None):
        s = min(rect.width(), rect.height()) * 0.64
        bob = math.sin(now * 2.1) * rect.height() * 0.025 if PAL_MOTION and not reduced_motion() else 0.0
        self.icon.paint(p, int(rect.center().x() - s / 2), int(rect.center().y() - s / 2 + bob), int(s), int(s))


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def pet_image(prefs=None, size: int = 256):
    """The owner's pet, still, on Monday's black rounded tile: the app's icon at one size (the logo the rail shows, 2026-10-20: "the
    logo of Monday should change the app logo"). Drawn at 4x and reduced, so 16 px stays clean."""
    from PySide6.QtGui import QImage
    big = size * 4
    img = QImage(big, big, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(RAIL))
    p.drawRoundedRect(QRectF(0, 0, big, big), big * 0.22, big * 0.22)
    art = PetArt(prefs)
    art.motion = False  # (a still: no bob, no blink caught mid-way)
    art.draw(p, QRectF(big * 0.08, big * 0.08, big * 0.84, big * 0.84), time.monotonic(), "idle", scale=1.0)
    p.end()
    return img.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def pet_icon(prefs=None) -> QIcon:
    """The app's icon (window, taskbar, tray) at every size Windows asks for."""
    ic = QIcon()
    for n in ICON_SIZES:
        ic.addPixmap(QPixmap.fromImage(pet_image(prefs, n)))
    return ic


class Pet(QWidget):
    """A pet as a widget (the rail's logo, empty states, the notch): art is a PetArt (the owner's pet unless given). mood() sets its
    face from a real state; live=True keeps it moving (blinks, the bob) while it shows and motion is allowed, else it is drawn once."""

    def __init__(self, size: int = 40, art: PetArt | None = None, mood: str = "idle", live: bool = False, name: str = "Monday",
                 parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.art = art or PetArt()
        self._mood, self.live, self.still_at = mood, live, 1.0  # still_at: the moment a still pet is drawn at (no blink, no bob)
        self.setAccessibleName(name)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._t = QTimer(self)
        self._t.setInterval(33)
        self._t.timeout.connect(self.update)

    def mood(self, mood: str):
        if mood != self._mood:
            self._mood = mood
            self.update()

    def showEvent(self, e):
        if self.live and self.art.motion:
            self._t.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._t.stop()
        super().hideEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        now = time.monotonic() if (self.live and self.art.motion) else self.still_at
        self.art.draw(p, QRectF(self.rect()), now, self._mood)
        p.end()


# -- focus: a ring only when the keyboard put it there ---------------------------------------------------------------------
class _Keyed:
    _kb = False

    def focusInEvent(self, e):
        self._kb = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason,
                                  Qt.FocusReason.OtherFocusReason)
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        self._kb = False
        super().focusOutEvent(e)
        self.update()

    def _ring(self, p: QPainter, rect: QRectF, radius: float):
        if self._kb and self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(FOCUS), 2))
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)


class Hover:
    """A 0..1 hover light that fades over 180 ms (Coucou's .18 s), from wherever it is."""

    def __init__(self, widget: QWidget, ms: int = 180):
        self.value = 0.0
        self._a = QVariantAnimation(widget)
        self._a.setDuration(ms)
        self._a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a.valueChanged.connect(lambda v: (setattr(self, "value", float(v)), widget.update()))

    def go(self, on: bool):
        target = 1.0 if on else 0.0
        if reduced_motion():
            self.value = target
            self._a.valueChanged.emit(target)
            return
        self._a.stop()
        self._a.setStartValue(self.value)
        self._a.setEndValue(target)
        self._a.start()


class SmoothScroll(QObject):
    """A scroll area's mouse wheel, eased: each notch glides WHEEL_PX over SCROLL_MS (OutCubic) instead of jumping three lines (the owner,
    2026-10-20: "fix scroll"); notches while it glides add up. A touchpad's pixel deltas are smooth already and go through untouched, so
    does a wheel at the end of its travel (the page around it scrolls then); reduced motion jumps, as everywhere in the kit."""
    WHEEL_PX, SCROLL_MS = 96, 150  # one notch (~Chrome's 100 px), its glide

    def __init__(self, area):
        super().__init__(area)
        self.bar, self.target = area.verticalScrollBar(), None
        self._a = QVariantAnimation(self)
        self._a.setDuration(self.SCROLL_MS)
        self._a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a.valueChanged.connect(lambda v: self.bar.setValue(int(v)))
        self._a.finished.connect(lambda: setattr(self, "target", None))
        area.viewport().installEventFilter(self)

    def eventFilter(self, _obj, e):
        if e.type() != QEvent.Type.Wheel or e.pixelDelta().y() or not e.angleDelta().y() or reduced_motion():
            return False
        start = self.bar.value() if self.target is None else self.target
        want = max(0, min(self.bar.maximum(), start - e.angleDelta().y() * self.WHEEL_PX // 120))
        if want == self.bar.value() and self.target is None:
            return False  # nothing left to scroll this way: Qt passes the wheel up to the page around
        self.target = want
        self._a.stop()
        self._a.setStartValue(self.bar.value())
        self._a.setEndValue(want)
        self._a.start()
        e.accept()
        return True


# -- controls --------------------------------------------------------------------------------------------------------------
class NavItem(_Keyed, QAbstractButton):
    """A sidebar entry: its outline icon and its name; lit when it is the page on show."""

    def __init__(self, icon_name: str, text: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.setText(text)
        self.setAccessibleName(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedHeight(NAV_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.badge = ""  # a count the page reports (approvals waiting, notifications): shown only when real
        self._hover = Hover(self)

    def set_badge(self, text: str):
        if text != self.badge:
            self.badge = text
            self.update()

    def enterEvent(self, e):
        self._hover.go(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover.go(False)
        super().leaveEvent(e)

    def sizeHint(self):
        return QSize(RAIL_W - 24, NAV_H)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        if on or self._hover.value:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(NAV_ON) if on else alpha(NAV_ON, self._hover.value))
            p.drawRoundedRect(r, 8, 8)
        paint_icon(p, self.icon_name, QRectF(14, (r.height() - 16) / 2, 16, 16), TEXT if on else ICON, 1.9)
        p.setPen(QColor(TEXT if on else TEXT_NAV))
        p.setFont(font(13.5, 600))
        p.drawText(QRectF(40, 0, r.width() - 40, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        if self.badge:
            p.setFont(font(11, 700))
            tw = QFontMetricsF(p.font()).horizontalAdvance(self.badge) + 12
            b = QRectF(r.right() - tw - 8, (r.height() - 18) / 2, tw, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ORANGE))
            p.drawRoundedRect(b, 9, 9)
            p.setPen(QColor(ON_ORANGE))
            p.drawText(b, Qt.AlignmentFlag.AlignCenter, self.badge)
        self._ring(p, r, 8)
        p.end()


class Button(_Keyed, QAbstractButton):
    """The recording's buttons. kind: primary (orange, the one action a page leads with), secondary (charcoal), light (white pill:
    Install…, Save key), ghost (words only), danger (Clear history), save (charcoal pill beside a field). dot draws its leading
    dot (Record a call's)."""
    KINDS = {  # background, hover, text, pill
        "primary": (ORANGE, ORANGE_HOVER, ON_ORANGE, False),
        "secondary": (BUTTON, BUTTON_HOVER, TEXT, False),
        "light": ("#f4f4f5", "#ffffff", "#111214", True),
        "ghost": ("#00000000", BUTTON, TEXT_2, False),
        "danger": (RED_BG, "#45191f", RED_TEXT, False),
        "save": (BUTTON, BUTTON_HOVER, TEXT, True),
        "link": ("#00000000", "#00000000", BLUE, False),       # words that go somewhere (the recording's "Open Settings")
        "quiet": ("#00000000", "#00000000", TEXT_2, False),   # a link in the notch's grey ("Settings…")
        "plain": ("#00000000", "#00000000", TEXT, False),     # a link in white ("Open Visual Studio Code")
    }

    def __init__(self, text: str, kind: str = "secondary", icon_name: str | None = None, dot: bool = False, name: str | None = None,
                 height: int = 32, parent=None, size: float = 13, key: str = ""):
        super().__init__(parent)
        self.kind, self.icon_name, self.dot, self.size = kind, icon_name, dot, size  # size: its words' px (the notch's links are 10.5)
        self.key = key  # its shortcut in a little outlined box after the words (the 2026-10-26 recording's "Deny [N]"), as this PC holds it
        self.setText(text)
        self.setAccessibleName(name or text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(height)
        self._hover = Hover(self)
        self._down = False
        self.pressed.connect(lambda: self._press(True))
        self.released.connect(lambda: self._press(False))

    def _press(self, down):
        self._down = down
        self.update()

    def setText(self, text):
        super().setText(text)
        self.updateGeometry()

    def enterEvent(self, e):
        self._hover.go(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover.go(False)
        super().leaveEvent(e)

    def sizeHint(self):
        fm = QFontMetricsF(font(self.size, 600))
        w = fm.horizontalAdvance(self.text()) + (6 if self.kind in ("link", "quiet", "plain") else 32 if self.KINDS[self.kind][3] else 28)
        if self.icon_name:
            w += 22 if self.text() else 4
        if self.dot:
            w += 16
        if self.key:
            w += self._key_w() + 8
        return QSize(int(math.ceil(w)), self.height())

    def _key_w(self):
        return QFontMetricsF(font(KEY_PX, 600)).horizontalAdvance(self.key) + 8

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        bg, hover, fg, pill = self.KINDS[self.kind]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if not self.isEnabled():
            p.setOpacity(0.45)
        rad = r.height() / 2 if pill else 9
        fill = mix(bg, hover, self._hover.value) if self.isEnabled() else QColor(bg)
        if self._down:
            fill = fill.darker(112)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, rad, rad)
        p.setFont(font(self.size, 600))
        fm = QFontMetricsF(p.font())
        lead = (16 if self.dot else 0) + ((22 if self.text() else 16) if self.icon_name else 0)
        # squeezed below its words (a layout that can't give it room): they end in "…", never cut on both sides (2026-10-20)
        kw = self._key_w() + 8 if self.key else 0.0
        room = r.width() - lead - kw - (4 if self.kind in ("link", "quiet", "plain") else 20)
        text = fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0.0, room))
        tw = fm.horizontalAdvance(text)
        x = r.center().x() - (tw + lead + kw) / 2
        if self.dot:
            p.setBrush(QColor(fg))
            p.drawEllipse(QPointF(x + 4, r.center().y()), 3.5, 3.5)
            x += 16
        if self.icon_name:
            paint_icon(p, self.icon_name, QRectF(x, r.center().y() - 8, 16, 16), fg, 2.0)
            x += 22 if self.text() else 16
        p.setPen(QColor(fg).lighter(100 + int(25 * self._hover.value)) if self.kind in ("link", "quiet", "plain") else QColor(fg))
        if self.kind == "quiet" and self._hover.value > 0.5:
            p.setPen(QColor(TEXT))
        p.drawText(QRectF(x, r.top(), tw + 2, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if self.key:  # the key in its box, a shade of the words' own colour
            kr = QRectF(x + tw + 8, r.center().y() - 8, self._key_w(), 16)
            ink = QColor(fg)
            ink.setAlphaF(0.55)
            p.setPen(QPen(ink, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(kr.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
            ink.setAlphaF(0.8)
            p.setPen(ink)
            p.setFont(font(KEY_PX, 600))
            p.drawText(kr, Qt.AlignmentFlag.AlignCenter, self.key)
        self._ring(p, r, rad)
        p.end()


class IconButton(_Keyed, QAbstractButton):
    """A small round icon button (the notch's tabs, its gear and sound, a row's ×). lit draws it as the chosen tab."""

    def __init__(self, icon_name: str, name: str, size: int = 28, lit: bool = False, parent=None):
        super().__init__(parent)
        self.icon_name, self.lit = icon_name, lit
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hover = Hover(self)

    def set_lit(self, lit: bool):
        if lit != self.lit:
            self.lit = lit
            self.update()

    def enterEvent(self, e):
        self._hover.go(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover.go(False)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if not self.isEnabled():
            p.setOpacity(0.4)
        if self.lit or self._hover.value:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#26292d") if self.lit else alpha("#26292d", self._hover.value * 0.8))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        s = r.height() * 0.5
        paint_icon(p, self.icon_name, QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s),
                   TEXT if (self.lit or self._hover.value > 0.5) else ICON, 2.2)
        self._ring(p, r, r.height() / 2)
        p.end()


class Toggle(_Keyed, QAbstractButton):
    """The recording's switch: green with the knob right when on, grey with it left when off. A click, Space or Enter flips it;
    whoever owns it decides what that means (and puts it back with set_quiet when Core refuses)."""

    def __init__(self, name: str, on: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(TOGGLE[0] + 4, TOGGLE[1] + 4)
        self._pos = 1.0 if on else 0.0
        self._a = QVariantAnimation(self)
        self._a.setDuration(GLIDE_MS)
        self._a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._a.valueChanged.connect(self._moved)
        self.toggled.connect(self._glide)

    def _glide(self, on):
        self._a.stop()
        if reduced_motion() or not self.isVisible():
            self._moved(1.0 if on else 0.0)
            return
        self._a.setStartValue(self._pos)
        self._a.setEndValue(1.0 if on else 0.0)
        self._a.start()

    def _moved(self, v):
        self._pos = float(v)
        self.update()

    def set_quiet(self, on: bool):
        """Show Core's value without it counting as the owner's click."""
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        self._a.stop()
        self._moved(1.0 if on else 0.0)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = TOGGLE
        r = QRectF(2, 2, w, h)
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(OFF, GREEN, self._pos))
        p.drawRoundedRect(r, h / 2, h / 2)
        k = h - 4
        x = r.left() + 2 + (w - 4 - k) * self._pos
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(QRectF(x, r.top() + 2.6, k, k))
        p.setBrush(QColor(KNOB))
        p.drawEllipse(QRectF(x, r.top() + 2, k, k))
        self._ring(p, r, h / 2)
        p.end()


class Tick(_Keyed, QAbstractButton):
    """A tick box (a to-do's done): a rounded square, green with its check when ticked."""

    def __init__(self, name: str, on: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setAccessibleName(name)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_quiet(self, on: bool):
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        on = self.isChecked()
        p.setPen(QPen(QColor(GREEN if on else "#4a4f55"), 1.2))
        p.setBrush(QColor(GREEN if on else FIELD))
        p.drawRoundedRect(r, 4.5, 4.5)
        if on:
            paint_icon(p, "check", r.adjusted(2, 2, -2, -2), "#0b0b0c", 3.2)
        self._ring(p, r, 4.5)
        p.end()


class KeyChip(_Keyed, QAbstractButton):
    """A shortcut's keys in a dark chip (monospace, bold). Off draws it dimmed; capturing draws it waiting for keys."""

    def __init__(self, keys: str, name: str, parent=None):
        super().__init__(parent)
        self.setText(keys)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(26)
        self.off = False
        self.capturing = False
        self.failed = False  # Core or the OS refused it: drawn red

    def sizeHint(self):
        fm = QFontMetricsF(font(12, 700, mono=True))
        text = "Press keys…" if self.capturing else self.text()
        return QSize(max(118, int(fm.horizontalAdvance(text)) + 28), 26)

    def set_state(self, off=None, capturing=None, failed=None):
        if off is not None:
            self.off = off
        if capturing is not None:
            self.capturing = capturing
        if failed is not None:
            self.failed = failed
        self.updateGeometry()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        edge = AMBER if self.capturing else (RED if self.failed else CHIP_EDGE)
        p.setPen(QPen(QColor(edge), 1))
        p.setBrush(QColor(CHIP_OFF if self.off else CHIP))
        p.drawRoundedRect(r, 6, 6)
        p.setFont(font(12, 700, mono=True))
        p.setPen(QColor(AMBER if self.capturing else (TEXT_OFF if self.off else TEXT)))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Press keys…" if self.capturing else self.text())
        self._ring(p, r, 6)
        p.end()


class Chip(QLabel):
    """A small tag: PDF / Images (the drop zone's), a model, a provider (with its dot), or amber (Types "ă")."""

    def __init__(self, text: str, tone: str = "plain", dot: str | None = None, parent=None, small: bool = False):
        super().__init__(text, parent)
        self.tone, self.dot_color, self.h = tone, dot, 19 if small else 22  # small: the recording's Types "ă" chip, 59 x 19
        self.setFont(font(11 if small else 12, 600 if tone in ("amber", "lit") else 400))
        self.setFixedHeight(self.h)
        self.setAccessibleName(text)

    def sizeHint(self):
        w = QFontMetricsF(self.font()).horizontalAdvance(self.text()) + (14 if self.h < 22 else 20) + (12 if self.dot_color else 0)
        return QSize(int(math.ceil(w)), self.h)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg, fg, edge = {"amber": (AMBER_BG, AMBER, None), "lit": ("#2a1d1d", TEXT, "#7a3a40"),
                        "plain": ("#1f2226", "#c4c7cc", None), "dark": ("#1c1c20", TEXT_2, None)}[self.tone]  # (dark: the recording's
        # drop chips and model chip, filled #1c1c20, no edge)
        if self.tone == "lit" and self.dot_color:  # the chosen one tinted in its own dot's colour (the recording's chosen provider)
            bg, edge = mix("#16181b", self.dot_color, 0.16).name(), mix("#16181b", self.dot_color, 0.55).name()
        p.setPen(QPen(QColor(edge), 1) if edge else Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x = r.left() + (7 if self.h < 22 else 10)
        if self.dot_color:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.dot_color))
            p.drawEllipse(QPointF(x + 3, r.center().y()), 3, 3)
            x += 12
        p.setPen(QColor(fg))
        p.setFont(self.font())
        p.drawText(QRectF(x, r.top(), r.right() - x, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class Dot(QWidget):
    """A status dot: green (works, checked), red (not set or failed), amber (waiting), grey (off / unknown)."""
    COLORS = {"ok": GREEN_TEXT, "bad": RED, "wait": AMBER, "off": "#6b6f75", "info": BLUE}

    def __init__(self, state: str = "off", size: int = 8, tip: str = "", parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedSize(size + 4, size + 4)
        self.setToolTip(tip)

    def set(self, state: str, tip: str | None = None):
        self.state = state if state in self.COLORS else state
        if tip is not None:
            self.setToolTip(tip)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.COLORS.get(self.state, self.state)))
        s = self.width() - 4
        p.drawEllipse(QRectF(2, 2, s, s))
        p.end()


def label(text: str = "", px: float = 13, weight: int = 400, color: str = TEXT, wrap: bool = False, mono: bool = False,
          select: bool = False) -> QLabel:
    lb = QLabel(text)
    lb.setFont(font(px, weight, mono))
    lb.setStyleSheet(f"color: {color}; background: transparent;")
    lb.setWordWrap(wrap)
    if select:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


class Elided(QLabel):
    """One line that ends in … when it doesn't fit (its whole text in the tooltip and for a screen reader)."""

    def __init__(self, text: str = "", px: float = 13, weight: int = 400, color: str = TEXT):
        super().__init__()
        self.setFont(font(px, weight))
        self.setStyleSheet(f"color: {color}; background: transparent;")
        self.setMinimumWidth(20)
        self._full = ""
        self.set(text)

    def set(self, text: str):
        self._full = " ".join(str(text or "").split())
        self.setToolTip(self._full if len(self._full) > 40 else "")
        self.setAccessibleName(self._full)
        self._fit()

    def resizeEvent(self, e):
        self._fit()
        super().resizeEvent(e)

    def _fit(self):
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(20, self.width())))

    def sizeHint(self):
        return QSize(min(600, self.fontMetrics().horizontalAdvance(self._full) + 4), super().sizeHint().height())


def reveal(path: str) -> str | None:
    """Show a file or folder in this OS's file manager; None, or why it couldn't."""
    path = os.path.abspath(path)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select," + path] if os.path.isfile(path) else ["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        return None
    except OSError as e:
        return f"Couldn't open the folder: {e.strerror or e}"


def _section(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setFont(font(11, 600, spacing=1.0))  # (the recording's caps: FROM ANYWHERE 11 % narrower than at 1.3)
    lb.setStyleSheet(f"color: {TEXT_3}; background: transparent;")
    return lb


class Card(QFrame):
    """A charcoal card on a hairline. Rows go in with row() (a setting: words left, its control right, hairlines between) or
    field() (a label column and its control, the General page's form)."""

    def __init__(self, title: str | None = None, words: str | None = None, dot: str | None = None, up: bool = False,
                 pad: int = 20, parent=None):
        super().__init__(parent)
        self.up = up
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(pad, pad - 2, pad, pad - 2)
        self.lay.setSpacing(10)
        self._rows: list[QWidget] = []
        self.title_label = self.words_label = self.title_dot = None
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            if dot:
                self.title_dot = Dot(dot, 8)
                head.addWidget(self.title_dot)
            self.title_label = label(title, 12.5, 600)  # (the recording's card titles: 12.5 semibold)
            head.addWidget(self.title_label)
            head.addStretch(1)
            self.head = head
            self.lay.addLayout(head)
        if words:
            self.words_label = label(words, 12.5, 400, TEXT_2, wrap=True)
            self.lay.addWidget(self.words_label)

    def row(self, title: str, control: QWidget | None = None, words: str | None = None, extra: QWidget | None = None) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 5, 0, 5)
        h.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(2)
        t = label(title, 13.5, 600)
        left.addWidget(t)
        if words:
            left.addWidget(label(words, 12.5, 400, TEXT_2, wrap=True))
        h.addLayout(left, 1)
        if extra is not None:
            h.addWidget(extra, 0, Qt.AlignmentFlag.AlignVCenter)
        if control is not None:
            h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        row.setMinimumHeight(ROW_H)
        if self._rows:  # the hairline and its row as one (the recording's rows are ~58 apart, the line between them)
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(0)
            bl.addWidget(_Hairline())
            bl.addWidget(row)
            row.box = box
            self.lay.addWidget(box)
        else:
            self.lay.addWidget(row)
        self._rows.append(row)
        row.title = t
        return row

    def field(self, name: str, control: QWidget, words: str | None = None, width: int = 132) -> QWidget:
        # (the label column: the recording's controls start 144 after it)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(12)
        lb = label(name, 13, 400, TEXT_2)
        lb.setFixedWidth(width)
        h.addWidget(lb, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        if words:
            h.addWidget(label(words, 12.5, 400, TEXT_2, wrap=True), 1)
        else:
            h.addStretch(1)
        self.lay.addWidget(row)
        return row

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(EDGE), 1))
        p.setBrush(QColor(CARD_UP if self.up else CARD))
        p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
        p.end()


class _Hairline(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(1)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(DIVIDER))
        p.end()


class Segmented(_Keyed, QWidget):
    """Bottom / Left / Right, Week / Month / All: one choice of a few, the chosen one lit. changed(value) when the owner picks."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, name: str, parent=None):
        super().__init__(parent)
        self.options, self.value = options, value
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        f = QFontMetricsF(font(13, 600))
        self._widths = [f.horizontalAdvance(t) + 28 for _v, t in options]
        self.setFixedWidth(int(sum(self._widths)) + 6)

    def set_value(self, value: str):
        if value != self.value:
            self.value = value
            self.update()

    def _pick(self, i: int):
        v = self.options[i][0]
        if v != self.value:
            self.value = v
            self.update()
            self.changed.emit(v)

    def mousePressEvent(self, e):
        x = 3.0
        for i, w in enumerate(self._widths):
            if x <= e.position().x() < x + w:
                self._pick(i)
                return
            x += w

    def keyPressEvent(self, e):
        i = [v for v, _t in self.options].index(self.value) if self.value in [v for v, _t in self.options] else 0
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._pick(max(0, i - 1))
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._pick(min(len(self.options) - 1, i + 1))
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(SEG))
        p.drawRoundedRect(r, 8, 8)
        x = 3.0
        p.setFont(font(13, 600))
        for (v, t), w in zip(self.options, self._widths):
            seg = QRectF(x, 3, w, r.height() - 5)
            if v == self.value:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(SEG_ON))
                p.drawRoundedRect(seg, 6, 6)
            p.setPen(QColor(TEXT if v == self.value else TEXT_2))
            p.drawText(seg, Qt.AlignmentFlag.AlignCenter, t)
            x += w
        self._ring(p, r, 8)
        p.end()


def field(placeholder: str = "", name: str | None = None, secret: bool = False, width: int | None = None) -> QLineEdit:
    """A text field (Qt's own, in the style sheet's colours). secret hides what is typed; a saved secret is never put back in it."""
    f = QLineEdit()
    f.setPlaceholderText(placeholder)
    f.setAccessibleName(name or placeholder)
    f.setFixedHeight(28)  # (the recording's fields: 28 tall)
    if secret:
        f.setEchoMode(QLineEdit.EchoMode.Password)
    if width:
        f.setFixedWidth(width)
    return f


class Dropdown(QComboBox):
    """Qt's own dropdown with the recording's chevron at its right (a style sheet draws none without an image file)."""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_icon(p, "down", QRectF(self.width() - 23, (self.height() - 12) / 2, 12, 12), TEXT_2 if self.isEnabled() else TEXT_OFF, 2.2)
        p.end()


def dropdown(options: list[tuple[str, str]], value: str | None, name: str, width: int | None = None) -> QComboBox:
    """A dropdown (Qt's own): options are (value, words); value() is currentData()."""
    c = Dropdown()
    c.setAccessibleName(name)
    for v, t in options:
        c.addItem(t, v)
    if value is not None:
        i = c.findData(value)
        if i >= 0:
            c.setCurrentIndex(i)
    c.setFixedHeight(32)
    if width:
        c.setFixedWidth(width)
    else:  # as wide as its longest choice, also when Core fills it later (the recording's compact "Right Ctrl ⌄")
        c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        c.setMinimumWidth(DROPDOWN_W[0])
        c.setMaximumWidth(DROPDOWN_W[1])
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    return c


def slider(value: int, name: str, width: int = 176) -> QSlider:
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(0, 100)
    s.setValue(value)
    s.setAccessibleName(name)
    s.setFixedWidth(width)
    s.setCursor(Qt.CursorShape.PointingHandCursor)
    return s


class Notice(QFrame):
    """A line set apart: amber (something to know before it works), red (it failed: what and how to recover), green (done)."""
    TONES = {"amber": ("#2a2210", "#4a3a18", "#e8c070"), "red": (RED_BG, "#5a2028", RED_TEXT), "green": ("#0f2a1a", "#1d4a2e", GREEN_TEXT),
             "plain": ("#16191c", FIELD_EDGE, TEXT_2)}

    def __init__(self, text: str = "", tone: str = "amber", parent=None):
        super().__init__(parent)
        self.tone = tone
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        self.text_label = label(text, 13, 400, self.TONES[tone][2], wrap=True, select=True)
        lay.addWidget(self.text_label)
        self.setVisible(bool(text))

    def say(self, text: str, tone: str | None = None):
        if tone:
            self.tone = tone
            self.text_label.setStyleSheet(f"color: {self.TONES[tone][2]}; background: transparent;")
        self.text_label.setText(text)
        self.setVisible(bool(text))
        self.update()

    def paintEvent(self, _e):
        bg, edge, _fg = self.TONES[self.tone]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(edge), 1))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.end()


class Empty(QWidget):
    """An honest empty state: Monday's pet asleep and what will show here, and how to make it."""

    def __init__(self, text: str, mood: str = "sleepy", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 24, 0, 24)
        lay.setSpacing(10)
        self.pet = Pet(64, pal("white"), mood=mood, name="Monday, resting")
        lay.addWidget(self.pet, 0, Qt.AlignmentFlag.AlignHCenter)
        self.text_label = label(text, 13.5, 400, TEXT_2, wrap=True)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.text_label)

    def say(self, text: str, mood: str = "sleepy"):
        self.text_label.setText(text)
        self.pet.mood(mood)


def starfield(size: QSize, ratio: float, tint, seed: int, strength: float = 1.0) -> QPixmap:
    """The recording's banner ground: near-black, the tint glowing in from the right (strongest at the top right), small stars thicker
    towards the light, a faint edge in the tint. Drawn once per size by whoever shows it (a banner, a glowing card)."""
    tint = QColor(tint)
    pix = QPixmap(int(size.width() * ratio), int(size.height() * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0, 0, size.width(), size.height()).adjusted(0.5, 0.5, -0.5, -0.5)
    clip = QPainterPath()
    clip.addRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
    p.setClipPath(clip)
    p.fillRect(r, QColor("#020202"))
    g = QLinearGradient(QPointF(r.width() * 0.30, 0), QPointF(r.width(), 0))
    g.setColorAt(0.0, alpha(tint, 0.0))
    g.setColorAt(0.55, alpha(tint, 0.45 * strength))
    g.setColorAt(1.0, alpha(tint, 1.0 * strength))
    p.fillRect(r, g)
    glow = QRadialGradient(QPointF(r.width() * 0.92, r.height() * 0.15), max(r.width() * 0.35, r.height()))
    glow.setColorAt(0.0, alpha(tint.lighter(150), 0.55 * strength))
    glow.setColorAt(1.0, alpha(tint, 0.0))
    p.fillRect(r, glow)
    floor = QLinearGradient(QPointF(0, 0), QPointF(0, r.height()))  # the tint dims towards the floor (the recording's: ~70 % at the bottom)
    floor.setColorAt(0.0, alpha("#020202", 0.0))
    floor.setColorAt(1.0, alpha("#020202", 0.32))
    p.fillRect(r, floor)
    rnd = random.Random(seed)
    star_tint = tint.lighter(260)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(int(r.width() * r.height() / 2600)):
        x, y = rnd.uniform(4, r.width() - 4), rnd.uniform(4, r.height() - 4)
        c = QColor(255, 255, 255) if rnd.random() < 0.6 else QColor(star_tint)
        c.setAlpha(int(rnd.uniform(40, 200) * (0.55 + 0.45 * x / r.width())))
        p.setBrush(c)
        rad = rnd.choice((0.6, 0.7, 0.8, 1.0, 1.3, 1.6))
        p.drawEllipse(QPointF(x, y), rad, rad)
    p.setClipping(False)
    p.setPen(QPen(alpha(mix(tint, "#ffffff", 0.12), 0.35), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
    p.end()
    return pix


class GlowCard(QFrame):
    """A card on the banner's ground (Home's: the recording's "Dictate anywhere" and "The Notetaker" glow warm with stars)."""

    def __init__(self, tint: str = "warm", strength: float = 0.8, pad: int = 22, parent=None):
        super().__init__(parent)
        self.tint, self.strength, self._bg = TINTS.get(tint, tint), strength, None
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(pad, pad - 2, pad, pad - 2)
        self.lay.setSpacing(6)

    def resizeEvent(self, e):
        self._bg = None
        super().resizeEvent(e)

    def paintEvent(self, _e):
        if self._bg is None or self._bg.size() != self.size() * self.devicePixelRatioF():
            self._bg = starfield(self.size(), self.devicePixelRatioF(), self.tint, self.width() * 7 + self.height(), self.strength)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg)
        p.end()


class Hero(QWidget):
    """A page's banner (the recording's): its title and words on black, stars, its tint glowing in from the right, and Monday's
    pals on its floor. The background is drawn once per size; the pals bob, blink and wander only while it shows, motion is allowed
    and the owner's pet isn't set to Still (PAL_MOTION)."""
    PAL = 50  # a pal's box (its body ~34 x 28: the recording's are ~28 x 22, a white one larger)

    def __init__(self, title: str, words: str, tint: str = "warm", pals=("white", "orange", "pink"), action: QWidget | None = None,
                 seed: int | None = None, parent=None):
        super().__init__(parent)
        self.tint = QColor(TINTS.get(tint, tint))
        self.setFixedHeight(HERO_H)
        self.setMinimumWidth(480)
        self._bg: QPixmap | None = None
        self._seed = seed if seed is not None else sum(map(ord, title))
        self.title_label = label(title, 28, 700)
        self.words_label = label(words, 14, 400, "#c4c7cc")
        self.title_label.setAccessibleName(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 26)
        lay.setSpacing(2)
        lay.addWidget(self.title_label)
        lay.addWidget(self.words_label)
        lay.addStretch(1)
        self.action_row = QHBoxLayout()
        if action is not None:
            self.action_row.addWidget(action)
        self.action_row.addStretch(1)
        lay.addLayout(self.action_row)
        self.pals: list[dict] = []
        self.set_pals(pals)
        self._t = QTimer(self)
        self._t.setInterval(int(1000 / PAL_FPS))
        self._t.timeout.connect(self._step)
        self._last = time.monotonic()

    def set_pals(self, pals):
        """The pals on the floor: colours (PALS names or #hex). A page whose pals stand for real things (an agent each, an integration
        each) sets them from Core; the same colour keeps its place."""
        old = {p["color"]: p for p in self.pals}
        pals = list(pals)[:6]
        rnd = random.Random(self._seed + len(pals))
        out = []
        for i, c in enumerate(pals):  # spread evenly over the banner's right part; one that was there keeps its own blink
            color = QColor(PALS.get(c, c)).name().upper()
            p_ = old.pop(color, None)
            if p_ is None:
                p_ = {"color": color, "art": pal(color, idle="bob", intensity=0.8), "phase": rnd.uniform(0, 6.28)}
                p_["art"].blink_at = time.monotonic() + rnd.uniform(0.5, 4.0)
            x = 0.64 + 0.105 * i if len(pals) <= 3 else 0.5 + (0.45 / (len(pals) - 1)) * i
            p_.update(x=min(0.95, x + rnd.uniform(-0.01, 0.01)), to=None)
            out.append(p_)
        self.pals = out
        self.update()

    def _moving(self) -> bool:
        return bool(self.pals) and PAL_MOTION and not reduced_motion() and self.isVisible()

    def showEvent(self, e):
        self._last = time.monotonic()
        if self._moving():
            self._t.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._t.stop()
        super().hideEvent(e)

    def resizeEvent(self, e):
        self._bg = None
        super().resizeEvent(e)

    def _step(self):
        if not self._moving():
            self._t.stop()
            self.update()
            return
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        rnd = random.Random()
        lo, hi = 0.5, 0.95
        for i, pal_ in enumerate(self.pals):
            if pal_["to"] is None:
                if rnd.random() < dt * 0.25:  # now and then one sets off somewhere near
                    pal_["to"] = max(lo, min(hi, pal_["x"] + rnd.uniform(-0.16, 0.16)))
            else:
                step = 0.045 * dt
                gap = pal_["to"] - pal_["x"]
                if abs(gap) <= step:
                    pal_["x"], pal_["to"] = pal_["to"], None
                else:
                    pal_["x"] += math.copysign(step, gap)
            pal_["art"].look = ((2.6 if pal_["to"] > pal_["x"] else -2.6) if pal_["to"] is not None else 0.0, 0.0)
        self.update()

    def _background(self) -> QPixmap:
        if self._bg is None or self._bg.size() != self.size() * self.devicePixelRatioF():
            self._bg = starfield(self.size(), self.devicePixelRatioF(), self.tint, self._seed)
        return self._bg

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._background())
        moving = self._moving()
        now = time.monotonic()
        s = self.PAL
        for pal_ in self.pals:
            pal_["art"].motion = moving
            x = pal_["x"] * self.width() - s / 2
            pal_["art"].draw(p, QRectF(x, self.height() - s - 2, s, s), now if moving else 1.0, "idle")
        p.end()


class Page(QWidget):
    """One page of the window: its banner, then its cards, scrolled by the window."""

    def __init__(self, hero: Hero | None = None, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 24)
        self.lay.setSpacing(18)
        self.hero = hero
        if hero is not None:
            self.lay.addWidget(hero)

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.lay.addWidget(w, stretch)
        return w

    def section(self, text: str, right: QWidget | None = None):
        row = QHBoxLayout()
        row.setContentsMargins(2, 8, 0, 0)
        row.addWidget(_section(text))
        row.addStretch(1)
        if right is not None:
            row.addWidget(right)
        self.lay.addLayout(row)


def confirm(parent: QWidget | None, title: str, text: str, action: str = "OK", danger: bool = False) -> bool:
    """Ask before something that can't be undone (Monday's own confirm: Cancel is the default)."""
    return confirm_dialog(parent, title, text, action, danger).exec() == QDialog.DialogCode.Accepted


def confirm_dialog(parent: QWidget | None, title: str, text: str, action: str = "OK", danger: bool = False) -> QDialog:
    """confirm's question, made and placed: asked from a window that stays over the others (the notch's Quit Monday?), it stays over them
    too and opens just under that window's edge; it opened under the notch, hidden by it (the owner, 2026-10-26)."""
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.setModal(True)
    d.setMinimumWidth(380)
    d.setStyleSheet(f"QDialog {{ background: {CARD}; }}")
    lay = QVBoxLayout(d)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(10)
    lay.addWidget(label(title, 15, 700))
    lay.addWidget(label(text, 13, 400, TEXT_2, wrap=True))
    row = QHBoxLayout()
    row.addStretch(1)
    no = Button("Cancel", "secondary")
    yes = Button(action, "danger" if danger else "primary")
    no.clicked.connect(d.reject)
    yes.clicked.connect(d.accept)
    row.addWidget(no)
    row.addWidget(yes)
    lay.addLayout(row)
    no.setFocus()
    host = parent.window() if parent is not None else None
    if host is not None and host.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
        d.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        d.adjustSize()
        g, scr = host.frameGeometry(), (host.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        d.move(max(scr.left(), min(g.center().x() - d.width() // 2, scr.right() - d.width())), min(g.bottom() + 12, scr.bottom() - d.height()))
    return d


# -- the application -------------------------------------------------------------------------------------------------------
STYLE = f"""
QToolTip {{ background: {FIELD}; color: {TEXT}; border: 1px solid {FIELD_EDGE}; padding: 4px 7px; border-radius: 6px; }}
QPlainTextEdit, QTextEdit {{ background: {FIELD}; color: {TEXT}; border: 1px solid {FIELD_EDGE}; border-radius: 8px;
    padding: 5px 10px; selection-background-color: {ORANGE}; selection-color: {ON_ORANGE}; }}
QLineEdit {{ background: {INPUT}; color: {TEXT}; border: 1px solid {FIELD_EDGE}; border-radius: 7px;
    padding: 3px 10px; selection-background-color: {ORANGE}; selection-color: {ON_ORANGE}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid #4a4f55; }}
QLineEdit:disabled {{ color: {TEXT_OFF}; }}
QComboBox {{ background: {FIELD}; color: {TEXT}; border: 1px solid {FIELD_EDGE}; border-radius: 8px; padding: 4px 30px 4px 12px; }}
QComboBox:hover {{ border: 1px solid #3a4046; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: #171a1d; color: {TEXT}; border: 1px solid {FIELD_EDGE}; outline: 0; padding: 4px;
    selection-background-color: #2b2f34; selection-color: {TEXT}; }}
QScrollBar:vertical {{ background: #2c2c2c; width: 10px; margin: 4px 2px 4px 0; border-radius: 5px; }}
QScrollBar::handle:vertical {{ background: #a0a0a0; border-radius: 5px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #c4c4c4; }}
QScrollBar:horizontal {{ background: #2c2c2c; height: 10px; margin: 0 4px 2px 4px; border-radius: 5px; }}
QScrollBar::handle:horizontal {{ background: #a0a0a0; border-radius: 5px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMenu {{ background: #15181b; color: {TEXT}; border: 1px solid {FIELD_EDGE}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 18px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {BUTTON}; }}
QMenu::item:disabled {{ color: {TEXT_OFF}; }}
QMenu::separator {{ height: 1px; background: {DIVIDER}; margin: 4px 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: #3a3d41; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #d9dadc; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def palette() -> QPalette:
    pal = QPalette()
    for role, c in ((QPalette.ColorRole.Window, PANEL), (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Base, FIELD),
                    (QPalette.ColorRole.AlternateBase, CARD), (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, BUTTON),
                    (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ORANGE),
                    (QPalette.ColorRole.HighlightedText, ON_ORANGE), (QPalette.ColorRole.PlaceholderText, TEXT_3),
                    (QPalette.ColorRole.ToolTipBase, FIELD), (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.Link, BLUE)):
        pal.setColor(role, QColor(c))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TEXT_OFF))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_OFF))
    return pal


def make_app(argv=None) -> QApplication:
    """The one QApplication: Fusion (the same controls on every OS), dark (Windows and macOS draw dark title bars for it), the
    platform's UI face, the style sheet above, Monday's icon."""
    app = QApplication.instance()
    if app is not None:
        return app
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(list(argv) if argv else sys.argv[:1])
    app.setApplicationName("Monday")
    app.setApplicationDisplayName("Monday")
    app.setOrganizationName("Monday")
    app.setDesktopFileName("monday")
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:  # Qt before 6.8
        pass
    app.setPalette(palette())
    app.setFont(font(13))
    app.setStyleSheet(STYLE)
    png = os.path.join(APP, "static", "monday-256.png")
    if os.path.isfile(png):
        app.setWindowIcon(QIcon(png))
    return app


def named(root: QWidget) -> list[str]:
    """Every control under root that a screen reader couldn't name (Monday's rule: each control has one)."""
    from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QScrollBar
    kinds = (QAbstractButton, QLineEdit, QComboBox, QAbstractSlider, QPlainTextEdit, QAbstractSpinBox, Segmented)
    bad = []
    for w in root.findChildren(QWidget):  # a scroll area's own bars are part of it, not controls of their own
        if isinstance(w, kinds) and not isinstance(w, QScrollBar) and not (w.accessibleName() or getattr(w, "text", lambda: "")()):
            bad.append(f"{type(w).__name__} {w.objectName() or hex(id(w))}")
    return bad


def render(widget: QWidget, path: str, size: QSize | None = None) -> QPixmap:
    """The widget drawn into a PNG without it showing on screen (the real platform's fonts; it is shown off-screen)."""
    if size is not None:
        widget.resize(size)
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    for _ in range(3):
        QApplication.processEvents()
    pix = widget.grab()
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not pix.save(path):
            raise OSError(f"couldn't write {path}")
    return pix


# -- self-check ------------------------------------------------------------------------------------------------------------
PET_MOODS = ("idle", "working", "listening", "hearing", "speaking", "waiting", "happy", "eating", "flat", "sad", "sleepy", "dizzy",
             "offline")


def filled(w: QWidget, color: str) -> QWidget:
    """A plain widget painted one colour (a palette fill, so it doesn't cascade into its children as a style sheet would)."""
    w.setAutoFillBackground(True)
    pal_ = w.palette()
    pal_.setColor(QPalette.ColorRole.Window, QColor(color))
    w.setPalette(pal_)
    return w


def _sheet() -> QWidget:
    root = filled(QWidget(), PANEL)
    v = QVBoxLayout(root)
    v.setContentsMargins(24, 24, 24, 24)
    v.setSpacing(16)
    v.addWidget(Hero("Hello", "Say what you want; Monday does it on this computer.", "warm",
                     action=Button("Ask Monday", "primary", dot=True)))
    rail = filled(QWidget(), RAIL)
    rail.setFixedWidth(RAIL_W)
    rv = QVBoxLayout(rail)
    rv.setContentsMargins(12, 16, 12, 16)
    rv.setSpacing(3)
    rv.addWidget(_section("Monday"))
    for ic, t in (("home", "Home"), ("chat", "Chat"), ("stats", "Statistics"), ("keyboard", "Shortcuts")):
        n = NavItem(ic, t)
        rv.addWidget(n)
        if t == "Home":
            n.setChecked(True)
        if t == "Chat":
            n.set_badge("2")
    rv.addStretch(1)
    row = QHBoxLayout()
    row.addWidget(rail)
    card = Card("Shortcuts", "Work from any app. Click a shortcut to change it, then press the new keys.")
    card.row("Open the chat", KeyChip("Ctrl+Alt+Space", "Open the chat shortcut"), extra=Chip('Types "ă"', "amber"))
    t1, t2 = Toggle("A switch, on", True), Toggle("A switch, off", False)
    card.row("Hold a key to dictate anywhere", t1, "Hold Right Ctrl in any app, speak, let go.")
    card.row("Where the notch is", Segmented([("bottom", "Bottom"), ("left", "Left"), ("right", "Right")], "bottom", "Where"))
    card.row("Microphone", dropdown([("", "System default")], "", "Microphone", 220))
    card.field("API key", field("sk-ant-…", "API key", secret=True))
    card.field("Sound", slider(60, "Volume"))
    btns = QHBoxLayout()
    for k in ("primary", "secondary", "light", "ghost", "danger", "save"):
        btns.addWidget(Button(k.title(), k))
    btns.addWidget(IconButton("gear", "Settings"))
    btns.addWidget(IconButton("home", "Home", lit=True))
    btns.addWidget(t2)
    card.lay.addLayout(btns)
    chips = QHBoxLayout()
    for t in ("PDF", "Images", "Code", "Docs"):
        chips.addWidget(Chip(t))
    chips.addWidget(Chip("Anthropic", "lit", dot=RED))
    chips.addWidget(Chip("Google", "plain", dot=BLUE))
    for s in ("ok", "bad", "wait", "off"):
        chips.addWidget(Dot(s))
    chips.addStretch(1)
    card.lay.addLayout(chips)
    card.lay.addWidget(Notice("The clean-up uses your Claude key. Add it in Chat.", "amber"))
    card.lay.addWidget(Notice("Failed: Core refused the key (403). Open Monday's own window and try again.", "red"))
    row.addWidget(card, 1)
    v.addLayout(row)
    pets = QHBoxLayout()  # the owner's witch in every mood, then every other kind at rest
    for mood in PET_MOODS:
        pets.addWidget(Pet(72, PetArt({"kind": "witch"}), mood=mood, name=mood))
    v.addLayout(pets)
    kinds = QHBoxLayout()
    for kind in ("blob", "bot", "cat", "fox", "gem", "maus", "blu", "hop", "sunny", "lovi", "muse", "grok", "nova", "byte", "webby"):
        kinds.addWidget(Pet(72, PetArt({"kind": kind}), name=kind))
    v.addLayout(kinds)
    v.addWidget(Empty("Your conversations will show here. Say something to Monday."))
    return root


def _check():
    app = make_app(["qtkit"])
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
        ok = ok and bool(cond)

    for fg, bg, need, what in ((TEXT, CARD, 4.5, "text on a card"), (TEXT_2, CARD, 4.5, "descriptions on a card"),
                               (TEXT_3, PANEL, 4.5, "section labels on the panel"), (TEXT_2, CARD_UP, 4.5, "words on a stat card"),
                               (ON_ORANGE, ORANGE, 4.5, "words on the orange button"), (TEXT_NAV, RAIL, 4.5, "nav on the rail"),
                               (RED_TEXT, RED_BG, 4.5, "red words on red"), (AMBER, AMBER_BG, 4.5, "amber chip"),
                               (GREEN_TEXT, CARD_UP, 4.5, "green words"), ("#e8c070", "#2a2210", 4.5, "amber notice")):
        check(f"contrast: {what}", contrast(fg, bg) >= need, f"{contrast(fg, bg):.1f}:1")
    sheet = _sheet()
    check("every control named", not named(sheet), ", ".join(named(sheet)))
    pix = render(sheet, "", QSize(1200, 900))
    check("the sheet draws", not pix.isNull() and pix.width() >= 1200, f"{pix.width()}x{pix.height()}")
    t = Toggle("t")
    seen = []
    t.toggled.connect(seen.append)
    t.click()
    t.set_quiet(False)
    check("a switch: a click says so, Core's value doesn't", seen == [True] and not t.isChecked())
    s = Segmented([("a", "A"), ("b", "B")], "a", "s")
    got = []
    s.changed.connect(got.append)
    s._pick(1)
    s._pick(1)
    check("segmented: one change per new choice", got == ["b"])
    b = Button("Install…", "light")
    check("a button sizes to its words", b.sizeHint().width() > 60)
    host = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)  # (as the notch)
    host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    host.setGeometry(400, 0, 640, 200)
    host.show()
    q = confirm_dialog(host, "Quit Monday?", "Monday Core stops.", "Quit Monday", danger=True)
    check("a question asked from the notch (it stays over every window) stays over it too, just under its edge (Quit Monday? opened under the "
          "notch, 2026-10-26)", bool(q.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) and q.pos().y() > host.frameGeometry().bottom(),
          f"{q.pos().y()} / {host.frameGeometry().bottom()}")
    page = QWidget()  # (Monday's own window: an ordinary one)
    plain = confirm_dialog(page, "Delete it?", "For good.")
    check("…a question from Monday's own window stays as it was", not plain.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    for w in (q, host, plain, page):
        w.deleteLater()
    check("every icon renders", all(icon_renderer(n).isValid() for n in ICONS), ", ".join(n for n in ICONS if not icon_renderer(n).isValid()))
    fam, mono = _families()
    check("fonts resolved", bool(fam and mono), f"{fam} / {mono}")
    app.processEvents()
    return ok


if __name__ == "__main__":
    if "--shot" in sys.argv:
        make_app(["qtkit"])
        out = sys.argv[sys.argv.index("--shot") + 1]
        render(_sheet(), out, QSize(1200, 900))
        print(out)
    elif "--check" in sys.argv:
        sys.exit(0 if _check() else 1)
    else:
        print(__doc__)
