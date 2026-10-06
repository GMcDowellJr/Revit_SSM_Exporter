"""run_meta.json's exporter_version changes with the code, with nobody bumping it.

It used to be the constant "vop_interwoven" (the package name), so every run
carried the same value whatever code produced it. It is now
"<__version__>+src.<12 hex>", the hex being run_meta.source_fingerprint()
over the package's own .py files. These tests assert the FILE
write_run_meta writes (CLAUDE.md, defect class 4), and exercise the
fingerprint on a copy of the real package, so they bind to the production
function rather than to a reimplementation of it.
"""
import json
import os
import re
import shutil
from pathlib import Path

import pytest

import vop_interwoven
from vop_interwoven import run_meta
from vop_interwoven.config import Config

PACKAGE = Path(vop_interwoven.__file__).resolve().parent
VERSION = re.compile(r"^(?P<release>\d+\.\d+\.\d+)\+src\.(?P<src>[0-9a-f]{12})$")


@pytest.fixture
def copy(tmp_path):
    """A copy of the real package's .py files (no __pycache__)."""
    dst = tmp_path / "pkg"
    shutil.copytree(str(PACKAGE), str(dst),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst


def _sha(package_dir):
    fp = run_meta.source_fingerprint(str(package_dir))
    assert fp["state"] == "value", fp
    return fp["value"]["sha256"]


def _written(tmp_path):
    meta = run_meta.build_run_meta(Config(), None, "20261006T120000", "2025-04-05",
                                   [1], "abcd1234", run_dir=str(tmp_path))
    path = run_meta.write_run_meta(meta, str(tmp_path))
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_written_version_is_release_plus_source_fingerprint(tmp_path):
    on_disk = _written(tmp_path)
    m = VERSION.match(on_disk["exporter_version"])
    assert m, on_disk["exporter_version"]
    assert m.group("release") == vop_interwoven.__version__
    assert on_disk["exporter_source"]["state"] == "value"
    assert on_disk["exporter_source"]["value"]["sha256"].startswith(m.group("src"))
    assert on_disk["exporter_source"]["value"]["files"] == len(
        [p for p in PACKAGE.rglob("*") if p.suffix in run_meta.FINGERPRINT_SUFFIXES
         and "__pycache__" not in p.parts])
    assert on_disk["exporter"] == "vop_interwoven" == run_meta.EXPORTER_NAME


def test_copy_of_the_same_code_fingerprints_the_same(copy):
    """A deployed copy (no .git) gives the running package's fingerprint."""
    assert _sha(copy) == _sha(PACKAGE)


def test_crlf_checkout_fingerprints_the_same(copy):
    for p in copy.rglob("*.py"):
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert _sha(copy) == _sha(PACKAGE)


@pytest.mark.parametrize("change", ["edit", "rename", "add", "remove"])
def test_any_code_change_changes_the_version(copy, change):
    before = run_meta.exporter_version(str(copy))[0]
    target = copy / "run_meta.py"
    if change == "edit":
        target.write_text(target.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    elif change == "rename":
        target.rename(copy / "run_meta_renamed.py")
    elif change == "add":
        (copy / "new_module.py").write_text("X = 1\n", encoding="utf-8")
    else:
        (copy / "core" / "hull.py").unlink()
    after = run_meta.exporter_version(str(copy))[0]
    assert before != after
    assert VERSION.match(after)


def test_the_runtime_metrics_manifest_changes_it(copy):
    """Config reads this JSON by default to choose metrics and CSV columns."""
    manifest = copy / "metrics" / "manifest" / "metrics_manifest.v1.json"
    assert manifest.is_file()
    before = _sha(copy)
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert _sha(copy) != before


def test_an_unlistable_directory_is_reported_not_skipped(copy, monkeypatch):
    """os.walk drops a directory it cannot list unless told; the fingerprint
    must not then hash the readable subset as if it were the package."""
    real_scandir = os.scandir

    def failing_scandir(path="."):
        if os.path.basename(str(path)) == "core":
            raise PermissionError("denied: core")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)
    version, source = run_meta.exporter_version(str(copy))
    assert source["state"] == "unavailable" and "denied: core" in source["reason"]
    assert version == vop_interwoven.__version__


def test_docs_and_caches_do_not_change_it(copy):
    before = _sha(copy)
    (copy / "notes.md").write_text("doc change\n", encoding="utf-8")
    os.makedirs(str(copy / "__pycache__"), exist_ok=True)
    (copy / "__pycache__" / "x.py").write_text("ignored\n", encoding="utf-8")
    assert _sha(copy) == before


def test_unreadable_source_is_reported_not_skipped(copy, monkeypatch):
    real_open = open

    def failing_open(path, *a, **k):
        if str(path).endswith("hull.py"):
            raise PermissionError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", failing_open)
    version, source = run_meta.exporter_version(str(copy))
    assert source["state"] == "unavailable" and "PermissionError" in source["reason"]
    assert version == vop_interwoven.__version__          # release only, never a partial hash


def test_empty_package_dir_is_unavailable(tmp_path):
    version, source = run_meta.exporter_version(str(tmp_path))
    assert source["state"] == "unavailable" and version == vop_interwoven.__version__
