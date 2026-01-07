def _get_excluded_3d_category_ids(doc):
    """Compatibility wrapper for legacy code; delegates to collection_policy.

    Returns a set of integer CategoryIds resolved in the given doc.
    """
    from .collection_policy import resolve_category_ids, excluded_bic_names_global
    return resolve_category_ids(doc, excluded_bic_names_global())
