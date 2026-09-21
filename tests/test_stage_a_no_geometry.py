"""tools/check_stage_a_no_geometry.py, proven against known-positives.

Stage A captures AABBs and parameters, never geometry. The checker asserts
that; this asserts the checker. A rule nobody has falsified is a hope, and
this one returned a clean "PROVEN" while its first implementation was silently
cutting edges at aliased imports -- right answer, wrong method.

Every scenario runs the tool as a SUBPROCESS and asserts on its exit code,
because the exit code is the whole contract:

    0  clean      1  geometry reachable      2  could not decide

The control (the real tree, unmutated, exits 0) is not optional: without it
every scenario below would also pass against a checker that refused
everything, or one that flagged everything.
"""
import os
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO_ROOT, "tools", "check_stage_a_no_geometry.py")
PKG = os.path.join(REPO_ROOT, "vop_interwoven")


def _run(*args):
    return subprocess.run(
        [sys.executable, TOOL] + list(args),
        cwd=REPO_ROOT, capture_output=True, text=True,
    )


# --- the control, first -----------------------------------------------------

def test_the_real_tree_is_clean():
    """Stage A calls no geometry today. Everything else here is meaningless
    without this."""
    result = _run(PKG)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STAGE A GEOMETRY-FREE: PROVEN" in result.stdout


def test_the_real_tree_actually_contains_geometry_to_find():
    """Reachability is only a claim if the tree HAS geometry calls that the
    roots fail to reach. A tree with none would report clean for the wrong
    reason -- so the tool refuses that case, and this pins the population."""
    result = _run(PKG)
    assert "geometry-using functions in the tree: 18 (none reachable)" in result.stdout, (
        "the legacy geometry path's population changed; if it reached 0 the "
        "clean result would prove nothing and the tool would refuse"
    )


# --- known-positive: geometry injected into the real Stage A path -----------

