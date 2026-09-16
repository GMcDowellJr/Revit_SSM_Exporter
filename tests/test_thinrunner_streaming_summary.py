"""
Unit tests for the pure-Python helper functions in
vop_interwoven/thinrunner_streaming.py (Stage A sidecar summarization and
batch output relocation).

thinrunner_streaming.py is a Dynamo Python Script node body: everything
below its "# RUN PIPELINE" marker is a top-level script that references
Dynamo's IN/OUT globals and calls Revit APIs directly, so the module as a
whole cannot be imported normally outside Dynamo. Its helper functions
(defined above that marker) have no such dependency, so this loads just
that prefix -- source up to (not including) the RUN PIPELINE marker -- via
exec() into an isolated namespace and tests the real functions directly,
rather than re-implementing their logic here.
"""
import os

import pytest

_THINRUNNER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "vop_interwoven", "thinrunner_streaming.py"
)
_RUN_PIPELINE_MARKER = "# RUN PIPELINE\n# ============================================================================"


def _load_thinrunner_helpers():
    with open(_THINRUNNER_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    idx = src.index(_RUN_PIPELINE_MARKER)
    defs_src = src[:idx]
    ns = {}
    exec(compile(defs_src, _THINRUNNER_PATH, "exec"), ns)
    return ns


@pytest.fixture(scope="module")
def helpers():
    return _load_thinrunner_helpers()


# --- _describe_suppression ---------------------------------------------------

def test_describe_suppression_false_means_suppressed(helpers):
    # applied_show_shadows/applied_smooth_edges == False means the property
    # WAS set to False, i.e. suppression succeeded -- must read as "yes",
    # not be echoed as the literal "False" (which reads backwards).
    assert helpers["_describe_suppression"](False) == "yes"


def test_describe_suppression_unchanged_means_no(helpers):
    # Still the right reading for applied_show_shadows, whose writer skips
    # the mutation when shadows are genuinely already off.
    assert helpers["_describe_suppression"]("unchanged") == (
        "no (already off, or unsupported on this Revit host)")


def test_describe_suppression_read_failed_is_unknown_not_no(helpers):
    # Equality, not `"no" in result.lower()`: "unknown" contains "no".
    assert helpers["_describe_suppression"]("read_failed") == (
        "unknown (state could not be read)")


def test_summarize_normalizes_legacy_unchanged_for_smooth_edges_only(helpers, tmp_path):
    """F5/G4: a legacy sidecar must not read as a confirmed AA-off capture,
    while the same literal on shadows keeps its own (correct) meaning."""
    import json
    sidecar = tmp_path / "legacy.json"
    sidecar.write_text(json.dumps({
        "applied_show_shadows": "unchanged",
        "applied_smooth_edges": "unchanged",
    }))
    lines = [ln.strip() for ln in helpers["_summarize_stage_a_sidecar"](str(sidecar))]
    assert "Smooth edges suppressed: unknown (state could not be read)" in lines
    assert "Shadows suppressed: no (already off, or unsupported on this Revit host)" in lines


def test_shadows_unchanged_prints_as_already_off_beside_a_clean_aa_read(helpers, tmp_path):
    """H4: the pairing that must NOT be normalised -- shadows genuinely off,
    AA genuinely confirmed off. Each field keeps its own meaning."""
    import json
    sidecar = tmp_path / "shadows_off.json"
    sidecar.write_text(json.dumps({
        "applied_show_shadows": "unchanged",
        "applied_smooth_edges": False,
    }))
    lines = [ln.strip() for ln in helpers["_summarize_stage_a_sidecar"](str(sidecar))]
    assert "Shadows suppressed: no (already off, or unsupported on this Revit host)" in lines
    assert "Smooth edges suppressed: yes" in lines


def test_describe_suppression_unchanged_failed_means_no(helpers):
    assert helpers["_describe_suppression"]("unchanged (failed)") == (
        "no (suppression attempt failed)")


# --- _summarize_stage_a_sidecar ---------------------------------------------

def test_summarize_stage_a_sidecar_happy_path(helpers, tmp_path):
    import json
    sidecar = tmp_path / "view.json"
    sidecar.write_text(json.dumps({
        "applied_show_shadows": False,
        "applied_smooth_edges": False,
        "link_category_color_map": {"Furniture": [10, 20, 30], "Casework": [40, 50, 60]},
        "near_face_w_map": {"host": {"1": {}, "2": {}}, "link": {"3:4": {}}},
        "paint_failures": 0,
    }))

    raw_lines = helpers["_summarize_stage_a_sidecar"](str(sidecar))
    text = "\n".join(raw_lines)
    lines = [ln.strip() for ln in raw_lines]

    assert "Shadows suppressed: yes" in lines
    assert "Smooth edges suppressed: yes" in lines
    assert "LINK categories colored: 2" in lines
    assert "Near-face-W collected: host=2 link=1" in lines
    # No paint failures -- line omitted entirely.
    assert "paint failures" not in text.lower()


def test_summarize_stage_a_sidecar_missing_file(helpers):
    lines = helpers["_summarize_stage_a_sidecar"]("/nonexistent/path/view.json")
    assert any("not found" in line for line in lines)


def test_summarize_stage_a_sidecar_malformed_json(helpers, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    lines = helpers["_summarize_stage_a_sidecar"](str(bad))
    assert any("unreadable" in line for line in lines)


# --- _relocate_batch_stage_a_outputs ----------------------------------------

def test_relocate_batch_stage_a_outputs_moves_files_and_rewrites_paths(helpers, tmp_path):
    batch_output_dir = tmp_path / "_batch_tmp_1"
    output_dir = tmp_path / "final"
    stage_a_dir = batch_output_dir / "color_id_buffer"
    stage_a_dir.mkdir(parents=True)

    tiff = stage_a_dir / "View1_100.tiff"
    sidecar = stage_a_dir / "View1_100.json"
    tiff.write_bytes(b"FAKE_TIFF")
    sidecar.write_text("{}")

    view_summaries = [
        {
            "view_id": 100,
            "view_name": "View1",
            "success": True,
            "stage": "color_id_buffer_stage_a",
            "tiff_path": str(tiff),
            "sidecar_path": str(sidecar),
        },
        # A non-Stage-A entry must be left untouched.
        {"view_id": 200, "view_name": "View2", "success": True, "width": 10, "height": 10},
    ]

    helpers["_relocate_batch_stage_a_outputs"](str(batch_output_dir), str(output_dir), view_summaries)

    final_tiff = output_dir / "color_id_buffer" / "View1_100.tiff"
    final_sidecar = output_dir / "color_id_buffer" / "View1_100.json"
    assert final_tiff.exists()
    assert final_sidecar.exists()
    assert not tiff.exists()
    assert not sidecar.exists()

    assert view_summaries[0]["tiff_path"] == str(final_tiff)
    assert view_summaries[0]["sidecar_path"] == str(final_sidecar)
    # Non-Stage-A entry unchanged.
    assert "tiff_path" not in view_summaries[1]


def test_relocate_batch_stage_a_outputs_noop_when_no_stage_a_dir(helpers, tmp_path):
    batch_output_dir = tmp_path / "_batch_tmp_2"
    batch_output_dir.mkdir()
    output_dir = tmp_path / "final2"

    # Must not raise when Stage A produced nothing for this batch.
    helpers["_relocate_batch_stage_a_outputs"](str(batch_output_dir), str(output_dir), [])
    assert not (output_dir / "color_id_buffer").exists()
