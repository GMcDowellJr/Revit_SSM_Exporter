"""ONE writer for the analysis layer's npz files, byte-deterministic by
construction.

``np.savez_compressed`` names each member by a string, and the member's zip
timestamp is then whatever ``zipfile.ZipFile.open(name, "w")`` gives it. On
CPython 3.11-3.13 that is ``ZipInfo``'s default, 1980-01-01 00:00:00 (read
from each interpreter's zipfile source, 2026-10-22), so the bytes already
depend on the arrays alone -- but only because of a stdlib default nothing
here asserted. This writer passes an explicit ``ZipInfo`` with that same
date, so the bytes are the ones ``np.savez_compressed`` writes on those
interpreters (tests/test_npz_write.py compares them) and stay so on any
interpreter that stamps the clock instead.

Same member names (``<key>.npy``), same ``.npy`` format
(``numpy.lib.format.write_array``), deflate, zip64 forced as numpy forces
it, members in the order given. The grid's ``npz_sha256`` and the kinds
record's are therefore functions of the arrays.

LEAF MODULE: standard library + numpy.
"""
from __future__ import annotations

import os
import zipfile

import numpy as np
from numpy.lib import format as npy_format

# ZipInfo's own default; the earliest date a zip can hold.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def savez_compressed(path, arrays):
    """Write ``arrays`` (a mapping, in its iteration order) to ``path`` as
    ``np.savez_compressed(path, **arrays)`` does, with every member dated
    FIXED_DATE_TIME. ``path`` is used as given (no ``.npz`` appended)."""
    with zipfile.ZipFile(os.fspath(path), mode="w", compression=zipfile.ZIP_DEFLATED,
                         allowZip64=True) as zipf:
        for key, value in arrays.items():
            info = zipfile.ZipInfo(key + ".npy", date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            with zipf.open(info, "w", force_zip64=True) as fid:
                npy_format.write_array(fid, np.asanyarray(value), allow_pickle=False)
