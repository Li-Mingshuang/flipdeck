"""flipdeck/meshlib.py -- SDF -> closed triangle mesh (Surface Nets / naive Dual Contouring).

Also provides binary STL I/O and mesh statistics.

Conventions (see SPEC_interfaces.md):
  * millimetres, right-handed, Z up.
  * ``verts`` is ``(N,3)`` float64; ``tris`` is ``(M,3)`` int32 indexing ``verts``.
  * triangle vertices are counter-clockwise **seen from outside**, so the
    right-hand-rule normal points outward (this is what the renderer lights with).
  * meshes are closed: no boundary edges, ever.  For the SDFs this project uses
    (boxes/spheres/cylinders and CSG combinations, smooth blobs) they are also
    watertight *and* manifold -- every edge shared by exactly two triangles, no
    isolated vertices, no degenerate triangles.  The one exception is a cell face
    with the ambiguous "checkerboard" sign pattern; see ``surface_nets`` and
    self-test [6] for the measured behaviour.

Everything is numpy-vectorised.  The only Python loops iterate over the 8 cube
corners / 12 cube edges / 3 axes, never over grid points.
"""

from __future__ import annotations

import os

import numpy as np

__all__ = [
    "sample_grid",
    "surface_nets",
    "write_stl",
    "read_stl",
    "mesh_stats",
    "voxel_occupancy",
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

#: cube corner offsets, index c = dx + 2*dy + 4*dz
_CORNER_POS = np.array(
    [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [1, 1, 0],
        [0, 0, 1],
        [1, 0, 1],
        [0, 1, 1],
        [1, 1, 1],
    ],
    dtype=np.float64,
)

#: the 12 cube edges as pairs of corner indices (4 along x, 4 along y, 4 along z)
_EDGE_CORNERS = np.array(
    [
        [0, 1], [2, 3], [4, 5], [6, 7],
        [0, 2], [1, 3], [4, 6], [5, 7],
        [0, 4], [1, 5], [2, 6], [3, 7],
    ],
    dtype=np.int64,
)
_EDGE_A = _EDGE_CORNERS[:, 0]
_EDGE_B = _EDGE_CORNERS[:, 1]

#: chunk sizes used to keep peak memory bounded regardless of grid size
_VERT_CHUNK = 131072
_POINT_CHUNK = 1 << 21  # ~2M sample points evaluated per sdf() call

#: Edge crossings are clamped to this fraction of the edge away from its end points.
#: Without it, a zero level set that runs exactly through a lattice point (e.g. an
#: axis-aligned box or a round hole whose axis sits on a grid line) puts the crossing
#: exactly on the corner; two neighbouring cells then average exactly the same
#: crossing set and produce *coincident* dual vertices, which yields zero-area
#: triangles (and 0/0 face normals downstream).  The clamp only ever affects
#: t == 0 or t == 1, i.e. exactly that case, and displaces the surface by at most
#: ``_T_EPS * voxel`` (0.5 nm at voxel 0.5).
_T_EPS = 1e-6


def _norm_bounds(lo, hi, voxel):
    lo = np.asarray(lo, dtype=np.float64).reshape(3)
    hi = np.asarray(hi, dtype=np.float64).reshape(3)
    voxel = float(voxel)
    if not (np.isfinite(lo).all() and np.isfinite(hi).all()):
        raise ValueError("lo/hi must be finite")
    if not np.isfinite(voxel) or voxel <= 0.0:
        raise ValueError("voxel must be a positive finite number")
    if np.any(hi <= lo):
        raise ValueError("hi must be strictly greater than lo on every axis")
    return lo, hi, voxel


def _grid_counts(lo, hi, voxel):
    """nx = int(ceil((hi-lo)/voxel)) + 1 per axis (a 1e-9 guard absorbs fp noise)."""
    n = np.ceil((hi - lo) / voxel - 1e-9).astype(np.int64) + 1
    return np.maximum(n, 2)


def _eval_grid(sdf, axes):
    """Evaluate ``sdf`` on the tensor product of ``axes`` -> (nx,ny,nz) float64.

    Evaluation is done in slabs of z-planes so that the (nx,ny,nz,3) point array
    is never materialised for very large grids.
    """
    n = (axes[0].size, axes[1].size, axes[2].size)
    planes = max(1, _POINT_CHUNK // max(1, n[0] * n[1]))

    F = np.empty(n, dtype=np.float64)
    xc = axes[0][:, None, None]
    yc = axes[1][None, :, None]
    for k0 in range(0, n[2], planes):
        k1 = min(n[2], k0 + planes)
        kk = k1 - k0
        P = np.empty((n[0], n[1], kk, 3), dtype=np.float64)
        P[..., 0] = xc
        P[..., 1] = yc
        P[..., 2] = axes[2][k0:k1][None, None, :]
        val = np.asarray(sdf(P), dtype=np.float64)
        try:
            val = np.broadcast_to(val, (n[0], n[1], kk))
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(
                "sdf returned shape %r, expected something broadcastable to %r"
                % (np.shape(val), (n[0], n[1], kk))
            ) from exc
        F[:, :, k0:k1] = val
    return F


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #

def sample_grid(sdf, lo, hi, voxel):
    """Sample ``sdf`` on the regular grid ``p = lo + (i,j,k)*voxel``.

    Returns ``(F, axes)``:
      * ``F``    -- ``(nx,ny,nz)`` float64, ``F[i,j,k] == sdf(xs[i], ys[j], zs[k])``
      * ``axes`` -- ``(xs, ys, zs)`` 1-D float64 coordinate arrays of length
        ``nx, ny, nz`` with ``n = int(ceil((hi-lo)/voxel)) + 1``.
    """
    lo, hi, voxel = _norm_bounds(lo, hi, voxel)
    n = _grid_counts(lo, hi, voxel)
    axes = tuple(lo[a] + np.arange(n[a], dtype=np.float64) * voxel for a in range(3))
    return _eval_grid(sdf, axes), axes


def surface_nets(sdf, lo, hi, voxel):
    """SDF -> closed triangle mesh (Surface Nets / naive Dual Contouring).

    * one dual vertex per *cell* that straddles the zero level set, placed at the
      mean of the interpolated zero crossings on the cell's 12 edges;
    * one quad per *grid edge* whose two endpoints differ in sign, joining the
      dual vertices of the 4 cells that share that edge;
    * quads are wound so the right-hand-rule normal points along the signed
      distance gradient, i.e. outward.

    Because every sign-changing edge contributes exactly one quad and the 4 cells
    around such an edge are guaranteed to own dual vertices, no quad is ever left
    dangling: ``boundary_edges`` is always 0 and the mesh is always *closed*.

    The zero level set must not touch the sampling boundary; a ``ValueError`` is
    raised if it does (enlarge ``lo``/``hi``).

    Known limitation of the one-vertex-per-cell rule (measured, see the demo at the
    bottom of this file): when a cell face has the *ambiguous* ("checkerboard")
    sign pattern -- its 4 corners alternating inside/outside -- the surface crosses
    that face in two separate branches.  All four grid edges of such a face emit a
    quad that joins the same pair of dual vertices, so that dual edge ends up in 4
    triangles and ``mesh_stats`` reports it as a ``nonmanifold_edge``.  The mesh
    stays closed (no holes) but is not manifold at that edge.  Resolving it
    rigorously needs more than one vertex per cell (Manifold Dual Contouring),
    which the one-vertex-per-cell rule forbids, so it is reported rather than
    silently papered over.
    """
    lo, hi, voxel = _norm_bounds(lo, hi, voxel)
    F, axes = sample_grid(sdf, lo, hi, voxel)
    nx, ny, nz = F.shape

    if nx < 2 or ny < 2 or nz < 2:
        raise ValueError("sampling grid too small for surface_nets")

    # ---- the zero level set must stay strictly inside the sampling box ------ #
    face_vecs = (
        ("x = lo[0]", F[0, :, :]), ("x = hi[0]", F[-1, :, :]),
        ("y = lo[1]", F[:, 0, :]), ("y = hi[1]", F[:, -1, :]),
        ("z = lo[2]", F[:, :, 0]), ("z = hi[2]", F[:, :, -1]),
    )
    worst = None
    for fname, fval in face_vecs:
        m = float(fval.min())
        if m < 0.0 and (worst is None or m < worst[1]):
            worst = (fname, m)
    if worst is not None:
        pad = -worst[1] + 2.0 * voxel
        raise ValueError(
            "the zero level set touches the sampling boundary: %s has a sample at "
            "%.6g mm (still inside the solid). Enlarge lo/hi by at least %.6g mm "
            "(= the overlap plus 2 voxels of padding) so the surface is fully "
            "contained." % (worst[0], worst[1], pad)
        )

    cell_shape = (nx - 1, ny - 1, nz - 1)
    ncx, ncy, ncz = cell_shape
    n_cells = ncx * ncy * ncz

    # ---- which cells straddle the surface? --------------------------------- #
    nin = np.zeros(cell_shape, dtype=np.uint8)
    for dz in range(2):
        for dy in range(2):
            for dx in range(2):
                nin += F[dx:dx + ncx, dy:dy + ncy, dz:dz + ncz] < 0.0
    active = (nin > 0) & (nin < 8)
    del nin

    if not active.any():
        return (np.zeros((0, 3), dtype=np.float64),
                np.zeros((0, 3), dtype=np.int32))

    flat_active = np.nonzero(active.ravel())[0]
    del active
    K = int(flat_active.size)
    ci, cj, ck = np.unravel_index(flat_active, cell_shape)

    # cell flat index -> dual vertex index (-1 for cells without a vertex)
    cell_to_vert = np.full(n_cells, -1, dtype=np.int32)
    cell_to_vert[flat_active] = np.arange(K, dtype=np.int32)

    Fflat = F.reshape(-1)                                # view: F is C-contiguous
    # C-order flat index for grid point (i,j,k) is i*(ny*nz) + j*nz + k
    stride_x, stride_y = ny * nz, nz
    grid_base = ci * stride_x + cj * stride_y + ck       # flat index of corner (0,0,0)
    corner_off = (
        _CORNER_POS[:, 0].astype(np.int64) * stride_x
        + _CORNER_POS[:, 1].astype(np.int64) * stride_y
        + _CORNER_POS[:, 2].astype(np.int64)
    )                                                    # (8,) flat offsets

    # ---- dual vertex positions (mean of the 12 edge crossings) ------------- #
    verts = np.empty((K, 3), dtype=np.float64)
    for s in range(0, K, _VERT_CHUNK):
        e = min(K, s + _VERT_CHUNK)
        cv = Fflat[grid_base[s:e, None] + corner_off[None, :]]      # (k,8)
        ins = cv < 0.0
        fa = cv[:, _EDGE_A]
        fb = cv[:, _EDGE_B]
        ia = ins[:, _EDGE_A]
        ib = ins[:, _EDGE_B]
        cross = ia != ib                                            # (k,12) bool
        den = fa - fb                                               # never 0 where cross
        den = np.where(cross, den, 1.0)
        t = np.where(cross, np.clip(fa / den, _T_EPS, 1.0 - _T_EPS), 0.0)   # (k,12)
        pa = _CORNER_POS[_EDGE_A]
        pb = _CORNER_POS[_EDGE_B]
        d = pb - pa                                                 # (12,3)
        pts = pa[None, :, :] + t[:, :, None] * d[None, :, :]        # (k,12,3)
        acc = pts.sum(axis=1, where=cross[:, :, None], initial=0.0)
        cnt = cross.sum(axis=1)
        local = acc / cnt[:, None]                                  # (k,3) in [0,1]^3
        verts[s:e] = lo + (
            np.stack([ci[s:e], cj[s:e], ck[s:e]], axis=1) + local
        ) * voxel

    # ---- one quad per sign-changing grid edge ------------------------------ #
    tri_parts = []
    for axis in range(3):
        quads = _axis_quads(F, Fflat, axis, cell_to_vert, cell_shape, verts.shape[0])
        if quads is not None and quads.size:
            tri_parts.append(quads)

    if not tri_parts:
        return (np.zeros((0, 3), dtype=np.float64),
                np.zeros((0, 3), dtype=np.int32))

    tris = np.concatenate(tri_parts, axis=0).astype(np.int32, copy=False)
    return verts, tris


def _axis_quads(F, Fflat, axis, cell_to_vert, cell_shape, n_verts):
    """Quads for every sign-changing grid edge running along ``axis``.

    For an edge along ``a`` the two remaining axes are ``b=(a+1)%3`` and
    ``c=(a+2)%3``.  The four surrounding cells, taken in the order

        (b_lo,c_lo) -> (b_hi,c_lo) -> (b_hi,c_hi) -> (b_lo,c_hi)

    form a quad whose right-hand-rule normal points along **+a** (verified by
    direct cross product).  The surface normal must point from inside to outside,
    so if the *low* end of the edge is outside we simply reverse the quad.
    """
    nx, ny, nz = F.shape
    n = (nx, ny, nz)
    a = axis
    b = (a + 1) % 3
    c = (a + 2) % 3

    # C-order flat index of a cell (i,j,k): i*(ncy*ncz) + j*ncz + k
    cell_stride_x = cell_shape[1] * cell_shape[2]
    cell_stride_y = cell_shape[2]

    # sign-changing grid edges: index along a in [0, n_a-2], along b/c in [1, n-2]
    s_lo = [slice(None)] * 3
    s_hi = [slice(None)] * 3
    s_lo[a] = slice(0, n[a] - 1)
    s_hi[a] = slice(1, n[a])
    for ax in (b, c):
        s_lo[ax] = slice(1, n[ax] - 1)
        s_hi[ax] = slice(1, n[ax] - 1)

    v_lo = F[tuple(s_lo)]
    v_hi = F[tuple(s_hi)]
    mask = (v_lo < 0.0) != (v_hi < 0.0)
    if not mask.any():
        return None

    flat = np.nonzero(mask.ravel())[0]
    iax = list(np.unravel_index(flat, mask.shape))     # reduced indices per axis

    # grid index of the low end of the edge
    g = [None, None, None]
    g[a] = iax[a]
    g[b] = iax[b] + 1
    g[c] = iax[c] + 1

    # cell indices of the 4 surrounding cells
    c_lo_b, c_hi_b = iax[b], iax[b] + 1
    c_lo_c, c_hi_c = iax[c], iax[c] + 1

    def cell_flat_index(cb, cc):
        idx = [None, None, None]
        idx[a] = iax[a]
        idx[b] = cb
        idx[c] = cc
        return (idx[0] * cell_stride_x + idx[1] * cell_stride_y + idx[2]).astype(
            np.int64, copy=False
        )

    f_ll = cell_flat_index(c_lo_b, c_lo_c)
    f_hl = cell_flat_index(c_hi_b, c_lo_c)
    f_hh = cell_flat_index(c_hi_b, c_hi_c)
    f_lh = cell_flat_index(c_lo_b, c_hi_c)

    q0 = cell_to_vert[f_ll]
    q1 = cell_to_vert[f_hl]
    q2 = cell_to_vert[f_hh]
    q3 = cell_to_vert[f_lh]
    if min(int(q0.min()), int(q1.min()), int(q2.min()), int(q3.min())) < 0:
        raise RuntimeError(
            "internal error: a sign-changing edge has a surrounding cell without a "
            "dual vertex (this must not happen)"
        )

    p0_flat = g[0] * (ny * nz) + g[1] * nz + g[2]        # C-order flat index
    inside_low = Fflat[p0_flat] < 0.0                  # gradient points towards +a ?

    # forward order (q0,q1,q2,q3) has normal +a  ->  good when the low end is inside
    p0 = q0
    p1 = np.where(inside_low, q1, q3)
    p2 = q2
    p3 = np.where(inside_low, q3, q1)

    t1 = np.stack([p0, p1, p2], axis=1)
    t2 = np.stack([p0, p2, p3], axis=1)
    quads = np.concatenate([t1, t2], axis=0)
    if quads.max() >= n_verts:  # pragma: no cover - defensive
        raise RuntimeError("internal error: triangle index out of range")
    return quads


# --------------------------------------------------------------------------- #
# binary STL
# --------------------------------------------------------------------------- #

_STL_DTYPE = np.dtype([("normal", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")])
assert _STL_DTYPE.itemsize == 50, _STL_DTYPE.itemsize


def write_stl(path, verts, tris):
    """Write a binary STL (80-byte header + uint32 count + 50 bytes/triangle).

    Returns ``path``.
    """
    verts = np.ascontiguousarray(verts, dtype=np.float64).reshape(-1, 3)
    tris = np.ascontiguousarray(tris, dtype=np.int64).reshape(-1, 3)
    if tris.size and (tris.min() < 0 or tris.max() >= verts.shape[0]):
        raise ValueError("triangle index out of range")

    tri_v = verts[tris]                                        # (M,3,3)
    nrm = np.cross(tri_v[:, 1] - tri_v[:, 0], tri_v[:, 2] - tri_v[:, 0])
    ln = np.linalg.norm(nrm, axis=1)
    ln[ln == 0.0] = 1.0
    nrm = nrm / ln[:, None]

    rec = np.zeros(tris.shape[0], dtype=_STL_DTYPE)
    rec["normal"] = nrm.astype(np.float32)
    rec["v"] = tri_v.astype(np.float32)

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    header = b"flipdeck meshlib binary STL - mm - Z up"
    header = header[:80].ljust(80, b"\x00")
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(np.uint32(tris.shape[0]).tobytes())
        fh.write(rec.tobytes())
    return path


def read_stl(path):
    """Read a binary STL back into ``(verts, tris)``.

    STL stores 3 loose vertices per facet, so identical coordinates are welded
    back into shared vertices (otherwise every edge would look like a boundary
    edge and round-trip watertightness could not be checked).
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 84:
        raise ValueError("not a binary STL: file shorter than 84 bytes")
    n_tri = int(np.frombuffer(data, dtype="<u4", count=1, offset=80)[0])
    need = 84 + 50 * n_tri
    if len(data) < need:
        raise ValueError(
            "truncated binary STL: header claims %d triangles (%d bytes) but file has %d"
            % (n_tri, need, len(data))
        )

    rec = np.frombuffer(data, dtype=_STL_DTYPE, count=n_tri, offset=84)
    loose = np.ascontiguousarray(rec["v"], dtype=np.float64).reshape(-1, 3)
    if n_tri == 0:
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0, 3), dtype=np.int32)

    uniq, inv = np.unique(loose, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    tris = inv[np.arange(3 * n_tri, dtype=np.int64).reshape(n_tri, 3)]
    return uniq, tris.astype(np.int32)


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #

def mesh_stats(verts, tris):
    """Statistics for a triangle mesh.

    Keys (at least these):
      ``n_tris``, ``n_verts``, ``volume`` (mm^3 via the divergence theorem),
      ``watertight`` (bool), ``bbox``, ``boundary_edges`` (shared by 1 triangle),
      ``nonmanifold_edges`` (>2 triangles).

    ``bbox`` is ``((xmin,ymin,zmin), (xmax,ymax,zmax))`` -- a ``min``/``max`` pair of
    two 3-vectors, which is how ``build.py`` consumes it (``bb[0][0] ... bb[1][2]``)
    and matches the ``(lo, hi)`` convention used throughout flipdeck-cad.  The flat
    6-tuple ``(xmin,ymin,zmin,xmax,ymax,zmax)`` is available as ``bbox_flat``.

    Extra keys: ``bbox_flat``, ``bbox_min``, ``bbox_max``, ``area``,
    ``degenerate_tris``, ``unused_verts``.
    """
    verts = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
    tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    n_verts = int(verts.shape[0])
    n_tris = int(tris.shape[0])

    volume = 0.0
    area = 0.0
    degenerate = 0
    boundary_edges = 0
    nonmanifold_edges = 0
    unused_verts = 0

    if n_tris:
        if tris.min() < 0 or tris.max() >= n_verts:
            raise ValueError("triangle index out of range")
        v0 = verts[tris[:, 0]]
        v1 = verts[tris[:, 1]]
        v2 = verts[tris[:, 2]]
        cr = np.cross(v1 - v0, v2 - v0)
        cl = np.linalg.norm(cr, axis=1)
        degenerate = int(np.count_nonzero(cl == 0.0))
        area = float(0.5 * cl.sum())
        volume = float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)

        edges = np.concatenate(
            [tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]], axis=0
        ).astype(np.int64)
        lo_e = np.minimum(edges[:, 0], edges[:, 1])
        hi_e = np.maximum(edges[:, 0], edges[:, 1])
        key = lo_e * n_verts + hi_e
        _, counts = np.unique(key, return_counts=True)
        boundary_edges = int(np.count_nonzero(counts == 1))
        nonmanifold_edges = int(np.count_nonzero(counts > 2))

        if n_verts:
            used = np.zeros(n_verts, dtype=bool)
            used[tris.ravel()] = True
            unused_verts = int(np.count_nonzero(~used))

    if n_verts:
        bmin = verts.min(axis=0)
        bmax = verts.max(axis=0)
    else:
        bmin = np.zeros(3)
        bmax = np.zeros(3)

    bbox_flat = (
        float(bmin[0]), float(bmin[1]), float(bmin[2]),
        float(bmax[0]), float(bmax[1]), float(bmax[2]),
    )
    return {
        "n_tris": n_tris,
        "n_verts": n_verts,
        "volume": volume,
        "watertight": bool(boundary_edges == 0 and nonmanifold_edges == 0),
        # (min, max) pair of 3-vectors -- see the docstring
        "bbox": ((bbox_flat[0], bbox_flat[1], bbox_flat[2]),
                 (bbox_flat[3], bbox_flat[4], bbox_flat[5])),
        "bbox_flat": bbox_flat,
        "bbox_min": (bbox_flat[0], bbox_flat[1], bbox_flat[2]),
        "bbox_max": (bbox_flat[3], bbox_flat[4], bbox_flat[5]),
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "area": area,
        "degenerate_tris": degenerate,
        "unused_verts": unused_verts,
    }


def voxel_occupancy(sdf, lo, hi, voxel):
    """Boolean voxel occupancy from ``F < 0`` sampled at voxel **centres**.

    Returns ``(occ, axes)`` where ``occ`` has shape
    ``(ceil((hi[0]-lo[0])/voxel), ...)`` and ``axes`` holds the centre coordinates.
    The voxelisation matches the cells used by :func:`sample_grid`.
    """
    lo, hi, voxel = _norm_bounds(lo, hi, voxel)
    n = np.maximum(np.ceil((hi - lo) / voxel - 1e-9).astype(np.int64), 1)
    axes = tuple(lo[a] + (np.arange(n[a], dtype=np.float64) + 0.5) * voxel for a in range(3))
    F = _eval_grid(sdf, axes)
    return F < 0.0, axes


# --------------------------------------------------------------------------- #
# self test
# --------------------------------------------------------------------------- #

def _sdf_sphere(p):
    return np.linalg.norm(p, axis=-1) - 20.0


def _sdf_box(p, hx, hy, hz):
    ax = np.abs(p[..., 0]) - hx
    ay = np.abs(p[..., 1]) - hy
    az = np.abs(p[..., 2]) - hz
    return np.maximum(np.maximum(ax, ay), az)


def _sdf_box_hole(p):
    """30x20x10 block with a diameter-4 through hole along Z."""
    box = _sdf_box(p, 15.0, 10.0, 5.0)
    cyl = np.sqrt(p[..., 0] ** 2 + p[..., 1] ** 2) - 2.0
    return np.maximum(box, -cyl)


def _point_in_mesh(verts, tris, points):
    """Parity ray cast (Moller-Trumbore), vectorised over triangles.

    Three generic, non axis-aligned ray directions are used and the results are
    decided by majority vote: an axis-aligned ray can graze a mesh edge or vertex
    and be counted zero or twice.
    """
    dirs = (
        np.array([0.3178, 0.5501, 0.7713]),
        np.array([-0.6183, 0.2071, 0.7583]),
        np.array([0.1327, -0.7211, 0.6803]),
    )
    v0 = verts[tris[:, 0]]
    v1 = verts[tris[:, 1]]
    v2 = verts[tris[:, 2]]
    e1 = v1 - v0
    e2 = v2 - v0

    votes = np.zeros(len(points), dtype=np.int32)
    for ray in dirs:
        h = np.cross(ray, e2)
        det = np.einsum("ij,ij->i", e1, h)
        ok = np.abs(det) > 1e-14
        inv = np.zeros_like(det)
        inv[ok] = 1.0 / det[ok]
        for i, p in enumerate(points):
            s = p - v0
            u = np.einsum("ij,ij->i", s, h) * inv
            q = np.cross(s, e1)
            w = (q @ ray) * inv
            t = np.einsum("ij,ij->i", e2, q) * inv
            hit = ok & (u >= 0.0) & (w >= 0.0) & (u + w <= 1.0) & (t > 1e-9)
            votes[i] += int(np.count_nonzero(hit)) & 1
    return votes >= 2


def _peak_rss_mb():
    """Peak working set of this process in MiB (Windows), else ``float('nan')``."""
    try:  # pragma: no cover - platform specific
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        lib = ctypes.WinDLL("psapi.dll")
        lib.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_PMC), wintypes.DWORD,
        ]
        lib.GetProcessMemoryInfo.restype = wintypes.BOOL
        # pseudo-handle of the current process is (HANDLE)-1
        if not lib.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(pmc), pmc.cb):
            return float("nan")
        return pmc.PeakWorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        return float("nan")


def _err_pct(measured, truth):
    return 100.0 * (measured - truth) / truth


def _selftest():
    import os
    import time

    ok_all = True

    def check(name, cond, detail):
        nonlocal ok_all
        ok_all = ok_all and bool(cond)
        print("      [%s] %s -- %s" % ("PASS" if cond else "FAIL", name, detail))

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out")
    out_dir = os.path.abspath(out_dir)

    print("=" * 78)
    print("meshlib self-test   numpy %s" % np.__version__)
    print("=" * 78)

    # ---------------------------------------------------------------- test 1
    print("\n[1] sphere  sdf = |p| - 20   bounds [-25,-25,-25]..[25,25,25]  voxel 0.5")
    t0 = time.perf_counter()
    v, t = surface_nets(_sdf_sphere, (-25, -25, -25), (25, 25, 25), 0.5)
    dt = time.perf_counter() - t0
    st = mesh_stats(v, t)
    truth = 4.0 / 3.0 * np.pi * 20.0 ** 3
    err = _err_pct(st["volume"], truth)
    print("      verts=%d tris=%d  volume=%.3f mm^3  (truth %.3f, err %+.3f%%)  %.3fs"
          % (st["n_verts"], st["n_tris"], st["volume"], truth, err, dt))
    print("      watertight=%s boundary_edges=%d nonmanifold_edges=%d degenerate=%d unused_verts=%d"
          % (st["watertight"], st["boundary_edges"], st["nonmanifold_edges"],
             st["degenerate_tris"], st["unused_verts"]))
    bb = st["bbox"]
    bbf = st["bbox_flat"]
    dev = max(abs(bbf[0] + 20), abs(bbf[1] + 20), abs(bbf[2] + 20),
              abs(bbf[3] - 20), abs(bbf[4] - 20), abs(bbf[5] - 20))
    print("      bbox=(%.4f, %.4f, %.4f)..(%.4f, %.4f, %.4f)  max |dev from +-20| = %.4f mm"
          % (bbf + (dev,)))
    check("bbox is a (min,max) pair of 3-vectors",
          len(bb) == 2 and len(bb[0]) == 3 and len(bb[1]) == 3
          and bb[0][0] == bbf[0] and bb[1][2] == bbf[5],
          "bb[0][0]=%.4f bb[1][2]=%.4f (= bbox_flat[0], bbox_flat[5])" % (bb[0][0], bb[1][2]))
    check("watertight", st["watertight"] is True, "watertight=%s" % st["watertight"])
    check("boundary_edges == 0", st["boundary_edges"] == 0, "%d" % st["boundary_edges"])
    check("nonmanifold_edges == 0", st["nonmanifold_edges"] == 0, "%d" % st["nonmanifold_edges"])
    check("volume positive", st["volume"] > 0, "%.3f mm^3" % st["volume"])
    check("volume error < 2%", abs(err) < 2.0, "%+.4f%%" % err)
    check("bbox dev < 1 voxel", dev < 0.5, "%.4f mm < 0.5 mm" % dev)
    check("no degenerate tris / unused verts",
          st["degenerate_tris"] == 0 and st["unused_verts"] == 0,
          "degenerate=%d unused=%d" % (st["degenerate_tris"], st["unused_verts"]))

    # -- explicit right-hand-rule outward-normal check (the renderer depends on it)
    vv = v[t]
    fn = np.cross(vv[:, 1] - vv[:, 0], vv[:, 2] - vv[:, 0])
    fn /= np.linalg.norm(fn, axis=1)[:, None]
    cen = vv.mean(axis=1)
    outward = np.einsum("ij,ij->i", fn, cen / np.linalg.norm(cen, axis=1)[:, None])
    frac_out = float(np.count_nonzero(outward > 0.0)) / outward.size
    print("      right-hand-rule normal . outward radial dir: min=%.4f  mean=%.4f  "
          "fraction outward = %.4f" % (outward.min(), outward.mean(), frac_out))
    check("all triangle normals point outward (sphere)", frac_out == 1.0,
          "%.4f%% of %d faces outward" % (100.0 * frac_out, outward.size))

    # ---------------------------------------------------------------- test 2
    print("\n[2] cube  sdf = max(|x|-15,|y|-10,|z|-5)   voxel 0.5   (30x20x10 = 6000 mm^3)")
    t0 = time.perf_counter()
    v2, t2 = surface_nets(lambda p: _sdf_box(p, 15.0, 10.0, 5.0),
                          (-20, -20, -20), (20, 20, 20), 0.5)
    dt2 = time.perf_counter() - t0
    s2 = mesh_stats(v2, t2)
    err2 = _err_pct(s2["volume"], 6000.0)
    print("      verts=%d tris=%d  volume=%.3f mm^3  (truth 6000, err %+.3f%%)  %.3fs"
          % (s2["n_verts"], s2["n_tris"], s2["volume"], err2, dt2))
    print("      watertight=%s boundary_edges=%d nonmanifold_edges=%d degenerate=%d unused_verts=%d"
          % (s2["watertight"], s2["boundary_edges"], s2["nonmanifold_edges"],
             s2["degenerate_tris"], s2["unused_verts"]))
    print("      bbox=(%.6f, %.6f, %.6f)..(%.6f, %.6f, %.6f)  area=%.3f mm^2 (truth 2200)"
          % (s2["bbox_flat"] + (s2["area"],)))
    check("watertight", s2["watertight"] is True, "watertight=%s" % s2["watertight"])
    check("volume error < 3%", abs(err2) < 3.0, "%+.4f%%" % err2)
    check("volume positive", s2["volume"] > 0, "%.3f mm^3" % s2["volume"])
    check("bbox matches +-half extents",
          np.allclose(s2["bbox"][0], (-15, -10, -5), atol=1e-4)
          and np.allclose(s2["bbox"][1], (15, 10, 5), atol=1e-4),
          "%.6f,%.6f,%.6f .. %.6f,%.6f,%.6f" % s2["bbox_flat"])
    check("no degenerate tris / unused verts",
          s2["degenerate_tris"] == 0 and s2["unused_verts"] == 0,
          "degenerate=%d unused=%d" % (s2["degenerate_tris"], s2["unused_verts"]))

    # ---------------------------------------------------------------- test 3
    print("\n[3] block 30x20x10 with a D4 through hole along Z   voxel 0.5")
    t0 = time.perf_counter()
    v3, t3 = surface_nets(_sdf_box_hole, (-20, -20, -10), (20, 20, 10), 0.5)
    dt3 = time.perf_counter() - t0
    s3 = mesh_stats(v3, t3)
    truth3 = 6000.0 - np.pi * 2.0 ** 2 * 10.0
    err3 = _err_pct(s3["volume"], truth3)
    print("      verts=%d tris=%d  volume=%.3f mm^3  (truth %.3f, err %+.3f%%)  %.3fs"
          % (s3["n_verts"], s3["n_tris"], s3["volume"], truth3, err3, dt3))
    print("      watertight=%s boundary_edges=%d nonmanifold_edges=%d degenerate=%d unused_verts=%d"
          % (s3["watertight"], s3["boundary_edges"], s3["nonmanifold_edges"],
             s3["degenerate_tris"], s3["unused_verts"]))
    check("watertight", s3["watertight"] is True, "watertight=%s" % s3["watertight"])
    check("no degenerate tris / unused verts",
          s3["degenerate_tris"] == 0 and s3["unused_verts"] == 0,
          "degenerate=%d unused=%d" % (s3["degenerate_tris"], s3["unused_verts"]))
    check("volume matches box-minus-hole (<3%)", abs(err3) < 3.0, "%+.4f%%" % err3)

    # -- the hole really is in the mesh: point-in-solid via ray casting
    pts = np.array([
        [0.13, 0.07, 0.0],     # r=0.15, on the hole axis  -> must be OUTSIDE the solid
        [0.11, -0.09, 4.3],    # r=0.14, on the hole axis  -> OUTSIDE
        [-1.70, 1.10, -3.70],  # r=2.02, just past the hole -> INSIDE
        [3.1, 0.2, 0.0],       # r=3.11 -> INSIDE the wall
        [0.05, 3.07, 1.3],     # r=3.07 -> INSIDE the wall
        [-7.3, 6.1, 4.03],     # r=9.51 -> INSIDE the wall
        [40.0, 0.0, 0.0],      # far outside               -> OUTSIDE
    ])
    ins = _point_in_mesh(v3, t3, pts)
    expect = np.array([False, False, True, True, True, True, False])
    print("      ray-cast point-in-solid (mesh):")
    for p, q, ex in zip(pts, ins, expect):
        print("        p=(%6.2f,%6.2f,%6.2f)  r=%5.2f  inside=%-5s (expected %s)"
              % (p[0], p[1], p[2], np.hypot(p[0], p[1]), q, ex))
    check("hole axis is empty in the mesh, wall is solid",
          bool(np.array_equal(ins, expect)),
          "%d/%d points agree with the analytic SDF" % (int((ins == expect).sum()), len(pts)))

    occ, cax = voxel_occupancy(_sdf_box_hole, (-20, -20, -10), (20, 20, 10), 0.5)
    icx, icy, icz = (int(np.argmin(np.abs(cax[a]))) for a in range(3))
    axis_col = occ[icx, icy, :]
    occ_axis = bool(axis_col.any())
    print("      voxel_occupancy %s  centre voxel (%d,%d,%d) at (%.2f,%.2f,%.2f)"
          % (occ.shape, icx, icy, icz, cax[0][icx], cax[1][icy], cax[2][icz]))
    print("      occupied fraction=%.4f   any occupied voxel on the hole axis column = %s"
          % (occ.mean(), occ_axis))
    check("voxel_occupancy: hole axis empty", occ_axis is False, "axis column occupied=%s" % occ_axis)

    # -- STL round trip
    stl3 = os.path.join(out_dir, "selftest_meshlib_hole.stl")
    write_stl(stl3, v3, t3)
    rv, rt = read_stl(stl3)
    rs = mesh_stats(rv, rt)
    print("      STL round trip: %s (%.2f MB)  verts=%d tris=%d volume=%.3f watertight=%s"
          % (stl3, os.path.getsize(stl3) / 1e6, rs["n_verts"], rs["n_tris"],
             rs["volume"], rs["watertight"]))
    check("STL round trip preserves the mesh",
          rs["n_tris"] == s3["n_tris"] and rs["watertight"] is True
          and abs(_err_pct(rs["volume"], s3["volume"])) < 0.01,
          "dvolume=%+.5f%%  watertight=%s" % (_err_pct(rs["volume"], s3["volume"]),
                                              rs["watertight"]))

    # ---------------------------------------------------------------- test 4
    print("\n[4] performance: box 170x90x25 mm, voxel 0.4")
    box4 = lambda p: _sdf_box(p, 85.0, 45.0, 12.5)
    lo4, hi4, vox4 = (-87, -47, -15), (87, 47, 15), 0.4
    n4 = np.ceil((np.array(hi4, float) - np.array(lo4, float)) / vox4 - 1e-9).astype(int) + 1
    print("      grid %d x %d x %d = %.2fM points, %.2fM cells"
          % (n4[0], n4[1], n4[2], np.prod(n4) / 1e6, np.prod(n4 - 1) / 1e6))
    t0 = time.perf_counter()
    v4, t4 = surface_nets(box4, lo4, hi4, vox4)
    dt4 = time.perf_counter() - t0
    s4 = mesh_stats(v4, t4)
    err4 = _err_pct(s4["volume"], 170.0 * 90.0 * 25.0)
    print("      verts=%d tris=%d  volume=%.3f mm^3 (truth 382500, err %+.4f%%)"
          % (s4["n_verts"], s4["n_tris"], s4["volume"], err4))
    print("      watertight=%s boundary_edges=%d nonmanifold_edges=%d"
          % (s4["watertight"], s4["boundary_edges"], s4["nonmanifold_edges"]))
    print("      surface_nets wall time = %.2f s     peak RSS = %.1f MiB"
          % (dt4, _peak_rss_mb()))
    check("surface_nets < 60 s", dt4 < 60.0, "%.2f s" % dt4)
    check("watertight", s4["watertight"] is True, "watertight=%s" % s4["watertight"])
    check("volume error < 1%", abs(err4) < 1.0, "%+.4f%%" % err4)

    # -- stl write of the big mesh (sanity + speed)
    stl4 = os.path.join(out_dir, "selftest_meshlib_box.stl")
    t0 = time.perf_counter()
    write_stl(stl4, v4, t4)
    dt_w = time.perf_counter() - t0
    print("      write_stl -> %s (%.2f MB) in %.2f s" % (stl4, os.path.getsize(stl4) / 1e6, dt_w))

    # ---------------------------------------------------------------- test 5
    print("\n[5] interface conformance (shapes / dtypes / broadcasts / guards)")
    check("verts float64 (N,3), tris int32 (M,3)",
          v3.dtype == np.float64 and v3.ndim == 2 and v3.shape[1] == 3
          and t3.dtype == np.int32 and t3.ndim == 2 and t3.shape[1] == 3,
          "verts %s %s, tris %s %s" % (v3.dtype, v3.shape, t3.dtype, t3.shape))

    F1, ax1 = sample_grid(_sdf_sphere, (-25, -25, -25), (25, 25, 25), 0.5)
    exp_n = (int(np.ceil(50 / 0.5)) + 1,) * 3
    check("sample_grid count formula nx = ceil((hi-lo)/voxel)+1",
          F1.shape == exp_n and ax1[0].size == exp_n[0]
          and ax1[0][0] == -25.0 and abs(ax1[0][-1] - 25.0) < 1e-12,
          "F%s axes %s, xs[-1]=%.6f" % (F1.shape, [a.size for a in ax1], ax1[0][-1]))

    Fn, axn = sample_grid(_sdf_sphere, (-25, -26, -27), (25, 24, 23), 1.0)
    check("sample_grid on a non-cubic, non-cubic-divisible box",
          Fn.shape == (51, 51, 51), "F%s" % (Fn.shape,))

    # arbitrary broadcast shapes must work (0-d probe, (4,) probe, (5,7,3) probe)
    probes = [
        np.array([1.0, 2.0, 3.0]),
        np.zeros((4, 3)),
        np.zeros((5, 7, 3)) + 1.0,
    ]
    ok_bc = all(np.asarray(_sdf_sphere(q)).shape == q.shape[:-1] for q in probes)
    val0 = float(_sdf_sphere(np.array([0.0, 0.0, 0.0])))
    check("sdf broadcast over (3,), (4,3), (5,7,3)", ok_bc and abs(val0 + 20.0) < 1e-12,
          "|0|-20 = %.1f" % val0)

    hit = False
    try:
        surface_nets(_sdf_sphere, (-5, -5, -5), (5, 5, 5), 0.5)
    except ValueError as exc:
        hit = "boundary" in str(exc)
    check("surface touching the sampling boundary raises ValueError", hit,
          "raised=%s" % hit)

    occ_e, ax_e = voxel_occupancy(_sdf_sphere, (-25, -25, -25), (25, 25, 25), 0.5)
    occ_true = 4.0 / 3.0 * np.pi * 20.0 ** 3 / 0.5 ** 3
    print("      voxel_occupancy: occ%s occupied=%d (analytic ~%.0f, err %+.2f%%)"
          % (occ_e.shape, int(occ_e.sum()), occ_true,
             _err_pct(float(occ_e.sum()), occ_true)))
    check("voxel_occupancy counts match the analytic volume (<1%)",
          abs(_err_pct(float(occ_e.sum()), occ_true)) < 1.0,
          "%.0f vs %.0f" % (float(occ_e.sum()), occ_true))

    empty_v, empty_t = surface_nets(lambda p: np.full(p.shape[:-1], 1.0),
                                    (-10, -10, -10), (10, 10, 10), 1.0)
    es = mesh_stats(empty_v, empty_t)
    check("empty SDF -> empty mesh, no crash",
          empty_v.shape == (0, 3) and empty_t.shape == (0, 3)
          and es["n_tris"] == 0 and es["volume"] == 0.0,
          "verts%s tris%s volume=%s" % (empty_v.shape, empty_t.shape, es["volume"]))

    # ------------------------------------------- test 6: known limitation
    print("\n[6] KNOWN LIMITATION -- ambiguous (checkerboard) cell faces")
    print("    A cell face whose 4 corners alternate inside/outside makes the surface")
    print("    cross that face twice, so all 4 of its grid edges emit a quad joining the")
    print("    same two dual vertices -> that edge lands in 4 triangles.  Measured:")
    from collections import namedtuple
    probe = namedtuple("probe", "name sdf lo hi vox truth")
    probes = [
        probe("sphere r=20 @0.5  (clean)", _sdf_sphere, (-25,) * 3, (25,) * 3, 0.5,
              4.0 / 3.0 * np.pi * 20.0 ** 3),
        probe("box+hole @0.5     (clean)", _sdf_box_hole, (-20, -20, -10), (20, 20, 10),
              0.5, 6000.0 - np.pi * 4.0 * 10.0),
        probe("gyroid sheet @0.45 (ambiguous)",
              lambda p: np.maximum(
                  np.abs(np.sin(0.35 * p[..., 0]) * np.cos(0.35 * p[..., 1])
                         + np.sin(0.35 * p[..., 1]) * np.cos(0.35 * p[..., 2])
                         + np.sin(0.35 * p[..., 2]) * np.cos(0.35 * p[..., 0])) - 0.35,
                  np.linalg.norm(p, axis=-1) - 18.0),
              (-22,) * 3, (22,) * 3, 0.45, None),
    ]
    print("      %-30s %8s %8s %8s %8s" % ("case", "tris", "bnd", "nonmf", "degen"))
    for pr in probes:
        pv, pt = surface_nets(pr.sdf, pr.lo, pr.hi, pr.vox)
        ps = mesh_stats(pv, pt)
        print("      %-30s %8d %8d %8d %8d" % (pr.name, ps["n_tris"],
                                               ps["boundary_edges"],
                                               ps["nonmanifold_edges"],
                                               ps["degenerate_tris"]))
        check("%s: boundary_edges == 0 (mesh is closed)" % pr.name.split()[0],
              ps["boundary_edges"] == 0, "%d" % ps["boundary_edges"])
    print("      -> boundary_edges is ALWAYS 0; only manifoldness can fail, and only on")
    print("         ambiguous faces.  Resolving it needs >1 vertex per cell, which the")
    print("         SPEC's one-vertex-per-cell rule forbids -- reported, not hidden.")

    print("\n" + "=" * 78)
    print("OVERALL: %s" % ("ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED"))
    print("=" * 78)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
