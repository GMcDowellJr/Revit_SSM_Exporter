"""Dynamo Python entry point for a prepared ``next_batch.json``.

Inputs: ``IN[0]`` batch path, optional ``IN[1]`` manifest root, and optional
boolean ``IN[2]`` validation-only flag. The returned summary never contains
TIFF analysis. Probe transactions remain entirely probe-owned.
"""
from __future__ import absolute_import

from tests.dynamo.revit_batch_executor import execute_batch
from tests.dynamo.revit_probe_registry import build_registry


def dynamo_main(inputs):
    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Persistence import DocumentManager
    clr.AddReference("RevitAPI")
    from Autodesk.Revit.DB import FilteredElementCollector, View

    doc = DocumentManager.Instance.CurrentDBDocument
    path = inputs[0]
    manifest_root = inputs[1] if len(inputs) > 1 and inputs[1] else None
    validation_only = bool(inputs[2]) if len(inputs) > 2 else False
    environment = {"revit_version": doc.Application.VersionNumber,
                   "revit_build": doc.Application.VersionBuild,
                   "dynamo_host": "Dynamo CPython3"}
    views = lambda current: FilteredElementCollector(current).OfClass(View).ToElements()
    return execute_batch(path, doc, build_registry(), views, manifest_root,
                         validation_only, environment)


if "IN" in globals():
    OUT = dynamo_main(IN)  # noqa: F821
