"""
Revit-specific integrations for VOP pipeline.

Modules:
- view_basis: View coordinate system extraction (O, R, U, F)
- collection: Element collection and visibility filtering
"""

from .view_basis import make_view_basis, ViewBasis
from .collection import collect_view_elements, is_element_visible_in_view

__all__ = [
    "make_view_basis",
    "ViewBasis",
    "collect_view_elements",
    "is_element_visible_in_view",
]
