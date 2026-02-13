"""Manifest evaluator for aggregate metrics totals."""

from __future__ import annotations

from typing import Any


class MetricsValidationError(ValueError):
    """Raised when strict metrics validation fails."""


_FAMILY_KEY_MAP = {
    "source_partition_8": [
        "Cells_Empty",
        "Cells_ModelOnly",
        "Cells_AnnoOnly",
        "Cells_ExtOnly",
        "Cells_ModelAnno",
        "Cells_ModelExt",
        "Cells_AnnoExt",
        "Cells_All3",
    ],
    "anno_types_final": [
        "AnnoFinalCells_TEXT",
        "AnnoFinalCells_TAG",
        "AnnoFinalCells_DIM",
        "AnnoFinalCells_DETAIL",
        "AnnoFinalCells_LINES",
        "AnnoFinalCells_REGION",
        "AnnoFinalCells_OTHER",
    ],
    "ext_types_final_flags": [
        "ExtFinalCells_Any",
        "ExtFinalCells_Only",
        "ExtFinalCells_DWG",
        "ExtFinalCells_RVT",
    ],
    "ext_types_final_intersection": ["ExtFinalCells_DWG_RVT"],
    "model_classes_multihot": [
        "ModelClassCells_WALL",
        "ModelClassCells_DOOR",
        "ModelClassCells_STAIR",
        "ModelClassCells_COLUMN",
        "ModelClassCells_LIGHT",
        "ModelClassCells_OTHER",
    ],
}


def _family_sum(totals: dict[str, int], family: str) -> int:
    keys = _FAMILY_KEY_MAP.get(family, [])
    return int(sum(int(totals.get(k, 0) or 0) for k in keys))


def _eval_expr(expr: Any, totals: dict[str, int]) -> tuple[Any, dict[str, Any]]:
    if not isinstance(expr, dict) or len(expr) != 1:
        raise ValueError("invalid_expr_shape")

    op, arg = next(iter(expr.items()))

    if op == "ref":
        name = str(arg)
        if name not in totals:
            raise KeyError(name)
        return int(totals[name]), {"ref": name, "value": int(totals[name])}

    if op in {"sum", "family_sum"}:
        family = arg.get("family") if isinstance(arg, dict) else None
        fam = str(family or "")
        value = _family_sum(totals, fam)
        return value, {"family": fam, "value": value}

    if op in {"add", "sub", "eq", "le", "ge"}:
        if not isinstance(arg, list) or len(arg) != 2:
            raise ValueError("invalid_arity")
        left, left_obs = _eval_expr(arg[0], totals)
        right, right_obs = _eval_expr(arg[1], totals)

        if op == "add":
            return int(left) + int(right), {"lhs": left_obs, "rhs": right_obs}
        if op == "sub":
            return int(left) - int(right), {"lhs": left_obs, "rhs": right_obs}
        if op == "eq":
            return bool(int(left) == int(right)), {
                "lhs": int(left),
                "rhs": int(right),
                "lhs_src": left_obs,
                "rhs_src": right_obs,
            }
        if op == "le":
            return bool(int(left) <= int(right)), {
                "lhs": int(left),
                "rhs": int(right),
                "lhs_src": left_obs,
                "rhs_src": right_obs,
            }
        return bool(int(left) >= int(right)), {
            "lhs": int(left),
            "rhs": int(right),
            "lhs_src": left_obs,
            "rhs_src": right_obs,
        }

    if op == "and":
        if not isinstance(arg, list) or not arg:
            raise ValueError("invalid_arity")
        items = []
        all_pass = True
        for child in arg:
            v, obs = _eval_expr(child, totals)
            items.append(obs)
            all_pass = all_pass and bool(v)
        return all_pass, {"items": items}

    raise ValueError("unsupported_op")


def evaluate_metrics_manifest(
    totals: dict[str, int],
    manifest: dict[str, Any],
    *,
    manifest_sha256: str,
    manifest_file_name: str,
    available_primitives: list[str],
    available_capabilities: list[str],
    mode: str = "warn",
) -> dict[str, Any]:
    required_primitives = list(manifest.get("requires", {}).get("primitives", []))
    required_capabilities = list(manifest.get("requires", {}).get("capabilities", []))

    missing_primitives = sorted(set(required_primitives) - set(available_primitives))
    missing_capabilities = sorted(set(required_capabilities) - set(available_capabilities))

    invariants = []
    for inv in manifest.get("invariants", []):
        inv_id = str(inv.get("id", ""))
        level = str(inv.get("level", "error"))
        expr = inv.get("expr", {})
        passed = False
        observed = {}
        explain = ""
        try:
            value, observed = _eval_expr(expr, totals)
            passed = bool(value)
            explain = f"expr:{inv_id} => {'pass' if passed else 'fail'}"
        except KeyError as exc:
            missing = str(exc).strip("'")
            passed = False
            observed = {"missing_ref": missing}
            explain = f"missing_ref:{missing}"
        except Exception as exc:
            passed = False
            observed = {"error": str(exc)}
            explain = f"eval_error:{exc}"

        invariants.append(
            {
                "id": inv_id,
                "level": level,
                "pass": passed,
                "observed_operands": observed,
                "explain": explain,
            }
        )

    failed_error_invariants = [i for i in invariants if (i.get("level") == "error" and not i.get("pass"))]

    requires_ok = (len(missing_primitives) == 0) and (len(missing_capabilities) == 0)
    ok = requires_ok and (len(failed_error_invariants) == 0)

    result = {
        "manifest": {
            "schema_version": manifest.get("schema_version"),
            "metrics_version": manifest.get("metrics_version"),
            "sha256": manifest_sha256,
            "file_name": manifest_file_name,
        },
        "mode": mode,
        "ok": ok,
        "requires": {
            "primitives_ok": len(missing_primitives) == 0,
            "capabilities_ok": len(missing_capabilities) == 0,
            "missing_primitives": missing_primitives,
            "missing_capabilities": missing_capabilities,
            "required_primitives": required_primitives,
            "available_primitives": list(available_primitives),
            "required_capabilities": required_capabilities,
            "available_capabilities": list(available_capabilities),
        },
        "invariants": invariants,
    }

    if str(mode).lower() == "strict" and not ok:
        raise MetricsValidationError("Metrics manifest validation failed in strict mode")

    return result
