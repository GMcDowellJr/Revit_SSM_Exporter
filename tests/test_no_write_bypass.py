import os
import re


ROOT = os.path.abspath(
 os.path.join(os.path.dirname(__file__), "..")
)


FORBIDDEN_PATTERNS = [
 r"\bset_cell_filled\s*\(",
 r"\bw_occ\s*\[.*\]\s*=",
 r"\bocc_host\s*\[.*\]\s*=",
 r"\bocc_link\s*\[.*\]\s*=",
 r"\bocc_dwg\s*\[.*\]\s*=",
]


ALLOWED_FILE = os.path.join("core", "raster.py")


def test_no_occupancy_write_bypass():
 violations = []

 for root, _, files in os.walk(ROOT):
     for fn in files:
         if not fn.endswith(".py"):
             continue

         path = os.path.join(root, fn)
         rel = os.path.relpath(path, ROOT)

         with open(path, "r", encoding="utf-8", errors="ignore") as f:
             src = f.read()

         for pat in FORBIDDEN_PATTERNS:
             for m in re.finditer(pat, src):
                 if ALLOWED_FILE in rel:
                     continue
                 violations.append((rel, pat, m.start()))

 assert not violations, (
     "Occupancy write bypass detected. "
     "All writes must go through ViewRaster.try_write_cell().\n"
     + "\n".join(str(v) for v in violations)
 )
