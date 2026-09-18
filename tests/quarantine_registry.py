"""The tracked registry of quarantined tests. ONE entry per quarantined test.

A quarantined test is one that FAILS and whose failure is a known, recorded,
unresolved question -- not a flake, and not something a marker alone should
be allowed to express. A bare ``@pytest.mark.xfail`` in a test file is an
exception with no owner, no date and no statement of what it is waiting on;
that is what per-PR-body prose was doing before, and it does not survive
being read six months later.

HOW IT WORKS
------------
tests/conftest.py enforces a two-way binding at collection time:

  - a test marked ``@pytest.mark.quarantine`` with no entry here is a
    collection ERROR, not a skip;
  - an entry here naming no collected test is a collection ERROR too, so a
    renamed or deleted test cannot leave a stale entry behind;
  - each marked test runs as ``xfail(strict=True, raises=<the entry's
    "raises">)``. BOTH halves matter, in opposite directions:

      * ``strict`` turns an unexpected PASS into a failure, so the day the
        underlying defect is fixed the suite goes RED and names the entry to
        remove. A quarantine that quietly starts passing is how a quarantine
        becomes permanent.
      * ``raises`` turns an unexpected FAILURE MODE into a failure. Strict
        says nothing about HOW a test fails: without a type, a production
        regression that made a quarantined test raise TypeError would still
        be reported as the expected xfail and leave the suite green. The
        quarantine would have grown from "this one known defect" into "any
        defect at all" without anyone deciding to widen it.

    ``raises`` is therefore mandatory, and collection fails on an entry that
    omits it.

The suite is therefore green with these two present, and it is green for a
stated reason that is checked mechanically.

WHAT A QUARANTINE IS NOT
------------------------
It is not a verdict that the test is wrong. Both entries below assert a core
architecture principle from CLAUDE.md, and in both the PRODUCTION CODE is
what disagrees with them. Quarantining records that; it does not settle it.
"""

# key:    "<path relative to the repo root>::<test name>"
# raises: the ONE exception type this test is quarantined for. Anything else
#         it raises is a new failure and must not be absorbed here.
QUARANTINED = {
    "tests/test_occlusion_contract.py::"
    "test_rasterize_areal_loops_low_does_not_populate_rect_gate_cells": {
        "first_seen": "2026-09-17",
        "first_seen_at": "a9051f2",
        "raises": AssertionError,
        "reason": (
            "rasterize_areal_loops() populates _out_cells for an AREAL element "
            "whose geometry came back LOW confidence via aabb_fallback. The test "
            "asserts the contract in CLAUDE.md -- 'Confidence-based occlusion "
            "semantics: failed strategies do not degrade occlusion', and "
            "pipeline.py's own policy header, 'only AREAL elements with "
            "HIGH-confidence geometry may write w_occ'. w_occ itself is still "
            "clean (the test's last assertion would pass); what leaks is the "
            "scene occluder rect seed, which is the same authority by another "
            "route."
        ),
        "open_question": (
            "Whether _out_cells is occlusion authority at all. If it is, "
            "rasterize_areal_loops must gate it on confidence the way it gates "
            "w_occ. If it is not, the contract test is asserting a promise "
            "nobody made and the policy header needs to say so. Nothing in the "
            "repo currently settles which, and picking one to make the suite "
            "green would be deciding an occlusion-authority question by test "
            "maintenance."
        ),
        "not_a_flake": (
            "Deterministic. Synthetic raster, no Revit, no I/O, no ordering. "
            "Reproduces at a9051f2 with every later change stashed."
        ),
    },
    "tests/test_single_areal_extraction_contract.py::"
    "test_areal_path_extracts_geometry_once_before_raster_decompose": {
        "first_seen": "2026-09-17",
        "first_seen_at": "a9051f2",
        "raises": AssertionError,
        "reason": (
            "A static AST contract: the per-element loop in "
            "render_model_front_to_back() must contain exactly one "
            "decompose_to_rects() call. It contains two. The test's other "
            "assertions -- one extract_areal_geometry, one rasterize_areal_loops, "
            "decompose after rasterize, both on _elem_cells -- are not reached, "
            "so the second call's position and argument are unchecked as well as "
            "uncounted."
        ),
        "open_question": (
            "Whether the second decompose_to_rects() is a legitimate second "
            "path (a branch the single-extraction contract was never meant to "
            "cover) or the duplication the contract exists to forbid. Answering "
            "it means reading the loop, not relaxing the count: raising the "
            "assertion to 2 would retire the contract while appearing to keep it."
        ),
        "not_a_flake": (
            "Deterministic. Parses vop_interwoven/pipeline.py from disk; no "
            "Revit, no I/O beyond that read, no ordering. Reproduces at a9051f2 "
            "with every later change stashed."
        ),
    },
}