@pytest.fixture
def mutable_tree(tmp_path):
    """A full copy of the package, so a mutation is the real call graph."""
    dest = tmp_path / "vop_interwoven"
    shutil.copytree(PKG, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def test_geometry_injected_into_the_stage_a_path_is_caught(mutable_tree):
    """The mutation that matters: production gains a get_Geometry call in a
    function the Stage A roots genuinely reach."""
    target = mutable_tree / "color_id_buffer.py"
    source = target.read_text()
    needle = "    host_out = {}\n"
    assert source.count(needle) == 1
    source = source.replace(
        needle,
        "    host_out = {}\n"
        "    _leak = doc.get_Geometry(None)  # injected by the test\n",
    )
    target.write_text(source)

    result = _run(str(mutable_tree))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "STAGE A GEOMETRY-FREE: VIOLATED" in result.stdout
    assert "_collect_near_face_w_data" in result.stdout
    assert "get_Geometry" in result.stdout


def test_geometry_reached_only_through_an_aliased_import_is_caught(mutable_tree):
    """The bug this tool was born with.

    ``from .revit.collection import expand_host_link_import_model_elements as
    _expand_elements`` cuts the edge for a walk that resolves callees by their
    local name. The first version of this checker did exactly that and still
    reported clean, because another path happened to reach the same module. A
    fixture where the ALIAS IS THE ONLY PATH is what discriminates.
    """
    (mutable_tree / "leaf_geom.py").write_text(
        "def leaf_that_tessellates(elem):\n"
        "    return elem.get_Geometry(None)\n"
    )
    target = mutable_tree / "color_id_buffer.py"
    source = target.read_text()
    needle = "    host_out = {}\n"
    source = source.replace(
        needle,
        "    from .leaf_geom import leaf_that_tessellates as _aliased\n"
        "    _aliased(doc)\n"
        + needle,
    )
    target.write_text(source)

    result = _run(str(mutable_tree))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "leaf_that_tessellates" in result.stdout


def test_geometry_outside_the_stage_a_path_is_not_flagged(mutable_tree):
    """Discrimination control. The legacy geometry path is full of
    get_Geometry and must stay legal -- a checker that flagged the whole tree
    would pass the two tests above for the wrong reason."""
    (mutable_tree / "unreached_geom.py").write_text(
        "def never_called_from_stage_a(elem):\n"
        "    return elem.get_Geometry(None)\n"
    )
    result = _run(str(mutable_tree))
    assert result.returncode == 0, result.stdout + result.stderr


# --- refusals ---------------------------------------------------------------

def test_a_file_that_cannot_be_parsed_is_refused_not_skipped(mutable_tree):
    """A file the walk cannot read is absent from the graph. Reporting PROVEN
    over a directory it failed to read is the exact failure the discarded-
    handler validator shipped with."""
    (mutable_tree / "broken.py").write_text("def oops(:\n    pass\n")

    result = _run(str(mutable_tree))
    assert result.returncode == 2, result.stdout
    assert "REFUSED" in result.stdout
    assert "NOT PROVEN" in result.stdout


def test_a_missing_root_is_refused(mutable_tree):
    """A root that does not resolve makes every claim about what it reaches
    vacuous -- and a typo'd root name is otherwise invisible."""
    result = _run(str(mutable_tree), "--root", "no_such_entry_point")
    assert result.returncode == 2, result.stdout
    assert "root function not found" in result.stdout


def test_a_nonexistent_scan_root_is_refused(tmp_path):
    """os.walk on a missing directory yields nothing and raises nothing, so a
    typo in a CI path would certify a scan of zero files."""
    result = _run(str(tmp_path / "does_not_exist"))
    assert result.returncode == 2, result.stdout
    assert "does not exist" in result.stdout


def test_an_empty_scan_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run(str(empty))
    assert result.returncode == 2, result.stdout
    assert "scanned 0 files" in result.stdout


def test_a_tree_with_no_geometry_at_all_is_refused(tmp_path):
    """Clean-because-there-is-nothing-to-find is not the same claim as
    clean-because-the-roots-avoid-it, and must not print PROVEN."""
    pkg = tmp_path / "tiny"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        "def export_color_id_buffer_view(doc):\n"
        "    return doc.get_BoundingBox(None)\n"
    )
    result = _run(str(pkg), "--root", "export_color_id_buffer_view")
    assert result.returncode == 2, result.stdout
    assert "no geometry calls at all" in result.stdout


def test_a_computed_callee_in_the_stage_a_path_is_refused(mutable_tree):
    """getattr(obj, name)() cannot be resolved. Refused, not assumed safe."""
    target = mutable_tree / "color_id_buffer.py"
    source = target.read_text()
    source = source.replace(
        "    host_out = {}\n",
        "    getattr(doc, 'get_Geometry')(None)\n    host_out = {}\n",
    )
    target.write_text(source)

    result = _run(str(mutable_tree))
    assert result.returncode == 2, result.stdout
    assert "computed callee" in result.stdout


def test_dotnet_generics_are_decided_not_refused(mutable_tree):
    """SCG.List[ElementId]() is a TYPE construction, not a dispatch on an
    element. The real tree has three, and refusing them would make the tool
    permanently undecidable on this repo -- which is how it first behaved."""
    result = _run(str(mutable_tree))
    assert result.returncode == 0, result.stdout
    assert "computed callee" not in result.stdout


def test_bbox_and_parameter_calls_are_never_flagged(tmp_path):
    """The positive half of the contract: what Stage A IS allowed to do."""
    pkg = tmp_path / "ok"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        "def export_color_id_buffer_view(doc, elem):\n"
        "    bb = elem.get_BoundingBox(None)\n"
        "    p = elem.get_Parameter(None)\n"
        "    return bb, p, elem.Category.Name, elem.LookupParameter('x')\n"
        "def unreached(elem):\n"
        "    return elem.get_Geometry(None)\n"
    )
    result = _run(str(pkg), "--root", "export_color_id_buffer_view")
    assert result.returncode == 0, result.stdout
    assert "PROVEN" in result.stdout
