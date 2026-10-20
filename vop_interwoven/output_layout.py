"""Where a run's files go and how long their names may be: ONE place.

Two layouts exist on disk and both are read:

* ``v1`` -- every run before this module: ``run_meta.json``,
  ``color_id_buffer/``, ``analysis_grid/``, ``<stem>_anno.registered.*``,
  ``grid_rollup.csv``, ``kinds_inventory.csv``.
* ``v2`` -- new runs: ``run_meta_<d>.json``, ``color_id/``, ``grid/``,
  ``<stem>_anno.reg.*``, ``grid_rollup_<d>.csv``, ``kinds_inventory_<d>.csv``,
  ``element_census_<d>.csv``, ``element_transitions_<d>.csv``.
  ``<d>`` is the run's file date (``file_date``): the value every other dated
  file of the run is named by (``views_core_<d>.csv``), so a run's files
  pulled together from several runs do not collide.

The element census and transitions are dated ``_<d>`` in BOTH layouts
(``census_names``, ``transitions_names``): ``analysis_grid/`` of a v1 run
holds ``element_census_<d>.csv`` beside an undated ``grid_rollup.csv``.

A run keeps the layout it was created in: a recapture into a v1 run writes v1
names, and the tools write into a run in its own layout, so no run mixes the
two. ``layout_of_run`` and ``layout_of_capture_dir`` decide; a folder that is
neither (a test's scratch folder, a copied capture) reads as v1, the names
every reader already knew.

PATH LENGTH (2026-10-19). Windows refuses a path of 260 characters or more
unless long paths are enabled, which cannot be assumed; Revit's ExportImage
then writes nothing and raises nothing. The 2025-07-02 rerun lost 24 views
that way in a run directory 159 characters deep. The variable part of every
path is the view's name in its files, so the name is CAPPED per view: as much
of it as fits beside the run's capture folder, the view id (which keeps the
name unique) and the longest suffix any writer appends (``STEM_SUFFIXES``).
A name that fits is unchanged. The full name is in the sidecar
(``view_name``). A view already captured into the run keeps its file name
(``existing_stem``), so a recapture replaces its files rather than writing a
second set. ``check_run_budget`` refuses a run whose capture folder leaves no
room even for the view id.

Standard library only, IronPython 2 compatible: the capture imports it.
"""

import os
import re

V1 = "v1"
V2 = "v2"
NEW_RUN_LAYOUT = V2

CAPTURE_DIR = {V2: "color_id", V1: "color_id_buffer"}
GRID_DIR = {V2: "grid", V1: "analysis_grid"}
# The registration tool's infix: <stem>_anno<infix>.tiff / .json.
REGISTERED_INFIX = {V2: ".reg", V1: ".registered"}

RUN_META_V1 = "run_meta.json"
RUN_META_PREFIX = "run_meta_"

# The longest path that is accepted: Windows MAX_PATH is 260 including the
# terminating NUL.
PATH_LIMIT = 259

# Every suffix any writer appends to a view's file stem, in each layout.
# tests/test_output_layout.py finds each literal suffix the tools append and
# fails when one is longer than these allow.
_TOOL_SUFFIXES = (".tiff", ".json", ".grid.json", ".grid.npz", ".grid.png",
                  ".kinds.json", ".kinds.npz", ".decoded.json", ".link_identity.json",
                  ".overlay.png", ".reencoded.json", ".analyzed.json")
STEM_SUFFIXES = {
    layout: tuple(s for base in _TOOL_SUFFIXES for s in (base, "_anno" + base))
    + ("_anno" + REGISTERED_INFIX[layout] + ".tiff",
       "_anno" + REGISTERED_INFIX[layout] + ".json")
    for layout in (V1, V2)}
# What the capture itself writes, which is all a batch folder ever holds.
CAPTURE_SUFFIXES = (".tiff", ".json", "_anno.tiff", "_anno.json")
# A batched run captures into <run>/_batch_tmp_<n>/<capture dir>/ and moves
# the files up; the name must fit there too.
BATCH_DIR_ALLOWANCE = len(os.sep + "_batch_tmp_999")
# The widest view id the run-start check assumes (ElementId is 64-bit).
MAX_VIEW_ID_DIGITS = 19


class PathBudgetExceeded(Exception):
    """A capture folder too deep for a view's files to fit under PATH_LIMIT."""


# --- layouts ------------------------------------------------------------------

def layout_of_capture_dir(path):
    """``v2`` for a ``color_id`` folder, else ``v1``."""
    return V2 if os.path.basename(os.path.normpath(str(path))) == CAPTURE_DIR[V2] else V1


