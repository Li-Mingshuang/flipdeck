"""装配位姿 + 间隙/干涉检查。

位姿定义（见 params.py）：
    θ = 合盖角（deck<->lid，绕 X 轴，轴心 (HINGE_Y, HINGE_Z)）
    φ = 托盘翻转角（lid<->cradle，绕 X 轴，轴心 (PIVOT_Y, PIVOT_Z)）
    θ=0/φ=0    合盖 + 手机朝内（游戏收纳）
    θ=110/φ=0  展开游戏
    θ=0/φ=180  合盖 + 手机屏幕朝上（当普通手机用）
"""

from __future__ import annotations
import numpy as np

from . import params as P
from .sdf import M4, R_x, xform

# 各零件在自身局部坐标系里的粗略包围盒（含凸台/铰链）
LOCAL_BBOX = {
    "deck":   ((-93.0, -47.0, -1.0), (93.0, 62.0, 37.0)),
    "lid":    ((-92.0, -97.0, -10.0), (92.0, 11.0, 12.0)),
    # cradle 现在多了朝手机一侧的围边（往下 7.4mm），包围盒要跟着放大
    "cradle": ((-90.0, -92.0, -9.0), (90.0, -10.0, 17.0)),
    "phone":  None,   # 由机型算
}


def phone_bbox(phone: P.Phone):
    z_back = P.PIVOT_Z - P.PLATE_T / 2 + P.PHONE_RECESS - P.PHONE_GAP
    return ((-phone.L / 2, P.PIVOT_Y - phone.W / 2, z_back - phone.T),
            (phone.L / 2, P.PIVOT_Y + phone.W / 2, z_back))


def pose(name: str, theta: float = 0.0, phi: float = 0.0) -> np.ndarray:
    """返回零件局部 -> 设备坐标系的 4x4 变换。

    注意：lid 的局部原点本来就在合盖转轴上，所以 T_lid = R(θ) 后平移到轴心；
    cradle 的局部原点不在托盘轴上（轴在 (PIVOT_Y, PIVOT_Z)），所以要绕该点旋转：
        p_out = R·(p_in - pivot) + pivot
    """
    axis = np.array([0.0, P.HINGE_Y, P.HINGE_Z])
    pivot = np.array([0.0, P.PIVOT_Y, P.PIVOT_Z])
    Rt = R_x(-theta)
    Rc = R_x(-phi)
    T_lid = M4(Rt, axis)
    T_cradle = M4(Rc, pivot - Rc @ pivot)
    if name == "deck":
        return np.eye(4)
    if name == "lid":
        return T_lid
    if name in ("cradle", "phone"):
        return T_lid @ T_cradle
    if name in ("caps", "levers", "caps_abxy", "cap_dpad", "caps_small", "lever_l",
                "lever_r", "hinge_pin"):
        return np.eye(4)
    raise KeyError(name)


def local_bbox(name: str, phone: P.Phone | None = None):
    if name == "phone":
        return phone_bbox(phone or P.PHONES[P.DEFAULT_PHONE])
    return LOCAL_BBOX[name]


def world_aabb(name: str, theta: float, phi: float, phone: P.Phone | None = None):
    lo, hi = local_bbox(name, phone)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0])
                        for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    T = pose(name, theta, phi)
    w = corners @ T[:3, :3].T + T[:3, 3]
    return w.min(axis=0), w.max(axis=0)


# --------------------------------------------------------------------------- 间隙计算
def signed_gap(fA, aabbA, fB, aabbB, margin: float = 5.0, voxel: float = 1.2,
               max_points: int = 2_500_000, refine: bool = True):
    """两实体的分离量 sep：sep<0 表示重叠（≈ -穿透深度/2），sep>0 表示间隙/2。

    用 min over space of max(dA, dB) 才是正确的"分离量"：
      - 两个实体相交 <=> 存在点使 dA<0 且 dB<0 <=> min max(dA,dB) < 0
      - 不相交时 min max = 两个表面间最大内切球半径 = 间隙/2
    返回 (sep, pos)。报告里换算成 2*sep（负值≈穿透深度）。
    """
    loA, hiA = np.asarray(aabbA[0]), np.asarray(aabbA[1])
    loB, hiB = np.asarray(aabbB[0]), np.asarray(aabbB[1])
    lo = np.maximum(loA, loB) - margin
    hi = np.minimum(hiA, hiB) + margin
    if np.any(hi <= lo):
        d = np.maximum(loA - hiB, loB - hiA)
        return 0.5 * float(np.linalg.norm(np.maximum(d, 0.0))), None

    def scan(lo_, hi_, vox_):
        n = np.ceil((hi_ - lo_) / vox_).astype(int) + 1
        xs = lo_[0] + np.arange(n[0]) * vox_
        ys = lo_[1] + np.arange(n[1]) * vox_
        zs = lo_[2] + np.arange(n[2]) * vox_
        gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
        pts = np.stack([gx, gy, gz], axis=-1)
        d = np.maximum(fA(pts), fB(pts))
        i = int(np.argmin(d))
        pos = (float(gx.ravel()[i]), float(gy.ravel()[i]), float(gz.ravel()[i]))
        return float(d.ravel()[i]), pos

    v = voxel
    while True:
        n = np.ceil((hi - lo) / v).astype(int) + 1
        if int(np.prod(n)) <= max_points or v > 6.0:
            break
        v *= 1.25
    sep, pos = scan(lo, hi, v)

    if refine and pos is not None:
        p = np.asarray(pos)
        r = 2.0 * v
        for _ in range(3):
            lo2 = np.maximum(lo, p - r)
            hi2 = np.minimum(hi, p + r)
            if np.any(hi2 <= lo2):
                break
            s2, p2 = scan(lo2, hi2, max(0.12, v / 4.0))
            if p2 is None:
                break
            if s2 < sep:
                sep, pos = s2, p2
            p = np.asarray(pos)
            v = max(0.12, v / 4.0)
            r = 2.0 * v
    return sep, pos


