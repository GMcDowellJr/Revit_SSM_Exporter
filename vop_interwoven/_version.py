"""The exporter's release version, MAJOR.MINOR.PATCH.

Written by tools/analysis_version.py (the release-version workflow) on every
merge to main, from the commit messages since the last release tag: a commit
declared analysis-breaking raises MAJOR. Do not edit by hand -- the tool
refuses a file that disagrees with the last vX.Y.Z tag.
"""
__version__ = "1.0.0"
