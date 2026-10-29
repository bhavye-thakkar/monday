"""Monday Core. An installed Monday (installer/build.py) brings its own Python packages in lib\\, beside its app folder: on the path for every
part of it (python -m core, core.desktop, the self-checks self-repair runs). The repo has none, and this does nothing there."""
import os

_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if os.path.isdir(_LIB):
    import site
    site.addsitedir(_LIB)  # (after the PC's own: theirs first, if it has them; its .pth files too, pywin32's)
