"""Capture the vetted-colorable category whitelist from a live Revit session.

Run this INSIDE Dynamo (a Python Script node) against any open document. It
prints the exact ``VETTED_COLORABLE_CATEGORY_IDS`` literal block to paste over
the placeholder in ``vop_interwoven/color_id_buffer.py``.

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
Primary categories only, as ElementId INTEGERS with the display name written
beside each as a comment.

Ids rather than names because ``Category.Name`` is localized: a whitelist
captured on an English Revit and keyed on names matches nothing on a French or
German one, so every category would be judged uncolorable and every LINK
category would lose its filter. A built-in category's id is its
BuiltInCategory enum value -- a negative integer, identical across locales and
Revit versions. The name comment keeps the frozen block readable in review
without being what the code matches on.

Subcategories are dropped: a subcategory resolves through
``doc.Settings.Categories`` with a non-None ``Parent``, and Stage A's LINK
category filters operate on primary categories.

Non-built-in ids are reported separately and NOT emitted. A built-in category
carries a negative id that means the same thing in every document; a
non-negative one is document-local and would be meaningless -- or worse,
silently wrong -- pasted into a constant that other documents are matched
against.

Usage in Dynamo
---------------
    import sys
    sys.path.append(r"C:\\path\\to\\Revit_SSM_Exporter")
    from tools.capture_vetted_colorable_categories import capture
    OUT = capture(DocumentManager.Instance.CurrentDBDocument)

``OUT`` is ``(literal_block, entries)``: paste ``literal_block`` into
color_id_buffer.py and record the Revit version it came from in the commit
message, since the whitelist is version-frozen by design.
"""


def collect_filterable_primary_categories(doc):
    """Filterable primary categories as ``(category_id, display_name)`` pairs.

    Returns ``(entries, skipped)``: ``entries`` is sorted by display name, so
    the emitted block is stable and diffable; ``skipped`` is a list of
    ``(category_id, reason)`` for every filterable id that did not resolve to
    a usable primary built-in category, so a partial capture is never mistaken
    for a complete one.
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

    entries = {}
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
        if cid >= 0:
            # Document-local id: stable within this model, meaningless in any
            # other. Emitting it would put an entry in the frozen constant that
            # matches an unrelated category elsewhere, or nothing at all.
            skipped.append((cid, "not a built-in category (id is not negative): '{0}'".format(name)))
            continue
        entries[cid] = name
    ordered = sorted(entries.items(), key=lambda pair: (pair[1], pair[0]))
    return ordered, skipped


def format_literal_block(entries):
    """The exact source block to paste into color_id_buffer.py."""
    lines = ["VETTED_COLORABLE_CATEGORY_IDS = frozenset(("]
    for cat_id, name in entries:
        lines.append("    {0},  # {1}".format(int(cat_id), name))
    lines.append("))")
    return "\n".join(lines)


def capture(doc):
    """Collect, report and format in one call. Returns (literal_block, entries)."""
    entries, skipped = collect_filterable_primary_categories(doc)
    block = format_literal_block(entries)
    report = [
        "# captured {0} primary filterable built-in categories".format(len(entries)),
    ]
    if skipped:
        report.append(
            "# {0} filterable id(s) NOT emitted (subcategory, non-built-in, or "
            "unresolvable):".format(len(skipped))
        )
        for cid, reason in skipped:
            report.append("#   {0}: {1}".format(cid, reason))
    print("\n".join(report))
    print(block)
    return block, entries
