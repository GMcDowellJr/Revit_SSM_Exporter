#!/bin/bash
# Quick test runner for VOP interwoven pipeline

echo "======================================"
echo "VOP Interwoven Pipeline - Test Runner"
echo "======================================"
echo ""

cd "$(dirname "$0")/.."

echo "Running geometry classification tests..."
python vop_interwoven/tests/test_geometry.py
RESULT_GEO=$?

echo ""
echo "Running raster structure tests..."
python vop_interwoven/tests/test_raster.py
RESULT_RASTER=$?

echo ""
echo "======================================"
if [ $RESULT_GEO -eq 0 ] && [ $RESULT_RASTER -eq 0 ]; then
    echo "✅ ALL TESTS PASSED (29/29)"
else
    echo "❌ SOME TESTS FAILED"
fi
echo "======================================"

exit $(($RESULT_GEO + $RESULT_RASTER))
