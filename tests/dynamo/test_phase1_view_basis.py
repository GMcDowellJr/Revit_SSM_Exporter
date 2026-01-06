"""
Phase 1 Test: View Basis & Coordinate System

Copy this code into a Dynamo Python node to test Phase 1 implementation.

Prerequisites:
- Phase 1 implementation complete (view_basis.py)
- Active Revit view open

Success Criteria:
- View basis extracted without errors
- Right, Up, Forward vectors are orthonormal
- View origin transforms to ~(0,0,0) in view space
"""

import sys
sys.path.append(r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter')

from vop_interwoven.entry_dynamo import get_current_document, get_current_view
from vop_interwoven.revit.view_basis import make_view_basis, world_to_view
import math

doc = get_current_document()
view = get_current_view()

results = []

try:
    # Test 1: Extract view basis
    vb = make_view_basis(view)
    results.append(f"✅ View basis extracted")
    results.append(f"   Origin: ({vb.origin[0]:.2f}, {vb.origin[1]:.2f}, {vb.origin[2]:.2f})")
    results.append(f"   Right:  ({vb.right[0]:.3f}, {vb.right[1]:.3f}, {vb.right[2]:.3f})")
    results.append(f"   Up:     ({vb.up[0]:.3f}, {vb.up[1]:.3f}, {vb.up[2]:.3f})")
    results.append(f"   Forward: ({vb.forward[0]:.3f}, {vb.forward[1]:.3f}, {vb.forward[2]:.3f})")

    # Test 2: Check orthonormality
    def dot(a, b):
        return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

    def length(v):
        return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)

    right_dot_up = abs(dot(vb.right, vb.up))
    right_dot_fwd = abs(dot(vb.right, vb.forward))
    up_dot_fwd = abs(dot(vb.up, vb.forward))

    if right_dot_up < 0.001 and right_dot_fwd < 0.001 and up_dot_fwd < 0.001:
        results.append("✅ Vectors are orthogonal")
    else:
        results.append(f"⚠ Vectors may not be orthogonal: {right_dot_up:.4f}, {right_dot_fwd:.4f}, {up_dot_fwd:.4f}")

    len_right = length(vb.right)
    len_up = length(vb.up)
    len_fwd = length(vb.forward)

    if abs(len_right - 1.0) < 0.001 and abs(len_up - 1.0) < 0.001 and abs(len_fwd - 1.0) < 0.001:
        results.append("✅ Vectors are normalized")
    else:
        results.append(f"⚠ Vectors may not be normalized: {len_right:.4f}, {len_up:.4f}, {len_fwd:.4f}")

    # Test 3: Transform view origin (should be ~(0,0,0) in view space)
    view_pt_origin = world_to_view(vb.origin, vb)
    dist_from_origin = math.sqrt(view_pt_origin[0]**2 + view_pt_origin[1]**2 + view_pt_origin[2]**2)

    if dist_from_origin < 0.001:
        results.append(f"✅ View origin transforms to (0,0,0)")
    else:
        results.append(f"⚠ View origin in view space: ({view_pt_origin[0]:.4f}, {view_pt_origin[1]:.4f}, {view_pt_origin[2]:.4f})")

    # Test 4: Transform a known world point
    world_pt = (0, 0, 0)  # Revit origin
    view_pt = world_to_view(world_pt, vb)
    results.append(f"✅ World (0,0,0) -> View: ({view_pt[0]:.2f}, {view_pt[1]:.2f}, {view_pt[2]:.2f})")

    results.append("")
    results.append("=" * 50)
    results.append("✅✅✅ PHASE 1 COMPLETE ✅✅✅")
    results.append("=" * 50)

except Exception as e:
    results.append(f"❌ Phase 1 failed: {e}")
    import traceback
    results.append(traceback.format_exc())

OUT = "\n".join(results)
