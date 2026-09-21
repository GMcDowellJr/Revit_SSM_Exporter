"""Pins a KNOWN GAP: the view cache signature does not cover the annotation frame.

This file asserts what is TRUE TODAY, not what ought to be. The gap is
documented in pipeline._view_signature and deliberately left unpatched --
cache policy is deferred until timing data exists. Pinning it executably
means the boundary moves loudly: whoever closes it sees these tests fail and
updates them, rather than the prose quietly going stale the way a comment
that states an invariant nothing asserts always does.

THE MECHANISM, which is the part worth binding. The signature's element
fingerprints come from a collector filtered by
collection_policy.should_include_element. Its step 4 admits
CategoryType.Model and excludes every other category type. The raster's
bounds, meanwhile, are the ANNOTATION-expanded frame, and Stage A step 2
made that frame the quantity the capture is sized from. Those two facts
together are the gap; either one alone is unremarkable, which is why both
are asserted here.
"""
import pytest

from vop_interwoven.revit.collection_policy import should_include_element

from tests.stage_a_capture_fakes import (
    FakeCategory,
    FakeDoc,
    FakeElement,
    install_fake_revit_db,
)

ANNOTATION_CATEGORIES = [
    ("Text Notes", 900),
    ("Dimensions", 901),
    ("Generic Annotations", 902),
]


def _include(name, cat_id, cat_type):
    with install_fake_revit_db():
        doc = FakeDoc(elements=[], link_instances=[], categories=[])
        cat = FakeCategory(name, cat_id, cat_type=cat_type)
        el = FakeElement(5000 + cat_id, cat)
        return should_include_element(elem=el, doc=doc, source_type="HOST", stats=None)


@pytest.mark.parametrize("name,cat_id", ANNOTATION_CATEGORIES,
                         ids=[n.replace(" ", "_") for n, _ in ANNOTATION_CATEGORIES])
def test_annotation_elements_do_not_reach_the_signature(name, cat_id):
    """The gap's mechanism. These are the categories that drive the
    annotation frame, and none of them is fingerprinted, so moving one
    changes the frame without changing the cache key."""
    include, reason, _cname = _include(name, cat_id, "Annotation")
    assert include is False
    assert reason == "non_model_category"


def test_model_elements_do_reach_it():
    """Control. Without this the test above would pass against a policy that
    excluded everything, which would be a different defect entirely and not
    the one this file is about."""
    include, reason, _cname = _include("Walls", 10, "Model")
    assert include is True
    assert reason == "included"


def test_the_annotation_frame_really_is_what_the_capture_is_sized_from():
    """The other half of the gap. If the capture stopped being sized from the
    annotation-expanded frame, the exclusion above would be harmless and this
    file would be pinning nothing.

    Asserted against the frame the capture actually reports, through the same
    fixture the step 2 gate uses, rather than by reading the sizing code.
    """
    from tests.test_capture_frame_is_not_recentred import (  # noqa: E402
        ANNO_BOUNDS, _bounds_result,
    )
    r = _bounds_result()
    u = r["anno_bounds_uncapped_uv"]
    assert u is not None
    # The frame the capture uses is the ANNOTATION extent, not the model one.
    assert u.xmax == pytest.approx(ANNO_BOUNDS.xmax)
    assert u.ymax == pytest.approx(ANNO_BOUNDS.ymax)


def test_the_signature_does_cover_crop_scale_and_config():
    """States the gap's REAL boundary, so it is not read as wider than it is.

    An earlier write-up of this finding said the signature was
    "element-fingerprint based, no bounds", which undersold it: a re-cropped
    or re-scaled view does invalidate. Only the annotation-driven part of the
    frame escapes.
    """
    import inspect

    from vop_interwoven import pipeline

    src = inspect.getsource(pipeline._view_signature)
    for key in ('"crop"', '"scale"', '"cfg_sha1"', '"view_mode"'):
        assert key in src, key
    # ... and no bounds rectangle is among them.
    assert '"bounds' not in src
