import math

def pca_oriented_extents_uv(points_uv):
    """Return (major_extent, minor_extent) in UV units using 2D PCA."""
    if not points_uv:
        return (0.0, 0.0)
    n = len(points_uv)
    mx = sum(p[0] for p in points_uv) / n
    my = sum(p[1] for p in points_uv) / n

    sxx = syy = sxy = 0.0
    for (u, v) in points_uv:
        x = u - mx
        y = v - my
        sxx += x * x
        syy += y * y
        sxy += x * y
    sxx /= n
    syy /= n
    sxy /= n

    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    c = math.cos(theta)
    s = math.sin(theta)

    minA = float("inf"); maxA = float("-inf")
    minB = float("inf"); maxB = float("-inf")
    for (u, v) in points_uv:
        x = u - mx
        y = v - my
        a =  c * x + s * y
        b = -s * x + c * y
        minA = min(minA, a); maxA = max(maxA, a)
        minB = min(minB, b); maxB = max(maxB, b)

    return (maxA - minA, maxB - minB)
