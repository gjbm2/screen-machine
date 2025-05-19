from importlib import import_module
import os as _os

# Save original search path (outer dir)
_outer_path = list(globals().get('__path__', []))

# Forward all public symbols from the actual SamsungTVWS package that lives
# in samsungtvws/samsungtvws so that `import samsungtvws` works even when
# the outer directory shadows the inner package on sys.path.
_inner = import_module('samsungtvws.samsungtvws')

# Re-export main API
SamsungTVWS = _inner.SamsungTVWS  # type: ignore
SamsungTVShortcuts = getattr(_inner, 'SamsungTVShortcuts', None)
__version__ = getattr(_inner, '__version__', '0.0.0')

# Copy everything else
import sys as _sys
_sys.modules[__name__].__dict__.update(_inner.__dict__)

# Restore outer path component so subpackages like `samsungtvws.example` work
if '_outer_path' in locals():
    for p in _outer_path:
        if p not in __path__:
            __path__.append(p)

# ------------------------------------------------------------------
# Ensure example/web_interface directory is importable at top level so that
# `import async_art_gallery_web` and similar absolute imports inside the
# example code resolve.
# ------------------------------------------------------------------
_pkg_root = _os.path.abspath(_sys.modules[__name__].__path__[0])
_example_dir = _os.path.join(_pkg_root, 'example', 'web_interface')
if _os.path.isdir(_example_dir) and _example_dir not in _sys.path:
    _sys.path.insert(0, _example_dir) 