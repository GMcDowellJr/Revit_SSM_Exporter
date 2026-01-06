import pathlib
import pytest

from vop_interwoven.core.source_identity import make_source_identity


def test_make_source_identity_accepts_known_types():
    assert make_source_identity("HOST", "HOST")["source_type"] == "HOST"
    assert make_source_identity("LINK", "RVT_LINK:abc:123", "MyLink")["source_type"] == "LINK"
    assert make_source_identity("DWG", "DWG_IMPORT:x:42")["source_type"] == "DWG"


@pytest.mark.parametrize("bad_type", ["", None, "host", "RVT_LINK", "DWG_IMPORT"])
def test_make_source_identity_rejects_bad_type(bad_type):
    with pytest.raises(ValueError):
        make_source_identity(bad_type, "X")


def test_make_source_identity_requires_nonempty_id():
    with pytest.raises(ValueError):
        make_source_identity("HOST", None)
    with pytest.raises(ValueError):
        make_source_identity("HOST", "")


def test_pipeline_does_not_parse_doc_key_for_source_type():
    # This is intentionally a "tripwire" regression guard.
    p = pathlib.Path(__file__).resolve().parents[1] / "vop_interwoven" / "pipeline.py"
    txt = p.read_text(encoding="utf-8")
    assert "_extract_source_type(" not in txt
