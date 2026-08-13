"""Dynamo Python entry point for a prepared ``next_batch.json``.

Inputs: ``IN[0]`` batch path, optional ``IN[1]`` manifest root, and optional
boolean ``IN[2]`` validation-only flag. The returned summary never contains
TIFF analysis. Probe transactions remain entirely probe-owned.
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
    environment = {"revit_version": doc.Application.VersionNumber,
                   "revit_build": doc.Application.VersionBuild,
                   "dynamo_host": "Dynamo CPython3"}
    views = lambda current: FilteredElementCollector(current).OfClass(View).ToElements()
    return execute_batch(path, doc, build_registry(doc), views, manifest_root,
                         validation_only, environment, is_view=lambda value: isinstance(value, View))


if "IN" in globals():
    OUT = dynamo_main(IN)  # noqa: F821
