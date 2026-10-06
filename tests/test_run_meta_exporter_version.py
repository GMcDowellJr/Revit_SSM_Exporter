"""run_meta.json's exporter_version is a version, not the package name.

It used to be the constant "vop_interwoven", which every run carried
whatever code produced it. These tests assert the FILE write_run_meta
writes (CLAUDE.md, defect class 4), not only the record it returns.
"""
import json
import re

import vop_interwoven
from vop_interwoven import run_meta
from vop_interwoven.config import Config

VERSION = re.compile(r"^\d+\.\d+\.\d+([.+-][0-9A-Za-z.]+)?$")


def _written(tmp_path):
    meta = run_meta.build_run_meta(Config(), None, "20261006T120000", "2025-04-05",
                                   [1], "abcd1234", run_dir=str(tmp_path))
    path = run_meta.write_run_meta(meta, str(tmp_path))
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_exporter_version_is_the_package_version(tmp_path):
    on_disk = _written(tmp_path)
    assert on_disk["exporter_version"] == vop_interwoven.__version__
    assert VERSION.match(on_disk["exporter_version"]), on_disk["exporter_version"]
    assert on_disk["exporter_version"] != on_disk["exporter"]


def test_exporter_name_is_kept_as_its_own_key(tmp_path):
    on_disk = _written(tmp_path)
    assert on_disk["exporter"] == "vop_interwoven" == run_meta.EXPORTER_NAME


def test_one_version_string_in_the_package():
    """run_meta reads __version__ rather than restating it, so a bump of the
    package version is the only edit a release needs."""
    assert run_meta.EXPORTER_VERSION is vop_interwoven.__version__
