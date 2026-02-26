"""
VOP NumPy + Pillow Bootstrap
============================
Run once per machine (or per Dynamo Python home) to install the optional
performance libraries. The VOP pipeline runs correctly without them — this
only enables the faster code paths.

Usage (command line):
    python vop_interwoven/bootstrap.py

Usage (Dynamo Python node, CPython3 engine):
    exec(open(r"C:\\path\\to\\vop_interwoven\\bootstrap.py").read())

Usage (PyRevit, #! python3 script):
    exec(open(r"C:\\path\\to\\vop_interwoven\\bootstrap.py").read())

After running, restart Revit / Dynamo before using VOP.
"""

import sys
import subprocess


def _is_importable(package_import_name):
    try:
        __import__(package_import_name)
        return True
    except ImportError:
        return False


def _install(pip_package_name):
    print("[VOP Bootstrap] Installing {} ...".format(pip_package_name))
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pip_package_name],
        )
        print("[VOP Bootstrap] OK: {}".format(pip_package_name))
        return True
    except subprocess.CalledProcessError as e:
        print("[VOP Bootstrap] FAILED: {} — {}".format(pip_package_name, e))
        return False
    except Exception as e:
        print("[VOP Bootstrap] FAILED: {} — {}".format(pip_package_name, e))
        return False


def run():
    needed = []
    if not _is_importable("numpy"):
        needed.append(("numpy", "numpy"))
    if not _is_importable("PIL"):
        needed.append(("Pillow", "PIL"))

    if not needed:
        print("[VOP Bootstrap] NumPy and Pillow already installed. Nothing to do.")
        return True

    all_ok = True
    for pip_name, import_name in needed:
        ok = _install(pip_name)
        if not ok:
            all_ok = False

    if all_ok:
        print("[VOP Bootstrap] Done. Restart Revit / Dynamo before running VOP.")
    else:
        print("[VOP Bootstrap] Some installs failed. See errors above.")
        print("[VOP Bootstrap] VOP will still run correctly using the Python fallback path.")

    return all_ok


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
