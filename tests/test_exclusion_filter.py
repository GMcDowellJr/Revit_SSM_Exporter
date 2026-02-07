"""Test exclusion filter functionality in collection.

Phase 2B: Verifies that the exclude_ids parameter was added correctly
and maintains backward compatibility with existing callers.

Since collect_view_elements() requires the Revit API (FilteredElementCollector)
at the function level, these tests focus on:
1. Function signature compatibility (exclude_ids parameter exists)
2. Backward compatibility (existing callers without exclude_ids still work)
3. Import-level validation (the module loads without errors)
"""

import inspect
import unittest


class TestExclusionFilterSignature(unittest.TestCase):
    """Test that the exclusion filter parameter was added correctly."""

    def test_collect_view_elements_accepts_exclude_ids(self):
        """Test that collect_view_elements has exclude_ids parameter."""
        from vop_interwoven.revit.collection import collect_view_elements

        sig = inspect.signature(collect_view_elements)
        param_names = list(sig.parameters.keys())

        self.assertIn("exclude_ids", param_names,
                       "collect_view_elements should accept exclude_ids parameter")

    def test_exclude_ids_defaults_to_none(self):
        """Test that exclude_ids defaults to None for backward compatibility."""
        from vop_interwoven.revit.collection import collect_view_elements

        sig = inspect.signature(collect_view_elements)
        exclude_param = sig.parameters["exclude_ids"]

        self.assertEqual(exclude_param.default, None,
                         "exclude_ids should default to None")

    def test_original_parameters_unchanged(self):
        """Test that original parameters are preserved in correct order."""
        from vop_interwoven.revit.collection import collect_view_elements

        sig = inspect.signature(collect_view_elements)
        param_names = list(sig.parameters.keys())

        # Original parameters must still be in order
        self.assertEqual(param_names[0], "doc")
        self.assertEqual(param_names[1], "view")
        self.assertEqual(param_names[2], "raster")
        self.assertEqual(param_names[3], "diag")
        self.assertEqual(param_names[4], "cfg")

    def test_exclude_ids_is_keyword_only_or_has_default(self):
        """Test that exclude_ids won't break existing positional callers."""
        from vop_interwoven.revit.collection import collect_view_elements

        sig = inspect.signature(collect_view_elements)
        exclude_param = sig.parameters["exclude_ids"]

        # Must have a default value so existing callers don't break
        self.assertIsNot(exclude_param.default, inspect.Parameter.empty,
                         "exclude_ids must have a default value for backward compatibility")

    def test_module_imports_cleanly(self):
        """Test that the collection module imports without errors after changes."""
        # This verifies no syntax errors were introduced
        import vop_interwoven.revit.collection as col
        self.assertTrue(hasattr(col, "collect_view_elements"))
        self.assertTrue(hasattr(col, "resolve_element_bbox"))
        self.assertTrue(hasattr(col, "expand_host_link_import_model_elements"))


if __name__ == "__main__":
    unittest.main()
