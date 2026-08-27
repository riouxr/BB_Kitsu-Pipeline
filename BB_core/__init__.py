"""BB Kitsu pipeline shared core.

DCC-agnostic pipeline logic: naming, paths, versioning, Kitsu access and
credential storage. Every integration - the Blender add-on, the Nuke plugin,
the Resolve script and the standalone browser - imports this package rather
than carrying its own copy, so a fix to the naming scheme or the upload path
lands everywhere at once.

Nothing in here may import ``bpy``, ``nuke``, ``PySide`` or anything else that
only exists inside one host application. The only hard dependency is
``requests``, which Blender bundles and every other host already has.
"""

from .config import Config, load
from .context import ShotContext
from .kitsu import AuthError, KitsuClient, KitsuError

__version__ = "0.6.1"

__all__ = [
    "Config",
    "load",
    "ShotContext",
    "KitsuClient",
    "KitsuError",
    "AuthError",
    "__version__",
]
