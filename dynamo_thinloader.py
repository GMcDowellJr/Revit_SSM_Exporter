"""
Dynamo thin loader for SSM_Exporter with module cache clearing.

This loader ensures code edits are picked up between runs without restarting Revit.
Place this in your Dynamo Python Script node.
"""
import sys
import os
import traceback
import importlib

sys.dont_write_bytecode = True

# MUST be the repo root that contains: core/, geometry/, export/
REPO_DIR = r"C:\Users\gmcdowell\Documents\Revit_SSM_Exporter"

# Basic validation to catch path mistakes
expected = [
    os.path.join(REPO_DIR, "SSM_Exporter_v4_A21.py"),
    os.path.join(REPO_DIR, "core"),
    os.path.join(REPO_DIR, "geometry"),
    os.path.join(REPO_DIR, "export"),
]
missing = [p for p in expected if not os.path.exists(p)]
if missing:
    OUT = {
        "error": "REPO_DIR does not look like the SSM repo root (missing expected paths).",
        "REPO_DIR": REPO_DIR,
        "missing": missing,
    }
else:
    # Ensure this repo wins import resolution
    if REPO_DIR in sys.path:
        sys.path.remove(REPO_DIR)
    sys.path.insert(0, REPO_DIR)

    try:
        # ---- CPython 3: purge cached modules so edits on disk are picked up ----
        # Only purge the repo's packages to avoid destabilizing stdlib / Dynamo internals.
        prefixes = ("core", "geometry", "export", "SSM_Exporter_v4_A21")

        for name in list(sys.modules.keys()):
            if name in prefixes or name.startswith("core.") or name.startswith("geometry.") or name.startswith("export."):
                sys.modules.pop(name, None)

        # Import the main exporter module
        exporter = importlib.import_module("SSM_Exporter_v4_A21")

        # Forward Dynamo inputs
        exporter.IN = IN

        # Execute and get output
        OUT = exporter._safe_main()

    except Exception as e:
        OUT = {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "REPO_DIR": REPO_DIR,
            "sys_path_head": sys.path[:8],
            "exporter_file": getattr(sys.modules.get("SSM_Exporter_v4_A21", None), "__file__", None),
        }
