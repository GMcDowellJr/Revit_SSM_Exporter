"""tools/npz_write.py: the analysis layer's npz bytes are a function of the
arrays, and are the bytes np.savez_compressed writes."""
import hashlib
import zipfile

import numpy as np

from tools import npz_write


def _arrays():
    rng = np.random.default_rng(3)
    return {"model_host": rng.integers(0, 9, (40, 30)).astype(np.int64),
            "inside_crop_a": rng.random((40, 30)) < 0.5,
            "occupancy": rng.integers(0, 4, (40, 30)).astype(np.uint8),
            "ec_key": np.arange(17, dtype=np.int32),
            "ec_px": np.zeros(0, dtype=np.int32)}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_bytes_are_numpys_and_np_load_reads_the_same_arrays(tmp_path):
    ours, theirs = tmp_path / "a.npz", tmp_path / "b.npz"
    npz_write.savez_compressed(ours, _arrays())
    np.savez_compressed(theirs, **_arrays())
    assert ours.read_bytes() == theirs.read_bytes()
    with np.load(str(ours)) as data:
        assert data.files == list(_arrays())
        for key, value in _arrays().items():
            assert data[key].dtype == value.dtype
            assert np.array_equal(data[key], value)


def test_two_writes_are_byte_identical_even_where_zipfile_would_stamp_the_clock(
        tmp_path, monkeypatch):
    """An interpreter whose ZipFile.open(name, "w") dates a member by the
    clock is simulated by giving ZipInfo a moving default. The writer passes
    its own date, so its bytes do not move; np.savez_compressed's do (the
    control: the simulation really does reach a name-only write)."""
    clock = iter(range(1, 100))
    original = zipfile.ZipInfo.__init__

    def stamping(self, filename="NoName", date_time=None):
        if date_time is None:
            date_time = (2026, 10, 22, 12, 0, 2 * next(clock))
        original(self, filename, date_time)

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", stamping)
    paths = [tmp_path / "{0}.npz".format(n) for n in range(4)]
    npz_write.savez_compressed(paths[0], _arrays())
    npz_write.savez_compressed(paths[1], _arrays())
    assert _sha(paths[0]) == _sha(paths[1])
    with zipfile.ZipFile(str(paths[0])) as z:
        assert set(i.date_time for i in z.infolist()) == {npz_write.FIXED_DATE_TIME}
    np.savez_compressed(paths[2], **_arrays())
    np.savez_compressed(paths[3], **_arrays())
    assert _sha(paths[2]) != _sha(paths[3])
