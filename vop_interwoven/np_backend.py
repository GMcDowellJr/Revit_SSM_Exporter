"""
vop_interwoven/np_backend.py

NumPy and Pillow availability detection with optional auto-install.
Import NUMPY_AVAILABLE, PILLOW_AVAILABLE, np, and Image from here.
All other modules in vop_interwoven must import numpy/PIL exclusively
through this module so the fallback is centralised.
"""

NUMPY_AVAILABLE = False
PILLOW_AVAILABLE = False
np = None
Image = None

try:
    import numpy as _np
    np = _np
    NUMPY_AVAILABLE = True
except ImportError:
    pass

try:
    from PIL import Image as _Image
    Image = _Image
    PILLOW_AVAILABLE = True
except ImportError:
    pass


def ensure_numpy(auto_install=False):
    """Return True if NumPy is importable.

    If auto_install=True and NumPy is absent, attempts pip install.
    Only set auto_install=True when Config.numpy_auto_install is True.
    Never call this from inside a hot path — call once at pipeline startup.
    """
    global NUMPY_AVAILABLE, np
    if NUMPY_AVAILABLE:
        return True
    if not auto_install:
        return False
    try:
        import subprocess
        import sys
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "numpy"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import numpy as _np
        np = _np
        NUMPY_AVAILABLE = True
        print("[VOP np_backend] NumPy installed successfully.")
        return True
    except Exception as e:
        print("[VOP np_backend] NumPy auto-install failed: {}".format(e))
        return False


def ensure_pillow(auto_install=False):
    """Return True if Pillow is importable.

    If auto_install=True and Pillow is absent, attempts pip install.
    Only set auto_install=True when Config.numpy_auto_install is True.
    Never call this from inside a hot path — call once at pipeline startup.
    """
    global PILLOW_AVAILABLE, Image
    if PILLOW_AVAILABLE:
        return True
    if not auto_install:
        return False
    try:
        import subprocess
        import sys
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "Pillow"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        from PIL import Image as _Image
        Image = _Image
        PILLOW_AVAILABLE = True
        print("[VOP np_backend] Pillow installed successfully.")
        return True
    except Exception as e:
        print("[VOP np_backend] Pillow auto-install failed: {}".format(e))
        return False
