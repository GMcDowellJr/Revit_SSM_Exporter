"""Capture the vetted-colorable category whitelist from a live Revit session.

Run this INSIDE Dynamo (a Python Script node) against any open document. It
prints the exact ``VETTED_COLORABLE_CATEGORY_NAMES`` literal block to paste
over the placeholder in ``vop_interwoven/color_id_buffer.py``.

Why a capture tool rather than a hand-written list
--------------------------------------------------
``ParameterFilterUtilities.GetAllFilterableCategories()`` is the same source
Revit's own Visibility/Graphics > Filters dialog reads from, so it -- not a
screenshot of that dialog, and not a prose description of it -- is
authoritative. The list runs to roughly 150-200 entries; transcribing it by
hand would risk silent single-entry errors that surface only as a category
quietly losing its color in a capture, which is precisely the failure mode
the whitelist exists to make impossible.

What it emits
-------------
PRIMARY category names only. ``GetAllFilterableCategories()`` returns category
ids; a subcategory resolves through ``doc.Settings.Categories`` with a
non-None ``Parent``, and those are dropped -- Stage A's LINK category filters
operate on primary categories.

Usage in Dynamo
---------------
    import sys
    sys.path.append(r"C:\\path\\to\\Revit_SSM_Exporter")
    from tools.capture_vetted_colorable_categories import capture
    OUT = capture(DocumentManager.Instance.CurrentDBDocument)

``OUT`` is ``(literal_block, names)``: paste ``literal_block`` into
color_id_buffer.py and record the Revit version it came from in the commit
message, since the whitelist is version-frozen by design.
"""


def collect_filterable_primary_category_names(doc):
    """Primary category names Revit reports as filterable, sorted.

    Returns ``(names, skipped)``: ``names`` is the sorted list of primary
    category names; ``skipped`` is a list of ``(category_id, reason)`` for
    every filterable id that did not resolve to a usable primary category, so
    a partial capture is never mistaken for a complete one.
    """
    from Autodesk.Revit.DB import ParameterFilterUtilities

    by_id = {}
    for cat in doc.Settings.Categories:
        try:
            by_id[int(cat.Id.IntegerValue)] = cat
        except Exception:
            continue
        for sub in cat.SubCategories:
            try:
                by_id[int(sub.Id.IntegerValue)] = sub
            except Exception:
                continue

    names = set()
    skipped = []
    for cat_id in ParameterFilterUtilities.GetAllFilterableCategories():
        try:
            cid = int(cat_id.IntegerValue)
        except Exception as ex:
            skipped.append((str(cat_id), "unreadable id: {0}".format(ex)))
            continue
        cat = by_id.get(cid)
        if cat is None:
            skipped.append((cid, "not resolvable in doc.Settings.Categories"))
            continue
        try:
            if cat.Parent is not None:
                skipped.append((cid, "subcategory of '{0}'".format(cat.Parent.Name)))
                continue
            name = cat.Name
        except Exception as ex:
            skipped.append((cid, "unreadable: {0}".format(ex)))
            continue
        if not name:
            skipped.append((cid, "empty name"))
            continue
        names.add(name)
    return sorted(names), skipped


def format_literal_block(names):
    """The exact source block to paste into color_id_buffer.py."""
    lines = ["VETTED_COLORABLE_CATEGORY_NAMES = frozenset(("]
    for name in names:
        lines.append('    "{0}",'.format(name.replace('\\', '\\\\').replace('"', '\\"')))
    lines.append("))")
    return "\n".join(lines)


def capture(doc):
    """Collect, report and format in one call. Returns (literal_block, names)."""
    names, skipped = collect_filterable_primary_category_names(doc)
    block = format_literal_block(names)
    report = [
        "# captured {0} primary filterable categories".format(len(names)),
    ]
    if skipped:
        report.append(
            "# {0} filterable id(s) skipped (subcategories or unresolvable):".format(len(skipped))
        )
        for cid, reason in skipped:
            report.append("#   {0}: {1}".format(cid, reason))
    print("\n".join(report))
    print(block)
    return block, names