def pair_gap(build, nameA, nameB, theta, phi, phone=None, voxel=1.2, margin=5.0):
    """build: dict name -> sdf 构造函数（局部坐标系）。返回 (2*sep, pos)。"""
    fA = xform(build[nameA](), pose(nameA, theta, phi))
    fB = xform(build[nameB](), pose(nameB, theta, phi))
    aA = world_aabb(nameA, theta, phi, phone)
    aB = world_aabb(nameB, theta, phi, phone)
    sep, pos = signed_gap(fA, aA, fB, aB, margin=margin, voxel=voxel)
    return 2.0 * sep, pos


def run_pose_checks(build: dict, phone: P.Phone | None = None,
                    verbose: bool = True) -> list[dict]:
    """关键姿态 + 翻转全程扫描的间隙报告。"""
    ph = phone or P.PHONES[P.DEFAULT_PHONE]
    rows: list[dict] = []

    def add(tag, a, b, th, ph_, voxel):
        gap, pos = pair_gap(build, a, b, th, ph_, ph, voxel=voxel)
        rows.append(dict(tag=tag, pair=f"{a}-{b}", theta=th, phi=ph_,
                         gap=gap, pos=pos, voxel=voxel))
        if verbose:
            print(f"  {tag:26s} {a:7s}-{b:7s} θ={th:6.1f} φ={ph_:6.1f} "
                  f"gap={gap:+7.2f}mm  @ {pos}")

    # 1. 关键姿态
    add("合盖·游戏(收纳)", "deck", "lid", 0.0, 0.0, 0.8)
    add("合盖·游戏(收纳)", "deck", "phone", 0.0, 0.0, 0.8)
    add("合盖·游戏(收纳)", "lid", "cradle", 0.0, 0.0, 0.8)
    add("展开·游戏", "deck", "lid", P.OPEN_GAME, 0.0, 0.8)
    add("展开·游戏", "deck", "cradle", P.OPEN_GAME, 0.0, 0.8)
    add("展开·游戏", "deck", "phone", P.OPEN_GAME, 0.0, 0.8)
    add("合盖·手机模式", "deck", "phone", 0.0, 180.0, 0.8)
    add("合盖·手机模式", "deck", "cradle", 0.0, 180.0, 0.8)
    add("合盖·手机模式", "lid", "cradle", 0.0, 180.0, 0.8)

    # 2. 翻转全程扫描（在开盖姿态下换形态）
    worst = None
    for phi in np.arange(0.0, 180.1, 10.0):
        for a, b in (("lid", "cradle"), ("deck", "cradle"), ("deck", "phone"), ("lid", "phone")):
            gap, pos = pair_gap(build, a, b, P.OPEN_GAME, float(phi), ph, voxel=1.6, margin=4.0)
            if a == "lid" and b == "cradle":
                rows.append(dict(tag=f"翻转扫描 θ=110", pair=f"{a}-{b}", theta=P.OPEN_GAME,
                                 phi=float(phi), gap=gap, pos=pos, voxel=1.6))
            if worst is None or gap < worst[0]:
                worst = (gap, f"{a}-{b}", float(phi), pos)
    if verbose:
        print(f"  翻转扫描最坏：gap={worst[0]:+.2f}mm  {worst[1]}  φ={worst[2]:.0f}  @ {worst[3]}")
    rows.append(dict(tag="翻转扫描(全部件最坏)", pair=worst[1], theta=P.OPEN_GAME,
                     phi=worst[2], gap=worst[0], pos=worst[3], voxel=1.6))

    # 3. 合盖过程中手机是否扫到 deck（手机模式合盖）
    for th in (30.0, 60.0, 90.0):
        gap, pos = pair_gap(build, "deck", "phone", th, 180.0, ph, voxel=1.6, margin=4.0)
        rows.append(dict(tag="手机模式合盖过程", pair="deck-phone", theta=th, phi=180.0,
                         gap=gap, pos=pos, voxel=1.6))
        if verbose:
            print(f"  手机模式合盖 θ={th:5.1f}  deck-phone gap={gap:+7.2f}mm @ {pos}")

    return rows


def verdict(rows: list[dict], tol: float = -0.15) -> tuple[bool, list[dict]]:
    """gap < tol 视为真干涉（-0.15mm 容差，因为 SDF 体素化有误差）。"""
    bad = [r for r in rows if r["gap"] < tol]
    return (len(bad) == 0), bad
