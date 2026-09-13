"""极简 SDF（有符号距离场）内核：只用 numpy，全部向量化。

约定：f(p) -> sdf，p 形状 (...,3)，单位 mm，f<0 在实体内部。
零等值面即最终表面，所以 primitive 的"表面尺寸"必须写准（不要把 clearance 算错）。
"""

from __future__ import annotations
import numpy as np

Array = np.ndarray


# --------------------------------------------------------------------------- 基础工具
def _norm(v: Array) -> Array:
    return np.sqrt(np.einsum("...i,...i->...", v, v))


def box(size, center=(0.0, 0.0, 0.0), r: float = 0.0):
    """圆角长方体。size=(sx,sy,sz) 是外形尺寸，r 是棱边圆角半径。"""
    h = np.asarray(size, dtype=np.float64) * 0.5
    c = np.asarray(center, dtype=np.float64)
    r = float(r)

    def f(p: Array) -> Array:
        q = np.abs(p - c) - (h - r)
        outside = _norm(np.maximum(q, 0.0))
        inside = np.minimum(np.max(q, axis=-1), 0.0)
        return outside + inside - r

    return f


def cyl(r: float, h: float, center=(0.0, 0.0, 0.0), axis: str = "z"):
    """圆柱（平头）。axis 指定轴向 'x'|'y'|'z'。"""
    c = np.asarray(center, dtype=np.float64)
    ai = {"x": 0, "y": 1, "z": 2}[axis]
    others = [i for i in (0, 1, 2) if i != ai]

    def f(p: Array) -> Array:
        d = p - c
        radial = _norm(d[..., others])
        axial = np.abs(d[..., ai])
        return np.maximum(radial - r, axial - h * 0.5)

    return f


def cone(r1: float, r2: float, h: float, center=(0, 0, 0), axis: str = "z"):
    """圆台（用于倒角/锥形唇口）。r1 在 -axis 端，r2 在 +axis 端。"""
    c = np.asarray(center, dtype=np.float64)
    ai = {"x": 0, "y": 1, "z": 2}[axis]
    others = [i for i in (0, 1, 2) if i != ai]
    hh = h * 0.5

    def f(p: Array) -> Array:
        d = p - c
        za = d[..., ai]
        rad = _norm(d[..., others])
        # 径向缩放：t=0 -> r1, t=1 -> r2
        t = np.clip((za + hh) / h, 0.0, 1.0)
        rr = r1 + (r2 - r1) * t
        dr = rad - rr
        dz = np.abs(za) - hh
        return np.maximum(dr, dz)

    return f


def sphere(r: float, center=(0.0, 0.0, 0.0)):
    c = np.asarray(center, dtype=np.float64)

    def f(p: Array) -> Array:
        return _norm(p - c) - r

    return f


