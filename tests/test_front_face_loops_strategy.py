# tests/test_front_face_loops_strategy.py

import types


def _import_config_and_silhouette():
    """
    Import using the real package first (pytest runs with repo root on sys.path),
    then fall back to flatter layouts if needed.
    """
    import importlib

    # Config
    Config = None
    for mod_path in ("vop_interwoven.config", "config"):
        try:
            m = importlib.import_module(mod_path)
            Config = getattr(m, "Config", None)
            if Config is not None:
                break
        except Exception:
            continue
    if Config is None:
        raise RuntimeError("Failed to import Config from vop_interwoven.config or config")

    # Silhouette module
    silhouette = None
    for mod_path in (
        "vop_interwoven.core.silhouette",
        "core.silhouette",
        "vop_interwoven.silhouette",
        "silhouette",
    ):
        try:
            silhouette = importlib.import_module(mod_path)
            break
        except Exception:
            continue
    if silhouette is None:
        raise RuntimeError("Failed to import silhouette module from known locations")

    return Config, silhouette


class _Id:
    def __init__(self, i: int):
        self.IntegerValue = int(i)


class _StubElem:
    def __init__(self, i: int = 101):
        self.Id = _Id(i)


class _StubView:
    def __init__(self, i: int = 201):
        self.Id = _Id(i)


class _StubViewBasis:
    # get_element_silhouette() passes this through; our stub strategy doesn't use it
    forward = (0.0, 0.0, 1.0)


class _StubRaster:
    # get_element_silhouette() passes this into _determine_uv_mode; we monkeypatch that
    pass


def test_config_areal_prefers_front_face_loops():
    """
    Acceptance boundary:
      AREAL strategies must try front-facing planar face loops BEFORE silhouette_edges.
    """
    Config, _ = _import_config_and_silhouette()
    cfg = Config()

    strategies = cfg.get_silhouette_strategies("AREAL")
    assert strategies, "Expected non-empty strategy list for AREAL"

    assert strategies[0] in ("front_face_loops", "planar_face_loops"), (
        "Expected AREAL primary strategy to be a planar front-face loop strategy "
        f"(got {strategies[0]!r}; full={strategies!r})"
    )


def test_get_element_silhouette_uses_front_face_loops_first_and_preserves_holes(monkeypatch):
    """
    Verifies dispatch order + passthrough semantics without requiring Revit geometry:
    - Force uv_mode='AREAL'
    - Provide a cfg strategy list that starts with 'front_face_loops'
    - Stub _front_face_loops_silhouette to return an outer loop + a hole
    - Stub _silhouette_edges to hard-fail if called
    - Assert returned loops keep is_hole flags and gain strategy tag
    """
    _, silhouette = _import_config_and_silhouette()

    # Force AREAL without touching bbox/geometry
    monkeypatch.setattr(silhouette, "_determine_uv_mode", lambda *a, **k: "AREAL")

    calls = {"silhouette_edges": 0}

    # get_element_silhouette swallows strategy exceptions, so don't raise.
    # Record invocation instead and assert later.
    def _record_silhouette_edges(*a, **k):
        calls["silhouette_edges"] += 1
        return []

    monkeypatch.setattr(silhouette, "_silhouette_edges", _record_silhouette_edges)

    # Return an outer boundary + a hole; no 'strategy' key (get_element_silhouette should add it)
    stub_loops = [
        {"points": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)], "is_hole": False},
        {"points": [(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (3.0, 3.0), (2.0, 2.0)], "is_hole": True},
    ]

    # For this unit test, select the planar strategy deterministically based on
    # what the silhouette module actually implements.
    if hasattr(silhouette, "_planar_face_loops_silhouette"):
        planar_primary = "planar_face_loops"
        monkeypatch.setattr(
            silhouette,
            "_planar_face_loops_silhouette",
            lambda *a, **k: list(stub_loops),
        )
    else:
        planar_primary = "front_face_loops"
        monkeypatch.setattr(
            silhouette,
            "_front_face_loops_silhouette",
            lambda *a, **k: list(stub_loops),
        )

    cfg = types.SimpleNamespace(
        get_silhouette_strategies=lambda uv_mode: [
            planar_primary,
            "silhouette_edges",
            "obb",
            "bbox",
        ]
    )

    elem = _StubElem()
    view = _StubView()
    view_basis = _StubViewBasis()
    raster = _StubRaster()

    loops = silhouette.get_element_silhouette(elem, view, view_basis, raster, cfg=cfg)

    assert len(loops) == 2
    assert loops[0]["is_hole"] is False
    assert loops[1]["is_hole"] is True

    assert calls["silhouette_edges"] == 0, (
        "Expected planar face loops to succeed without attempting silhouette_edges"
    )

    assert loops[0].get("strategy") == planar_primary
    assert loops[1].get("strategy") == planar_primary