def run_meta_candidates(run_dir):
    """Every run_meta file directly in ``run_dir``: ``run_meta.json`` and
    ``run_meta_<d>.json``, sorted."""
    try:
        names = os.listdir(str(run_dir))
    except (IOError, OSError):
        return []
    return sorted(os.path.join(str(run_dir), n) for n in names
                  if n == RUN_META_V1
                  or (n.startswith(RUN_META_PREFIX) and n.endswith(".json")))


def find_run_meta(run_dir):
    """``(path, None)`` for the run's one run_meta file; ``(None, reason)``
    when there is none or more than one -- never a guess between two."""
    found = run_meta_candidates(run_dir)
    if not found:
        return None, "no run_meta file in {0}".format(run_dir)
    if len(found) > 1:
        return None, "{0} run_meta files in {1}: {2}".format(
            len(found), run_dir, ", ".join(os.path.basename(p) for p in found))
    return found[0], None


def layout_of_run(run_dir):
    """The layout of an existing run, or None for a directory that holds no
    run yet. A v1 capture folder or ``run_meta.json`` makes it v1."""
    run_dir = str(run_dir)
    if (os.path.isdir(os.path.join(run_dir, CAPTURE_DIR[V1]))
            or os.path.isfile(os.path.join(run_dir, RUN_META_V1))):
        return V1
    if (os.path.isdir(os.path.join(run_dir, CAPTURE_DIR[V2]))
            or run_meta_candidates(run_dir)):
        return V2
    return None


def file_date(meta):
    """The ``<d>`` of a run's dated file names, from its run_meta: the tag
    when the run was given one, else its as-of date -- what
    ``run_meta.output_date_str`` named the run's views_core file by."""
    tag = (meta or {}).get("run_tag")
    return str(tag) if tag else (meta or {}).get("date")


def run_meta_name(layout, d):
    return RUN_META_V1 if layout == V1 else "{0}{1}.json".format(RUN_META_PREFIX, d)


def rollup_names(layout, d):
    """(csv, summary) of the grid roll-up."""
    if layout == V1:
        return "grid_rollup.csv", "grid_rollup.summary.json"
    return "grid_rollup_{0}.csv".format(d), "grid_rollup_{0}.summary.json".format(d)


def inventory_names(layout, d):
    """(csv, json) of the kinds inventory."""
    if layout == V1:
        return "kinds_inventory.csv", "kinds_inventory.json"
    return "kinds_inventory_{0}.csv".format(d), "kinds_inventory_{0}.json".format(d)


# The census and transitions files are dated in BOTH layouts: they are new in
# 2026-10, so no v1 reader knows an undated name. The undated names are what
# the census wrote before (unreleased), kept so it can adopt those files.
CENSUS_UNDATED = ("element_census.csv", "element_census.summary.json")
TRANSITIONS_UNDATED = ("element_transitions.csv", "element_transitions.summary.json")


def census_file_date(meta, run_dir):
    """The ``<d>`` of a run's census and transitions names: ``file_date``,
    else the ``<date>__`` prefix of the run folder's name, else "undated".
    The census writer and the runs index both name the files by it."""
    d = file_date(meta)
    if d:
        return str(d)
    parts = split_run_folder(os.path.basename(os.path.normpath(str(run_dir))))
    return parts[0] if parts else "undated"


def census_names(layout, d):
    """(csv, summary) of the per-run element census (tools/stage_a_element_census),
    ``element_census_<d>.*`` in either layout."""
    return "element_census_{0}.csv".format(d), "element_census_{0}.summary.json".format(d)


def transitions_names(layout, d):
    """(csv, summary) of a run's element transitions against its predecessor,
    ``element_transitions_<d>.*`` in either layout."""
    return ("element_transitions_{0}.csv".format(d),
            "element_transitions_{0}.summary.json".format(d))


def grid_dir_for_capture(capture_dir):
    """The analysis folder beside a capture folder, in its layout."""
    capture_dir = os.path.normpath(str(capture_dir))
    return os.path.join(os.path.dirname(capture_dir),
                        GRID_DIR[layout_of_capture_dir(capture_dir)])


def registered_paths(anno_sidecar_path):
    """``(tiff, json)`` the registration tool writes for an annotation
    sidecar, named in its capture folder's layout."""
    path = str(anno_sidecar_path)
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    infix = REGISTERED_INFIX[layout_of_capture_dir(folder)]
    return (os.path.join(folder, stem + infix + ".tiff"),
            os.path.join(folder, stem + infix + ".json"))


# --- view file names ------------------------------------------------------------

