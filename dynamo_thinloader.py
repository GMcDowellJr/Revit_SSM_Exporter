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

# MUST be the repo root that contains: core/, geometry/, export/, processing/, revit/
REPO_DIR = r"C:\Users\gmcdowell\Documents\Revit_SSM_Exporter"

# Basic validation to catch path mistakes
expected = [
    os.path.join(REPO_DIR, "ssm_exporter_main.py"),
    os.path.join(REPO_DIR, "core"),
    os.path.join(REPO_DIR, "geometry"),
    os.path.join(REPO_DIR, "export"),
    os.path.join(REPO_DIR, "processing"),
    os.path.join(REPO_DIR, "revit"),
]
missing = [p for p in expected if not os.path.exists(p)]
if missing:
    OUT = {
        "error": "REPO_DIR does not look like the SSM repo root (missing expected paths).",
        "REPO_DIR": REPO_DIR,
        "missing": missing,
    }
else:
    # Clean up sys.path: remove old repo paths that might conflict
    # But preserve Python stdlib paths (don't touch system Python paths)
    paths_to_remove = []
    for p in sys.path:
        if not p:
            continue
        # Remove old SSM or Fingerprint repo paths, but keep everything else
        if any(marker in p for marker in ["Revit_SSM_Exporter", "Revit_Fingerprint", "SSM_Exporter_Run"]):
            paths_to_remove.append(p)

    for p in paths_to_remove:
        while p in sys.path:
            sys.path.remove(p)

    # Insert this repo at the FRONT so it's found first, but keep stdlib paths intact
    sys.path.insert(0, REPO_DIR)

    try:
        # ---- CPython 3: purge cached modules so edits on disk are picked up ----
        # Only purge the repo's packages to avoid destabilizing stdlib / Dynamo internals.
        prefixes = ("core", "geometry", "export", "processing", "revit", "ssm_exporter_main")

        for name in list(sys.modules.keys()):
            if name in prefixes or name.startswith("core.") or name.startswith("geometry.") or name.startswith("export.") or name.startswith("processing.") or name.startswith("revit."):
                sys.modules.pop(name, None)

        # Import the main exporter module
        exporter = importlib.import_module("ssm_exporter_main")

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
            "exporter_file": getattr(sys.modules.get("ssm_exporter_main", None), "__file__", None),
        }