def capsule(a, b, r: float):
    """两个点之间的胶囊（做圆角/柔性臂用）。"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    ba = b - a
    l2 = float(np.dot(ba, ba))

    def f(p: Array) -> Array:
        pa = p - a
        t = np.clip(np.einsum("...i,i->...", pa, ba) / l2, 0.0, 1.0)[..., None]
        return _norm(pa - t * ba) - r

    return f


# --------------------------------------------------------------------------- 布尔/变换
def union(*fs):
    def f(p: Array) -> Array:
        out = fs[0](p)
        for g in fs[1:]:
            np.minimum(out, g(p), out=out)
        return out

    return f


def inter(*fs):
    def f(p: Array) -> Array:
        out = fs[0](p)
        for g in fs[1:]:
            np.maximum(out, g(p), out=out)
        return out

    return f


def diff(f, *cutters):
    def g(p: Array) -> Array:
        out = f(p)
        for c in cutters:
            np.maximum(out, -c(p), out=out)
        return out

    return g


def translate(f, t):
    t = np.asarray(t, dtype=np.float64)

    def g(p: Array) -> Array:
        return f(p - t)

    return g


def xform(f, M):
    """把 M（4x4 或 (R,t)）作用到实体的局部坐标系上：世界点 p 先变换回局部再求值。
    M 表示"局部 -> 世界"的刚体变换。"""
    if isinstance(M, tuple):
        R, t = M
    else:
        M = np.asarray(M, dtype=np.float64)
        R, t = M[:3, :3], M[:3, 3]

    def g(p: Array) -> Array:
        return f((p - t) @ R)

    return g


def mirror_x(f):
    """以 X=0 平面镜像（返回的函数把 +x 侧镜像到 -x）。"""

    def g(p: Array) -> Array:
        q = p.copy()
        q[..., 0] = -q[..., 0]
        return f(q)

    return g


def repeat_x(f, period: float, count: int, start: float = 0.0):
    """沿 X 复制 count 份（间隔 period，从 start 起）。"""
    return union(*[translate(f, (start + i * period, 0.0, 0.0)) for i in range(count)])


# --------------------------------------------------------------------------- 刚体变换构造
def R_x(deg: float) -> Array:
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def R_y(deg: float) -> Array:
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def R_z(deg: float) -> Array:
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def M4(R=None, t=(0.0, 0.0, 0.0)) -> Array:
    M = np.eye(4, dtype=np.float64)
    if R is not None:
        M[:3, :3] = np.asarray(R, dtype=np.float64)
    M[:3, 3] = np.asarray(t, dtype=np.float64)
    return M


def compose(*Ms) -> Array:
    out = np.eye(4, dtype=np.float64)
    for M in Ms:
        out = out @ (M if not isinstance(M, tuple) else M4(M[0], M[1]))
    return out


# --------------------------------------------------------------------------- 调试：切面 ASCII 图
def ascii_slice(f, axis: str = "z", level: float = 0.0,
                x0=-95, x1=95, y0=-60, y1=60, z0=-40, z1=50, res=1.0,
                width: int = 150, chars: str = " .:-=+*#%@") -> str:
    """把某个平面切片的 SDF<0 区域打成 ASCII 图，用于不开网格器就能肉眼查结构。"""
    ai = {"x": 0, "y": 1, "z": 2}[axis]
    if axis == "z":
        us, vs = np.arange(x0, x1 + 1e-9, res), np.arange(y0, y1 + 1e-9, res)
        uu, vv = np.meshgrid(us, vs, indexing="ij")
        pts = np.zeros(uu.shape + (3,))
        pts[..., 0], pts[..., 1] = uu, vv
        pts[..., 2] = level
        ulab, vlab = "X", "Y"
    elif axis == "y":
        us, vs = np.arange(x0, x1 + 1e-9, res), np.arange(z0, z1 + 1e-9, res)
        uu, vv = np.meshgrid(us, vs, indexing="ij")
        pts = np.zeros(uu.shape + (3,))
        pts[..., 0], pts[..., 2] = uu, vv
        pts[..., 1] = level
        ulab, vlab = "X", "Z"
    else:
        us, vs = np.arange(y0, y1 + 1e-9, res), np.arange(z0, z1 + 1e-9, res)
        uu, vv = np.meshgrid(us, vs, indexing="ij")
        pts = np.zeros(uu.shape + (3,))
        pts[..., 1], pts[..., 2] = uu, vv
        pts[..., 0] = level
        ulab, vlab = "Y", "Z"
    d = f(pts)
    # 用距离做灰度，实体内部用最深字符
    scale = np.clip(d / (6.0 * res), -1, 1)
    idx = ((1.0 - scale) * 0.5 * (len(chars) - 1)).astype(int)
    idx = np.where(d < 0, idx, 0)
    step = max(1, int(np.ceil(uu.shape[0] / width)))
    lines = [f"# slice {axis}={level:.1f}  ({ulab}->横, {vlab}->纵)"]
    for j in range(uu.shape[1] - 1, -1, -step):
        lines.append("".join(chars[idx[i, j]] for i in range(0, uu.shape[0], step)))
    return "\n".join(lines)