def safe_view_name(name):
    """The view name as it appears in file names (the rule every Stage A
    file name has used)."""
    return "".join(c if c.isalnum() or c in (" ", "-", "_") else "_"
                   for c in (name if name is not None else "view"))


def _reserve(layout):
    longest = max(len(s) for s in STEM_SUFFIXES[layout])
    return max(longest, BATCH_DIR_ALLOWANCE + max(len(s) for s in CAPTURE_SUFFIXES))


def name_room(capture_dir, view_id, layout=None):
    """How many characters of the view name fit in ``capture_dir`` beside
    ``_<view_id>`` and the longest suffix; negative when not even the id fits."""
    layout = layout or layout_of_capture_dir(capture_dir)
    tail = len("_{0}".format(view_id))
    return (PATH_LIMIT - len(os.path.abspath(str(capture_dir))) - len(os.sep)
            - tail - _reserve(layout))


def existing_stem(capture_dir, view_id):
    """The stem of the one model sidecar in ``capture_dir`` that carries
    ``view_id`` in its name, or None (none, or more than one)."""
    tail = "_{0}.json".format(view_id)
    try:
        names = os.listdir(str(capture_dir))
    except (IOError, OSError):
        return None
    found = [n for n in names if n.endswith(tail)]
    return found[0][:-len(".json")] if len(found) == 1 else None


def view_file_stem(view_name, view_id, capture_dir, layout=None):
    """``<name>_<view id>`` for a view's files in ``capture_dir`` (the run's
    FINAL capture folder): the stem the run already has for the view, else
    the view name capped to the room the folder leaves. Raises
    PathBudgetExceeded when not even the id fits, and when the stem the run
    already has would not fit with the longest suffix: an older run's name
    was never capped, and cutting it would leave a second set of files, so
    the view is refused rather than captured under either name."""
    room = name_room(capture_dir, view_id, layout)
    kept = existing_stem(capture_dir, view_id)
    if kept is not None:
        over = len(kept) - len("_{0}".format(view_id)) - room
        if over > 0:
            raise PathBudgetExceeded(
                "view {0}'s files in {1} are named {2!r}; with the longest suffix a "
                "tool appends that passes the {3}-character path limit by {4}, so the "
                "view is not captured again under it".format(
                    view_id, capture_dir, kept, PATH_LIMIT + 1, over))
        return kept
    if room < 0:
        raise PathBudgetExceeded(
            "the capture folder {0} ({1} characters) leaves no room for view {2}'s "
            "files under the {3}-character path limit (short by {4})".format(
                capture_dir, len(os.path.abspath(str(capture_dir))), view_id,
                PATH_LIMIT + 1, -room))
    return safe_view_name(view_name)[:room] + "_{0}".format(view_id)


def check_run_budget(run_dir, layout=NEW_RUN_LAYOUT):
    """At run start: the characters of view name the run's capture folder
    leaves for the widest view id. Raises PathBudgetExceeded when it leaves
    none -- every view would fail, so nothing is exported."""
    capture_dir = os.path.join(str(run_dir), CAPTURE_DIR[layout])
    room = name_room(capture_dir, "9" * MAX_VIEW_ID_DIGITS, layout)
    if room < 0:
        raise PathBudgetExceeded(
            "run directory {0} is {1} characters deep; its files would pass the "
            "{2}-character path limit even with no view name in them (short by "
            "{3}). Choose a shorter output folder.".format(
                run_dir, len(os.path.abspath(str(run_dir))), PATH_LIMIT + 1, -room))
    return room


def capture_dir_of(cfg, base_output_dir):
    """``(write_dir, final_dir, layout)`` for a capture: the folder the files
    are written into now (under the batch's output folder) and the run's
    final capture folder, which is what names are budgeted against. The run
    sets ``cfg._stage_a_layout`` and ``cfg._stage_a_run_dir`` (private
    attributes, outside Config.to_dict and the config hash); without them
    (a direct call, a test) the layout is v1 and the output folder is final."""
    layout = getattr(cfg, "_stage_a_layout", None) or V1
    run_dir = getattr(cfg, "_stage_a_run_dir", None) or base_output_dir
    name = CAPTURE_DIR[layout]
    return (os.path.join(str(base_output_dir), name), os.path.join(str(run_dir), name),
            layout)


_RUN_FOLDER = re.compile(r"^(\d{4}-\d{2}-\d{2})__(.+)$")


def split_run_folder(name):
    """``(as_of_date, run_id)`` from a run folder name, or None."""
    m = _RUN_FOLDER.match(str(name))
    return (m.group(1), m.group(2)) if m else None
