"""Maths on irregularly sampled series.

Scheduled runs can be late or missing, so nothing here assumes a fixed gap
between samples: every function works from the real sample timestamps.
"""

from bisect import bisect_left, bisect_right


def interp(ts, vs, t, max_gap, edge_tol=0):
    """Value at time `t`, linearly interpolated between the surrounding samples.

    Returns None when `t` lies outside the sampled range (beyond `edge_tol`) or
    inside a gap wider than `max_gap` (unless a sample is within `edge_tol`).
    """
    n = len(ts)
    if n == 0:
        return None
    i = bisect_right(ts, t)
    if i == 0:
        return vs[0] if ts[0] - t <= edge_tol else None
    if i == n:
        return vs[-1] if t - ts[-1] <= edge_tol else None
    t0, t1 = ts[i - 1], ts[i]
    if t0 == t:
        return vs[i - 1]
    if t1 - t0 > max_gap:
        if t - t0 <= edge_tol:
            return vs[i - 1]
        if t1 - t <= edge_tol:
            return vs[i]
        return None
    return vs[i - 1] + (vs[i] - vs[i - 1]) * (t - t0) / (t1 - t0)


def bucket_stats(ts, vs, edges, max_gap):
    """Time-weighted mean of the series inside each bucket [edges[k], edges[k+1]).

    The series is treated as piecewise linear between samples that are at most
    `max_gap` apart; wider gaps count as "not covered". Returns two lists with
    one entry per bucket: the mean (None if nothing is known) and the number of
    covered seconds. A bucket that has samples but no covered span (a single
    isolated sample) falls back to the plain mean of those samples.
    """
    buckets = len(edges) - 1
    area = [0.0] * buckets
    covered = [0.0] * buckets
    n = len(ts)
    if buckets <= 0:
        return [], []
    lo, hi = edges[0], edges[-1]

    start = max(0, bisect_right(ts, lo) - 1)
    for i in range(start, n - 1):
        t0, t1 = ts[i], ts[i + 1]
        if t0 >= hi:
            break
        if t1 <= lo or t1 <= t0 or t1 - t0 > max_gap:
            continue
        v0, v1 = vs[i], vs[i + 1]
        slope = (v1 - v0) / (t1 - t0)
        k = max(0, bisect_right(edges, t0) - 1)
        while k < buckets and edges[k] < t1:
            a = max(t0, edges[k])
            b = min(t1, edges[k + 1])
            if b > a:
                va = v0 + slope * (a - t0)
                vb = v0 + slope * (b - t0)
                area[k] += (va + vb) / 2.0 * (b - a)
                covered[k] += b - a
            k += 1

    means = []
    for k in range(buckets):
        if covered[k] > 0:
            means.append(area[k] / covered[k])
            continue
        i0 = bisect_left(ts, edges[k])
        # The last bucket also owns a sample sitting exactly on its end edge.
        i1 = bisect_right(ts, edges[k + 1]) if k == buckets - 1 else bisect_left(ts, edges[k + 1])
        if i1 > i0:
            means.append(sum(vs[i0:i1]) / (i1 - i0))
        else:
            means.append(None)
    return means, covered


def peak_between(ts, vs, a, b):
    """Largest sample with a <= t < b, or None."""
    i0 = bisect_left(ts, a)
    i1 = bisect_left(ts, b)
    return max(vs[i0:i1]) if i1 > i0 else None


def last_before(ts, vs, t):
    """Latest sample with time < t, or None."""
    i = bisect_left(ts, t)
    return vs[i - 1] if i > 0 else None
