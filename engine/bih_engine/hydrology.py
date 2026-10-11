"""Deterministic terrain hydrology on a metric grid: depression filling, D8 flow, accumulation.

Pure numpy/numba, no I/O and no randomness: the same grid in gives the same answer out.
"""
from __future__ import annotations

import heapq

import numpy as np
from numba import njit

# D8 neighbour offsets. The order is fixed so ties break the same way on every run.
D8_DR = np.array([-1, -1, -1, 0, 0, 1, 1, 1], dtype=np.int64)
D8_DC = np.array([-1, 0, 1, -1, 1, -1, 0, 1], dtype=np.int64)
_DIST = np.array([2**0.5, 1.0, 2**0.5, 1.0, 1.0, 2**0.5, 1.0, 2**0.5])

# Keep epsilon tiny: it accumulates along a filled flat, and the depth threshold sits well above it.
EPS = 1e-6


@njit(cache=True)
def _priority_flood(dem, outlet, eps):
    """Barnes (2014) Priority-Flood with epsilon: fill every depression to its spill level and
    leave a tiny gradient across the filled flats so every cell still drains somewhere."""
    h, w = dem.shape
    filled = dem.copy()
    closed = np.zeros((h, w), np.bool_)
    heap = [(0.0, 0)]
    heapq.heappop(heap)
    for r in range(h):
        for c in range(w):
            if outlet[r, c]:
                closed[r, c] = True
                heapq.heappush(heap, (filled[r, c], r * w + c))
    while len(heap) > 0:
        z, idx = heapq.heappop(heap)
        r = idx // w
        c = idx % w
        for k in range(8):
            nr = r + D8_DR[k]
            nc = c + D8_DC[k]
            if nr < 0 or nr >= h or nc < 0 or nc >= w:
                continue
            if closed[nr, nc]:
                continue
            closed[nr, nc] = True
            nz = filled[nr, nc]
            if nz <= z + eps:
                nz = z + eps
            filled[nr, nc] = nz
            heapq.heappush(heap, (nz, nr * w + nc))
    return filled


@njit(cache=True)
def _d8_directions(filled, outlet):
    """Index (0-7) of the steepest-descent neighbour, or -1 for outlets (water leaves the grid)."""
    h, w = filled.shape
    out = np.full((h, w), -1, np.int8)
    for r in range(h):
        for c in range(w):
            if outlet[r, c]:
                continue
            z = filled[r, c]
            best = 0.0
            bk = -1
            for k in range(8):
                nr = r + D8_DR[k]
                nc = c + D8_DC[k]
                if nr < 0 or nr >= h or nc < 0 or nc >= w:
                    continue
                drop = (z - filled[nr, nc]) / _DIST[k]
                if drop > best:
                    best = drop
                    bk = k
            out[r, c] = bk
    return out


@njit(cache=True)
def _accumulate(filled, fdir, weight):
    """Sum `weight` down the flow directions, highest cell first. Strictly lower downstream
    cells (guaranteed by the epsilon fill) make this order a valid topological order."""
    h, w = filled.shape
    order = np.argsort(filled.ravel())[::-1]
    acc = weight.copy()
    for i in range(order.size):
        idx = order[i]
        r = idx // w
        c = idx % w
        k = fdir[r, c]
        if k >= 0:
            acc[r + D8_DR[k], c + D8_DC[k]] += acc[r, c]
    return acc


@njit(cache=True)
def _exit_accumulation(labels, fdir, acc, n):
    out = np.zeros(n + 1)
    h, w = labels.shape
    for r in range(h):
        for c in range(w):
            lab = labels[r, c]
            k = fdir[r, c]
            if lab == 0 or k < 0:
                continue
            r2 = r + D8_DR[k]
            c2 = c + D8_DC[k]
            if labels[r2, c2] == lab:
                continue  # water stays inside the region
            # (r, c) is an exit. Count it only if the water never comes back to this region further
            # down: otherwise the later exit already includes everything this one carries.
            returns = False
            while True:
                if labels[r2, c2] == lab:
                    returns = True
                    break
                k2 = fdir[r2, c2]
                if k2 < 0:
                    break
                r2 += D8_DR[k2]
                c2 += D8_DC[k2]
            if not returns:
                out[lab] += acc[r, c]
    return out


def exit_accumulation(labels: np.ndarray, fdir: np.ndarray, acc: np.ndarray, n: int) -> np.ndarray:
    """Catchment of each labelled region 1..n, in accumulation units (cells by default).

    Everything that enters a region leaves it through exit cells (downstream cell outside it), so
    the catchment is the accumulation summed over those exits, with two traps:
    - The max inside the region undercounts: a filled flat has several equal-level exits.
    - Summing every exit overcounts when water leaves the region over a shallow rim and re-enters
      it further down (a region cut at a depth threshold is rarely convex). Only exits whose
      downstream path never returns to the region are counted.
    """
    return _exit_accumulation(np.ascontiguousarray(labels, dtype=np.int64),
                              np.ascontiguousarray(fdir, dtype=np.int8),
                              np.ascontiguousarray(acc, dtype=np.float64), int(n))[1:]


def fill_and_route(dem: np.ndarray, outlet: np.ndarray, weight: np.ndarray | None = None):
    """Return (filled, flow_dir, accumulation) for a DEM.

    `outlet` marks cells where water leaves the domain (grid border, nodata, open water).
    `weight` is what each cell contributes to accumulation (default 1 per cell).
    """
    dem = np.ascontiguousarray(dem, dtype=np.float64)
    outlet = np.ascontiguousarray(outlet, dtype=np.bool_)
    if weight is None:
        weight = np.ones(dem.shape, dtype=np.float64)
    filled = _priority_flood(dem, outlet, EPS)
    fdir = _d8_directions(filled, outlet)
    acc = _accumulate(filled, fdir, np.ascontiguousarray(weight, dtype=np.float64))
    return filled, fdir, acc
