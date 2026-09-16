"""Dynamo Python entry point for a prepared ``next_batch.json``.

Inputs:
    IN[0] batch path
    IN[1] manifest root            (optional; see below)
    IN[2] validation-only flag     (optional bool)
    IN[3] artifact root            (optional; where job output actually lands)

``IN[3]`` matters whenever a batch uses relative ``output_directory`` values, as
a checked-in campaign must -- an absolute path cannot be committed without
being wrong on every other machine. Without it those paths resolve against the
directory holding the batch file, which for a campaign inside the repository
means capture artifacts are written into the checkout. A Stage A TIFF can reach
several hundred megabytes, so that is not a small mistake.

``IN[1]`` decides only where the run manifest goes, and resume reads prior runs
from it: pass the SAME value every time, or leave it null every time. A
manifest root that changes between runs looks like a campaign that has never
run, and everything is captured again.

The returned summary never contains TIFF analysis. Probe transactions remain
entirely probe-owned.
"""
from __future__ import absolute_import

import os
import sys


def _bootstrap_repository(batch_path):
    """Find the checkout before importing package modules in a pasted node."""
    seeds = (os.path.dirname(os.path.abspath(str(batch_path))), os.getcwd(),
             os.environ.get("REVIT_SSM_EXPORTER_ROOT"), os.environ.get("VOP_REPO_ROOT"))
    for seed in seeds:
        if not seed:
            continue
        current = os.path.abspath(os.path.expanduser(str(seed)))
        for _ in range(8):
            marker = os.path.join(current, "tests", "dynamo", "revit_batch_executor.py")
            if os.path.isfile(marker):
                if current not in sys.path:
                    sys.path.insert(0, current)
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    raise RuntimeError("Could not locate the Revit_SSM_Exporter checkout. Set REVIT_SSM_EXPORTER_ROOT.")


def dynamo_main(inputs):
    path = inputs[0]
    _bootstrap_repository(path)
    from tests.dynamo.revit_batch_executor import execute_batch
    from tests.dynamo.revit_probe_registry import build_registry

    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Persistence import DocumentManager
    clr.AddReference("RevitAPI")
    from Autodesk.Revit.DB import FilteredElementCollector, View

    doc = DocumentManager.Instance.CurrentDBDocument
    manifest_root = inputs[1] if len(inputs) > 1 and inputs[1] else None
    validation_only = bool(inputs[2]) if len(inputs) > 2 else False
    artifact_root = inputs[3] if len(inputs) > 3 and inputs[3] else None
    environment = {"revit_version": doc.Application.VersionNumber,
                   "revit_build": doc.Application.VersionBuild,
                   "dynamo_host": "Dynamo CPython3"}
    views = lambda current: FilteredElementCollector(current).OfClass(View).ToElements()
    return execute_batch(path, doc, build_registry(doc), views, manifest_root,
                         validation_only, environment, is_view=lambda value: isinstance(value, View),
                         artifact_root=artifact_root)


if "IN" in globals():
    OUT = dynamo_main(IN)  # noqa: F821
