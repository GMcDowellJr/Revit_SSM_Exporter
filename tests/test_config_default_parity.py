"""Config has TWO construction paths with independently written defaults --
``Config(...)``'s signature and ``Config.from_dict()``'s per-key ``d.get(key,
<literal>)`` -- plus docstrings that state a third copy in prose. Nothing made
the three agree, and they drifted.

``include_dwg_imports`` was the visible instance: False in the constructor,
True in from_dict, "default: True" in both docstrings. Whether a DWG import
was collected and painted at all therefore depended on which path built the
Config. It is now True everywhere (Greg, 2026-09-21: the documented value
wins).

Testing that ONE field would prove nothing about the drift, and testing each
path against its own literal would prove nothing at all -- so this COMPOSES
the two paths across every field to_dict() reports, per CLAUDE.md's "a
quantity computed in two places, never composed".

Five other fields still disagree. They are enumerated below rather than
skipped: the assertion is EXACT, so a seventh divergence fails here, and so
does fixing one of the five without updating the list. A quarantine that
merely tolerated "some divergence" would have let include_dwg_imports sit
there forever, which is precisely what happened.
"""
import inspect

import pytest

from vop_interwoven import config as config_module
from vop_interwoven.config import Config


# (field, Config() value, Config.from_dict({}) value) for every field where
# the two construction paths still disagree. Each is a real defect awaiting a
# decision on WHICH value is correct -- not a tolerated difference. Removing an
# entry is how a fix is recorded.
KNOWN_DEFAULT_DIVERGENCES = {
    "anno_crop_margin_in": (0.0, 0.5),
    "anno_expand_cap_cells": (0, 4),
    "bounds_buffer_in": (0.0, 0.5),
    "export_perf_csv": (True, False),
    "perf_subtimings": (True, False),
}

_ABSENT = "<absent>"


def _divergences():
    from_ctor = Config().to_dict()
    from_empty_dict = Config.from_dict({}).to_dict()
    fields = set(from_ctor) | set(from_empty_dict)
    return {
        name: (from_ctor.get(name, _ABSENT), from_empty_dict.get(name, _ABSENT))
        for name in fields
        if from_ctor.get(name, _ABSENT) != from_empty_dict.get(name, _ABSENT)
    }


def test_include_dwg_imports_agrees_across_both_construction_paths():
    """The field this file exists for."""
    assert Config().include_dwg_imports is True
    assert Config.from_dict({}).include_dwg_imports is True


def test_include_dwg_imports_docstrings_state_the_value_they_implement():
    """The docstrings are a THIRD copy of the default, in prose, and prose
    cannot fail -- so it is asserted.

    Cited by its own words rather than by line number, per CLAUDE.md's rule
    about citations that rot: a line number moves silently when anything is
    inserted above it.
    """
    source = inspect.getsource(config_module)
    claims = source.count(
        "include_dwg_imports: Include elements from DWG/DXF imports (default: True)"
    ) + source.count(
        "include_dwg_imports (bool): Include elements from DWG/DXF imports (default: True)"
    )
    assert claims == 2, (
        "expected both include_dwg_imports docstrings to claim 'default: True'; "
        "found {0}. If the default changed, the prose has to change with it."
        .format(claims)
    )
    assert Config().include_dwg_imports is True


def test_the_two_construction_paths_have_exactly_the_known_divergences():
    """Composes the paths across EVERY field, not just the one just fixed.

    Exact, in both directions: a new divergence fails, and so does a fixed one
    still listed. That is what keeps the list honest as the five get decided.
    """
    actual = _divergences()

    unexpected = {k: v for k, v in actual.items() if k not in KNOWN_DEFAULT_DIVERGENCES}
    assert not unexpected, (
        "new default divergence between Config(...) and Config.from_dict({{}}): {0}. "
        "The two paths write their defaults independently; make them agree, or add "
        "the field here with the decision recorded.".format(unexpected)
    )

    resolved = {k: KNOWN_DEFAULT_DIVERGENCES[k] for k in KNOWN_DEFAULT_DIVERGENCES
                if k not in actual}
    assert not resolved, (
        "these fields no longer diverge and must be removed from "
        "KNOWN_DEFAULT_DIVERGENCES: {0}".format(sorted(resolved))
    )

    assert actual == KNOWN_DEFAULT_DIVERGENCES


@pytest.mark.parametrize("field", sorted(KNOWN_DEFAULT_DIVERGENCES))
def test_known_divergence_still_has_the_recorded_values(field):
    """Pins the VALUES too, so a drifted default inside an already-known
    divergence is not absorbed by the field simply still being listed."""
    assert _divergences()[field] == KNOWN_DEFAULT_DIVERGENCES[field]


def test_include_dwg_imports_is_not_in_the_known_divergences():
    """The control. Without it, this file would keep passing if the fix were
    reverted and the field simply added to the list above."""
    assert "include_dwg_imports" not in KNOWN_DEFAULT_DIVERGENCES


def test_an_explicit_value_still_beats_both_defaults():
    """The defaults agreeing must not come from the field being ignored."""
    assert Config(include_dwg_imports=False).include_dwg_imports is False
    assert Config.from_dict({"include_dwg_imports": False}).include_dwg_imports is False


def test_round_trip_preserves_an_explicitly_disabled_flag():
    """to_dict() always writes the key, so a round trip must never let
    from_dict's default reassert itself over an explicit False."""
    cfg = Config(include_dwg_imports=False)
    assert Config.from_dict(cfg.to_dict()).include_dwg_imports is False
