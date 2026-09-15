"""零件几何：deck（控制下半）/ lid（盖框）/ cradle（翻转托盘）/ phone（校验用）/ caps（键帽）

所有零件都在自己的局部坐标系里建模，位姿由 poses.py 提供：
    deck   局部 = 设备坐标系
    lid    局部 = 合盖转轴轴线为原点（z_l=0 在轴线高度，y_l=0 在轴线 y）
    cradle 局部 = lid 局部在 φ=0（游戏位）时的构型；翻转 = 绕托盘轴转 φ
"""

from __future__ import annotations
import numpy as np

from . import params as P
from .sdf import (box, cone, cyl, diff, M4, R_x, sphere, translate, union, xform)


# --------------------------------------------------------------------------- 小工具
def prism(w: float, d: float, r: float, z0: float, z1: float, cx: float = 0.0, cy: float = 0.0):
    """XY 圆角矩形 + Z 向 [z0,z1] 拉伸（外壳主流形状）。"""
    hx, hy = w * 0.5 - r, d * 0.5 - r
    zc, hz = (z0 + z1) * 0.5, (z1 - z0) * 0.5

    def f(p):
        qx = np.abs(p[..., 0] - cx) - hx
        qy = np.abs(p[..., 1] - cy) - hy
        d2 = (np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2)
              + np.minimum(np.maximum(qx, qy), 0.0) - r)
        return np.maximum(d2, np.abs(p[..., 2] - zc) - hz)

    return f


def prism_ring(w_out, d_out, r_out, w_in, d_in, r_in, z0, z1, cx=0.0, cy=0.0):
    return diff(prism(w_out, d_out, r_out, z0, z1, cx, cy),
                prism(w_in, d_in, r_in, z0 - 1.0, z1 + 1.0, cx, cy))


def ring_annulus(r_out, r_in, h, center, axis="z"):
    return diff(cyl(r_out, h, center, axis), cyl(r_in, h + 2, center, axis))


def bump_sphere(face_x: float, protrude: float, direction: float, radius: float = 1.4):
    """在垂直于 X 的面上做球冠凸点。
    face_x: 面所在 x；direction: 凸点指向 (+1/-1，沿 X)；protrude: 凸出高度。
    返回球心 x。"""
    return face_x - direction * (radius - protrude)


def _dir_yz(phi_deg: float) -> tuple[float, float]:
    """局部参考方向 -z 绕 X 轴转 φ 后在 (y,z) 平面上的方向（与位姿 R_x(-φ) 一致）。"""
    R = R_x(-phi_deg)
    v = R @ np.array([0.0, 0.0, -1.0])
    return float(v[1]), float(v[2])


# --------------------------------------------------------------------------- deck
def build_deck(phone=None):
    # --- 外壳：三段叠出上下收边
    shell = union(
        prism(P.DECK_W - 3.0, P.DECK_D - 3.0, 5.0, 0.0, 1.6),
        prism(P.DECK_W, P.DECK_D, 6.5, 1.3, P.DECK_H - 1.5),
        prism(P.DECK_W - 3.2, P.DECK_D - 3.2, 5.0, P.DECK_H - 1.8, P.DECK_H),
    )

    ins = P.CAVITY_INSET
    cavity = prism(P.DECK_W - 2 * ins, P.DECK_D - 2 * ins, 3.0, P.FLOOR, P.CAVITY_TOP)
    well = prism(P.WELL_W, P.WELL_D, P.WELL_R, P.WELL_FLOOR, P.DECK_H + 2.0)

    # --- 摇杆口袋 + 螺纹底孔
    stick_cuts, stick_pilots = [], []
    for sx in (P.STICK_X, -P.STICK_X):
        stick_cuts.append(cyl(P.STICK_POCKET_D / 2, P.WELL_FLOOR - P.STICK_POCKET_FLOOR + 1.0,
                              (sx, P.STICK_Y, (P.WELL_FLOOR + P.STICK_POCKET_FLOOR) / 2)))
        for k in range(4):
            a = np.deg2rad(45 + 90 * k)
            stick_pilots.append(cyl(P.STICK_SCREW_PILOT / 2, P.STICK_SCREW_DEPTH,
                                    (sx + P.STICK_SCREW_BC / 2 * np.cos(a),
                                     P.STICK_Y + P.STICK_SCREW_BC / 2 * np.sin(a),
                                     P.STICK_POCKET_FLOOR - P.STICK_SCREW_DEPTH / 2 + 0.02)))

    # --- D-pad / ABXY / 小按键（穿过控制面板）
    z_mid = (P.CAVITY_TOP + P.WELL_FLOOR) / 2
    h_plate = P.WELL_FLOOR - P.CAVITY_TOP + 1.0
    dpad = cyl(P.DPAD_HOLE / 2, h_plate, (P.DPAD_X, P.DPAD_Y, z_mid))
    abxy = [cyl(P.ABXY_HOLE / 2, h_plate, (P.ABXY_CX + dx, P.ABXY_CY + dy, z_mid))
            for dx, dy in ((P.ABXY_R, 0), (-P.ABXY_R, 0), (0, P.ABXY_R), (0, -P.ABXY_R))]
    smalls = [cyl(P.SMALL_HOLE / 2, h_plate, (sx, P.SMALL_Y, z_mid)) for sx in P.SMALL_XS]

    # --- 肩键开槽（后壁）
    shoulder = [box((P.SHOULDER_X[1] - P.SHOULDER_X[0], 4.0, P.SHOULDER_Z[1] - P.SHOULDER_Z[0] - 2.0),
                    center=(sx * (P.SHOULDER_X[1] + P.SHOULDER_X[0]) / 2, P.DECK_BACK - 0.6,
                            (P.SHOULDER_Z[0] + P.SHOULDER_Z[1]) / 2)) for sx in (1, -1)]

    # --- 接口
    usbc = box((4.0, P.USBC_CUT[0], P.USBC_CUT[1]), center=(-P.DECK_W / 2, P.USBC_Y, P.USBC_Z))
    power = box((4.0, P.POWER_CUT[0], P.POWER_CUT[1]), center=(P.DECK_W / 2, P.POWER_Y, P.POWER_Z))

    # --- 贴合面密封条槽（与上收边同心地内缩，保证外壁 ~1.5mm 实体、圆角处不被切穿）
    g_in = P.GASKET_INSET + P.GASKET_W
    gasket = prism_ring(P.DECK_W - 2 * P.GASKET_INSET, P.DECK_D - 2 * P.GASKET_INSET, P.GASKET_R_OUT,
                        P.DECK_W - 2 * g_in, P.DECK_D - 2 * g_in,
                        max(1.5, P.GASKET_R_OUT - P.GASKET_W),
                        P.DECK_H - P.GASKET_DEPTH, P.DECK_H + 1.0)

    # --- 合盖磁铁坑（两侧导轨底面对应位置）
    mags = [cyl(P.LATCH_MAG_D / 2, P.LATCH_MAG_DEPTH,
                (sx * (P.LID_RAIL_X[0] + P.LID_RAIL_X[1]) / 2, my,
                 P.DECK_H - P.LATCH_MAG_DEPTH / 2 + 0.02))
            for sx in (1, -1) for my in P.LATCH_MAG_Y]

    deck = diff(shell, cavity, well, *stick_cuts, *stick_pilots, dpad, *abxy, *smalls,
                *shoulder, usbc, power, gasket, *mags)

    # --- 内部 PCB 柱
    bosses = [cyl(P.PCB_BOSS_D / 2, P.PCB_BOSS_TOP - P.FLOOR, (bx, by, (P.PCB_BOSS_TOP + P.FLOOR) / 2))
              for bx, by in P.PCB_BOSS_XY]
    pilots = [cyl(P.PCB_BOSS_PILOT / 2, 4.0, (bx, by, P.PCB_BOSS_TOP - 2.0 + 0.02))
              for bx, by in P.PCB_BOSS_XY]

    # --- 铰链：两侧凸耳 + 腹板（中间区域必须留空，否则挡托盘翻转）
    hx0, hx1 = P.HINGE_X_DECK
    wx0, wx1 = P.HINGE_WEB_X
    hinges, bumps = [], []
    for sx in (1, -1):
        cx = sx * (hx0 + hx1) / 2
        knuckle = cyl(P.HINGE_BARREL_R, hx1 - hx0, (cx, P.HINGE_Y, P.HINGE_Z), axis="x")
        wcx = sx * (wx0 + wx1) / 2
        ww = wx1 - wx0
        low = box((ww, 16.0, 5.8), center=(wcx, 45.0, 16.9))          # 贴住后壁
        riser = box((ww, 10.0, 12.0), center=(wcx, 49.0, 23.0))       # 撑到凸耳
        hinges.append(union(knuckle, low, riser))
        # 合盖角定位凸点：凸耳端面 |x|=hx1，朝外(+x*sx)，方向 -y，半径 HINGE_DETENT_R
        dy, dz = _dir_yz(0.0)
        bx = bump_sphere(sx * hx1, 0.8, direction=float(sx))
        bumps.append(sphere(1.4, (bx, P.HINGE_Y + dy * P.HINGE_DETENT_R,
                                  P.HINGE_Z + dz * P.HINGE_DETENT_R)))

    pin_bore = cyl(P.HINGE_BORE / 2, 340.0, (0.0, P.HINGE_Y, P.HINGE_Z), axis="x")

    return diff(union(deck, *bosses, *hinges, *bumps), *pilots, pin_bore)


# --------------------------------------------------------------------------- lid
def _deck_shell_prisms():
    """底盘外壳三段收边（游戏底座与键盘底座共用）。"""
    return [
        prism(P.DECK_W - 3.0, P.DECK_D - 3.0, 5.0, 0.0, 1.6),
        prism(P.DECK_W, P.DECK_D, 6.5, 1.3, P.DECK_H - 1.5),
        prism(P.DECK_W - 3.2, P.DECK_D - 3.2, 5.0, P.DECK_H - 1.8, P.DECK_H),
    ]


def _deck_hinge_and_faces():
    """铰链（两侧凸耳 + 腹板）+ 合盖磁铁坑 + 密封条槽下的共用件。"""
    hx0, hx1 = P.HINGE_X_DECK
    wx0, wx1 = P.HINGE_WEB_X
    hinges = []
    for sx in (1, -1):
        cx = sx * (hx0 + hx1) / 2
        wcx = sx * (wx0 + wx1) / 2
        ww = wx1 - wx0
        hinges.append(union(
            cyl(P.HINGE_BARREL_R, hx1 - hx0, (cx, P.HINGE_Y, P.HINGE_Z), axis="x"),
            box((ww, 16.0, 5.8), center=(wcx, 45.0, P.HINGE_Z - 9.1)),
            box((ww, 10.0, 12.0), center=(wcx, 49.0, P.HINGE_Z - 3.0)),
        ))
    pin = cyl(P.HINGE_BORE / 2, 340.0, (0.0, P.HINGE_Y, P.HINGE_Z), axis="x")
    mags = [cyl(P.LATCH_MAG_D / 2, P.LATCH_MAG_DEPTH,
                (sx * (P.LID_RAIL_X[0] + P.LID_RAIL_X[1]) / 2, my,
                 P.DECK_H - P.LATCH_MAG_DEPTH / 2 + 0.02))
            for sx in (1, -1) for my in P.LATCH_MAG_Y]
    gasket = prism_ring(P.DECK_W - 2 * P.GASKET_INSET, P.DECK_D - 2 * P.GASKET_INSET, P.GASKET_R_OUT,
                        P.DECK_W - 2 * (P.GASKET_INSET + P.GASKET_W),
                        P.DECK_D - 2 * (P.GASKET_INSET + P.GASKET_W),
                        max(1.5, P.GASKET_R_OUT - P.GASKET_W),
                        P.DECK_H - P.GASKET_DEPTH, P.DECK_H + 1.0)
    return union(*hinges), pin, mags, gasket


def build_deck_keyboard(phone=None):
    """键盘底座（模块化变体）：同一铰链接口，控制井换成"迷你蓝牙键盘"舱位。

    放进去的是买来的迷你蓝牙键盘模块（约 150×60×7.5mm，自带电池/蓝牙/充电口），
    所以这一件不需要任何自研电路；装好后就是"手机当屏幕的迷你笔记本"。
    """
    kw, kd, kt = P.KB_MODULE
    shell = union(*_deck_shell_prisms())
    deck = shell

    # 舱体：从贴合面往下挖，模块顶面比贴合面低 KB_TOP_GAP
    bay_top = P.DECK_H - P.KB_TOP_GAP
    bay_floor = bay_top - kt
    bay = prism(kw + 2 * P.KB_FIT, kd + 2 * P.KB_FIT, 3.0, bay_floor, P.DECK_H + 2.0)
    # 底部掏空（留 3mm 壁 + 舱底）
    hollow = prism(P.DECK_W - 11.0, P.DECK_D - 11.0, 4.0, 3.0, bay_floor - 2.0)
    # 侧壁充电口（对准模块自己的充电口，插线用）
    charge = box((5.0, P.KB_CHARGE_CUT[0], P.KB_CHARGE_CUT[1]),
                 center=(-P.DECK_W / 2, 0.0, bay_floor + kt / 2))
    deck = diff(deck, bay, hollow, charge)

    # 前缘挡边 + 两侧定位边（模块靠重力 + 挡边定位，倒过来也不掉）
    lip = box((kw + 2 * P.KB_FIT, P.KB_LIP, P.KB_TOP_GAP + 0.4),
              center=(0.0, -P.PIVOT_Y * 0 - (kd / 2 + P.KB_FIT - P.KB_LIP / 2), bay_top + 0.2))
    deck = union(deck, lip)

    hinge, pin_bore, mags, gasket = _deck_hinge_and_faces()
    deck = diff(union(deck, hinge), pin_bore, *mags, gasket)
    return deck


def build_keyboard_module(phone=None):
    """买来的迷你蓝牙键盘（虚拟件，仅用于预览，不打印）。
    总厚 = KB_MODULE[2]（含键帽），键区 15 列 × 5 行、间距 9.6mm。"""
    kw, kd, kt = P.KB_MODULE
    body_h = kt - 1.6
    z0 = P.DECK_H - P.KB_TOP_GAP - kt            # 就地建模：坐在键盘舱底
    body = prism(kw, kd, 3.0, z0, z0 + body_h)
    cols, rows, pitch = 15, 5, 9.6
    x0 = -(cols - 1) * pitch / 2.0
    y0 = -(rows - 1) * pitch / 2.0
    keys = []
    for r in range(rows):
        for c in range(cols):
            if r == rows - 1 and 4 <= c <= 10:          # 最下一排留出空格
                continue
            keys.append(box((8.4, 8.4, 1.6), center=(x0 + c * pitch, y0 + r * pitch, z0 + body_h + 0.8), r=1.0))
    keys.append(box((5 * pitch + 1.0, 8.4, 1.6),
                    center=(x0 + 7 * pitch, y0 + (rows - 1) * pitch, z0 + body_h + 0.8), r=1.0))  # 空格
    return union(body, *keys)


def build_lid(phone=None):
    (x0, x1) = P.LID_RAIL_X
    (y0, y1) = P.LID_BODY_Y
    (yf0, yf1) = P.LID_BAR_FRONT
    (yb0, yb1) = P.LID_BAR_BACK
    zi, zo = P.LID_Z_INNER, P.LID_Z_OUTER

    rails = [prism(x1 - x0, y1 - y0, 2.0, zi, zo, cx=sx * (x0 + x1) / 2, cy=(y0 + y1) / 2)
             for sx in (1, -1)]
    bars = [prism(2 * x0, yf1 - yf0, 2.0, zi, zo, 0.0, (yf0 + yf1) / 2),
            prism(2 * x0, yb1 - yb0, 2.0, zi, zo, 0.0, (yb0 + yb1) / 2)]

    # 铰链凸耳：只在两侧（|x| >= 80），中间必须留空给托盘翻转
    kx0, kx1 = P.HINGE_X_LID
    knuckles = [cyl(P.HINGE_BARREL_R, kx1 - kx0, (sx * (kx0 + kx1) / 2, 0.0, 0.0), axis="x")
                for sx in (1, -1)]
    # 导轨后端 -> 凸耳 的连接腹板（在 |x| ∈ 导轨宽度内，安全）
    webs = [box((x1 - x0, 8.0, zo - zi), center=(sx * (x0 + x1) / 2, -10.5, (zi + zo) / 2))
            for sx in (1, -1)]

    lid = union(*rails, *bars, *knuckles, *webs)

    # 销孔
    lid = diff(lid, cyl(P.HINGE_BORE / 2, 340.0, (0.0, 0.0, 0.0), axis="x"))

    # 托盘轴轴承孔（贯穿两侧导轨）
    for sx in (1, -1):
        lid = diff(lid, cyl(P.PIVOT_BORE / 2, 10.0,
                            (sx * (x0 + x1) / 2, P.PIVOT_Y, P.PIVOT_Z), axis="x"))

    # 合盖磁铁坑（导轨底面，位置用设备坐标 y 给出 -> 转成 lid 局部 y）
    for sx in (1, -1):
        for my_device in P.LATCH_MAG_Y:
            lid = diff(lid, cyl(P.LATCH_MAG_D / 2, P.LATCH_MAG_DEPTH,
                                (sx * (x0 + x1) / 2, my_device - P.HINGE_Y,
                                 zi + P.LATCH_MAG_DEPTH / 2 - 0.02)))

    # 合盖角定位凹坑（凸耳内端面 |x|=kx0，方向 -y）
    dy, dz = _dir_yz(0.0)
    for sx in (1, -1):
        for ang in P.HINGE_ANGLES:
            d2, e2 = _dir_yz(ang)
            # 凹坑在 lid 上的相位：转到 deck 固定方向 -y
            lid = diff(lid, cyl(P.HINGE_DETENT_D / 2, 2.0 * P.HINGE_DETENT_DEPTH,
                                (sx * (kx0 + P.HINGE_DETENT_DEPTH - 0.3),
                                 d2 * P.HINGE_DETENT_R, e2 * P.HINGE_DETENT_R), axis="x"))

    # 托盘定位凸点（导轨内侧面，球冠朝内凸出 0.8mm）
    bumps = []
    for sx in (1, -1):
        bx = bump_sphere(sx * x0, 0.8, direction=-float(sx))
        for phi in P.FLIP_ANGLES:
            dy, dz = _dir_yz(phi)
            bumps.append(sphere(1.4, (bx, P.PIVOT_Y + dy * P.DETENT_R, P.PIVOT_Z + dz * P.DETENT_R)))
    lid = union(lid, *bumps)

    return lid


# --------------------------------------------------------------------------- cradle（翻转托盘）
def build_cradle(phone=None):
    ph = P.PHONES[P.DEFAULT_PHONE] if phone is None else phone
    z_lo, z_hi = P.PIVOT_Z - P.PLATE_T / 2, P.PIVOT_Z + P.PLATE_T / 2

    plate = prism(P.PLATE_W, P.PLATE_D, P.PLATE_R, z_lo, z_hi, 0.0, P.PIVOT_Y)

    # 手机托面：0.8mm 沉台 + 周边挡墙；挡墙外缘收在托盘板扫掠圆内
    rec_w, rec_d = ph.L + 2 * P.PHONE_FIT, ph.W + 2 * P.PHONE_FIT
    rim_top = z_lo                                    # 围边朝手机那一侧伸展
    rim_bot = z_lo - P.RIM_H
    # 手机形状的"挖空刀"：要贯穿挡墙 + 围边的整个高度，否则围边下半段会变成实心板
    phone_cut = prism(rec_w, rec_d, ph.R, rim_bot - 1.0, z_lo + 3.0, 0.0, P.PIVOT_Y)
    walls = diff(prism(P.PLATE_W, P.PLATE_D, P.PLATE_R, z_lo, z_lo + 2.0, 0.0, P.PIVOT_Y),
                 phone_cut)
    recess = prism(rec_w, rec_d, ph.R, z_lo - 1.0, z_lo + P.PHONE_RECESS, 0.0, P.PIVOT_Y)

    # 围边：把手机"嵌"进托盘（φ=0 朝内、φ=180 朝外，两个方向都不凸出框）
    rim_hi = prism(P.PLATE_W, P.PLATE_D, P.PLATE_R, rim_bot + P.RIM_STEP_Z, rim_top + 1.0,
                   0.0, P.PIVOT_Y)
    rim_lo = prism(P.PLATE_W - 2 * P.RIM_INSET, P.PLATE_D - 2 * P.RIM_INSET,
                   max(2.0, P.PLATE_R - P.RIM_INSET), rim_bot, rim_bot + P.RIM_STEP_Z + 0.5,
                   0.0, P.PIVOT_Y)
    rim = diff(union(rim_hi, rim_lo), phone_cut)
    # ±X 两端的取手机缺口（切穿围边）
    notch = [box((2 * P.RIM_NOTCH_D, P.RIM_NOTCH_W, P.RIM_H + 6.0),
                 center=(sx * (P.PLATE_W / 2 - P.RIM_NOTCH_D + 0.01), P.PIVOT_Y,
                         rim_bot + P.RIM_H / 2)) for sx in (1, -1)]

    cradle = diff(union(plate, walls, rim), recess, *notch)

    # 四角手指位
    cuts = [box((16.0, 16.0, 6.0), center=(sx * (ph.L / 2 + 2.0),
                                          P.PIVOT_Y + sy * (ph.W / 2 + 2.0), z_lo + 2.0))
            for sx in (1, -1) for sy in (1, -1)]
    cradle = diff(cradle, *cuts)

    # MagSafe 磁环坑 + 相机凸台让位
    ring = ring_annulus(P.MAGSAFE_RING_OUT / 2, P.MAGSAFE_RING_IN / 2,
                        P.MAGSAFE_RING_DEPTH * 2,
                        (0.0, P.PIVOT_Y, z_lo + P.MAGSAFE_RING_DEPTH - 0.02))
    cam = box((ph.cam_size, ph.cam_size, ph.cam_depth * 2),
              center=(ph.cam_x, P.PIVOT_Y + ph.cam_y, z_lo + ph.cam_depth - 0.02), r=6.0)
    cradle = diff(cradle, ring, cam)

    # 轴颈 + 棘轮圆盘（凹坑在其外端面）
    bx0, bx1 = P.BOSS_X
    sx1 = P.STUB_X[1]
    MOUTH = 1.0                      # 螺母坑口部外伸量（避免与轴颈端面形成亚体素薄片）
    NUT_LEN = P.NUT_POCKET[1] + 1.2  # 咬进材料的深度
    axles = []
    for sx in (1, -1):
        boss = cyl(P.BOSS_R, bx1 - bx0, (sx * (bx0 + bx1) / 2, P.PIVOT_Y, P.PIVOT_Z), axis="x")
        stub = cyl(P.AXLE_D / 2, sx1 - bx1, (sx * (bx1 + sx1) / 2, P.PIVOT_Y, P.PIVOT_Z), axis="x")
        axles.append(union(boss, stub))
    cradle = union(cradle, *axles)

    holes, dimples, nuts = [], [], []
    for sx in (1, -1):
        holes.append(cyl(3.2 / 2, 40.0, (sx * (bx0 + sx1) / 2, P.PIVOT_Y, P.PIVOT_Z), axis="x"))
        nuts.append(box((NUT_LEN, P.NUT_POCKET[0], P.NUT_POCKET[0]),
                        center=(sx * (bx0 - MOUTH + NUT_LEN / 2), P.PIVOT_Y, P.PIVOT_Z)))
        for phi in P.FLIP_ANGLES:
            dy, dz = _dir_yz(phi)
            dimples.append(cyl(P.DETENT_D / 2, 2.0 * P.DETENT_DEPTH,
                               (sx * (bx1 - P.DETENT_DEPTH + 0.3),
                                P.PIVOT_Y + dy * P.DETENT_R, P.PIVOT_Z + dz * P.DETENT_R), axis="x"))
    return diff(cradle, *holes, *dimples, *nuts)


def cradle_phone_dir_ok(phone=None):
    """自检：φ=0 时手机在托盘 -z 侧（屏幕朝内）；φ=180 时在 +z 侧（屏幕朝外朝上）。"""
    ph = P.PHONES[P.DEFAULT_PHONE] if phone is None else phone
    z_back = P.PIVOT_Z - P.PLATE_T / 2 + P.PHONE_RECESS - P.PHONE_GAP
    z_mid = z_back - ph.T / 2
    out = []
    for phi in P.FLIP_ANGLES:
        off = np.array([0.0, 0.0, z_mid - P.PIVOT_Z])
        v = R_x(-phi) @ off
        out.append((phi, round(float(P.PIVOT_Y + v[1]), 2), round(float(P.PIVOT_Z + v[2]), 2)))
    return out


# --------------------------------------------------------------------------- phone（仅校验/渲染）
def build_phone(phone=None):
    ph = P.PHONES[P.DEFAULT_PHONE] if phone is None else phone
    body = prism(ph.L, ph.W, ph.R, 0.0, ph.T, 0.0, P.PIVOT_Y)
    z_back = P.PIVOT_Z - P.PLATE_T / 2 + P.PHONE_RECESS - P.PHONE_GAP
    return translate(body, (0.0, 0.0, z_back - ph.T))


# --------------------------------------------------------------------------- 键帽 / 杠杆
def cap_round(d: float, h: float, stem_d: float = 4.2, stem_h: float = 6.0):
    return union(cone(d / 2, d / 2 - 0.7, h, (0, 0, 0)),
                 cyl(stem_d / 2, stem_h, (0, 0, -h / 2 - stem_h / 2 + 0.1)))


def build_caps_abxy():
    """ABXY 键帽：帽体嵌进 Ø10.4 孔（底面 z=CAP_BOTTOM），中心小柱把行程压到开关上。"""
    outs = []
    for dx, dy in ((P.ABXY_R, 0), (-P.ABXY_R, 0), (0, P.ABXY_R), (0, -P.ABXY_R)):
        outs.append(translate(cap_round(P.ABXY_HOLE - 0.6, P.CAP_H, 4.2, P.CAP_STEM),
                              (P.ABXY_CX + dx, P.ABXY_CY + dy, P.CAP_BOTTOM + P.CAP_H / 2)))
    return union(*outs)


def build_cap_dpad():
    """十字键：四臂压 4 个贴片开关，靠 Ø27 孔定位，无中心轴。"""
    arm_w, arm_l, h = 11.0, 26.0, P.CAP_H
    cross = union(box((arm_w, arm_l, h)), box((arm_l, arm_w, h)))
    nubs = union(*[cyl(2.2, P.CAP_STEM + 0.6, (dx, dy, -h / 2 - P.CAP_STEM / 2 + 0.3))
                   for dx, dy in ((0, 8), (0, -8), (8, 0), (-8, 0))])
    return translate(union(cross, nubs), (P.DPAD_X, P.DPAD_Y, P.CAP_BOTTOM + h / 2))


def build_caps_small():
    return union(*[translate(cap_round(P.SMALL_HOLE - 0.6, 3.5, 3.0, P.CAP_STEM),
                             (sx, P.SMALL_Y, P.CAP_BOTTOM + 1.75)) for sx in P.SMALL_XS])


def build_lever(side: int = 1):
    """肩键杠杆（side=1 右侧，-1 左侧）。杠杆体关于自身 X 对称，左右只差平移。"""
    arm = box((16.0, 8.5, 3.0), center=(0, 0, 0), r=1.2)
    tip = box((14.0, 4.0, 5.0), center=(0, 6.0, 1.0), r=1.0)
    stem = cyl(2.0, 5.0, (0, -2.0, -3.0))
    body = union(arm, tip, stem)
    return translate(body, (side * (P.SHOULDER_X[0] + P.SHOULDER_X[1]) / 2,
                            P.DECK_BACK + 2.0, (P.SHOULDER_Z[0] + P.SHOULDER_Z[1]) / 2))


def build_switch_frame():
    """打印开关架：替代 PCB 的按键定位载体。
    直接拧在 deck 的 4 个 PCB 柱上，11 个 6x6 贴片轻触开关从下方压进窗口，
    顶面与 PCB 顶面齐（z=PCB_TOP..+1.8），底面留走线槽。"""
    z0 = P.PCB_TOP
    z1 = z0 + 1.8
    plate = prism(P.PCB_W, P.PCB_D, P.PCB_R, z0, z1)

    keepouts = [cyl(P.STICK_POCKET_D / 2 + P.PCB_KEEPOUT_MARGIN, 6.0,
                    (sx, P.STICK_Y, (z0 + z1) / 2)) for sx in (P.STICK_X, -P.STICK_X)]
    mounts = [cyl(P.PCB_MOUNT_HOLE / 2, 6.0, (hx, hy, (z0 + z1) / 2))
              for hx, hy in P.PCB_BOSS_XY]

    windows, tabs, channels = [], [], []
    w = P.SWITCH_WINDOW
    for (sx, sy) in P.all_switch_positions():
        windows.append(box((w, w, 6.0), center=(sx, sy, (z0 + z1) / 2)))
        # 顶部两侧卡扣凸台（0.3mm），开关从下方压入后被压住
        for s in (1, -1):
            tabs.append(box((P.SWITCH_TAB, 4.0, 0.6),
                            center=(sx + s * (w / 2 - P.SWITCH_TAB / 2), sy, z1 - 0.3)))
        # 走线槽：从开关窗口往中间汇
        channels.append(box((w, 2.0, 0.9), center=(sx, sy - 4.5, z0 + 0.45)))
    # 总线槽（横向）
    channels.append(box((2 * P.STICK_X + 8, 2.4, 1.0), center=(0.0, P.ABXY_CY - 4.5, z0 + 0.5)))

    frame = diff(plate, *keepouts, *mounts, *windows, *channels)
    return union(frame, *tabs)


STICK_BODY_H = 6.0
STICK_PIVOT_Z = P.STICK_POCKET_FLOOR + 0.1 + STICK_BODY_H + 1.0   # 摇杆摆动中心 12.6


def build_sticks_base(phone=None):
    """两个摇杆的固定部分（Ø24 模块本体 + 枢轴座）。虚拟件，不打印。"""
    y, z0 = P.STICK_Y, P.STICK_POCKET_FLOOR + 0.1
    out = []
    for sx in (P.STICK_X, -P.STICK_X):
        out.append(cyl(12.0, STICK_BODY_H, (sx, y, z0 + STICK_BODY_H / 2)))
        out.append(cyl(6.0, 2.0, (sx, y, z0 + STICK_BODY_H + 1.0)))
    return union(*out)


def build_stick_cap(side: int = 1, phone=None):
    """单侧摇杆的轴 + 帽（可绕枢轴摆动）。虚拟件，不打印。"""
    sx = side * P.STICK_X
    y, z0 = P.STICK_Y, P.STICK_POCKET_FLOOR + 0.1
    z = z0 + STICK_BODY_H
    return union(cyl(3.0, 1.8, (sx, y, z + 2.2)),            # 轴
                 cyl(9.0, 1.8, (sx, y, z + 5.0)),            # Ø18 帽
                 cone(9.0, 8.2, 0.6, (sx, y, z + 6.4)))      # 帽顶倒角


def build_sticks(phone=None):
    """两个摇杆整体（兼容旧调用/预览用）。"""
    return union(build_sticks_base(), build_stick_cap(1), build_stick_cap(-1))


def build_cap_one(which: str):
    """单个键帽（ABXY / Start·Select·Home），用于网页里做"按下去"的动画。"""
    if which in ("a", "b", "x", "y"):
        i = {"a": 0, "b": 1, "x": 2, "y": 3}[which]
        dx, dy = ((P.ABXY_R, 0), (-P.ABXY_R, 0), (0, P.ABXY_R), (0, -P.ABXY_R))[i]
        return translate(cap_round(P.ABXY_HOLE - 0.6, P.CAP_H, 4.2, P.CAP_STEM),
                         (P.ABXY_CX + dx, P.ABXY_CY + dy, P.CAP_BOTTOM + P.CAP_H / 2))
    j = {"start": 0, "select": 1, "home": 2}[which]
    return translate(cap_round(P.SMALL_HOLE - 0.6, 3.5, 3.0, P.CAP_STEM),
                     (P.SMALL_XS[j], P.SMALL_Y, P.CAP_BOTTOM + 1.75))


def build_hinge_pin():
    return cyl(P.HINGE_PIN_D / 2, 150.0, (0, P.HINGE_Y, P.HINGE_Z), axis="x")


PARTS = {
    "deck": build_deck,
    "lid": build_lid,
    "cradle": build_cradle,
    "phone": build_phone,
    "caps_abxy": build_caps_abxy,
    "cap_dpad": build_cap_dpad,
    "caps_small": build_caps_small,
    "lever_l": lambda: build_lever(-1),
    "lever_r": lambda: build_lever(1),
    "deck_keyboard": build_deck_keyboard,
    "keyboard_module": build_keyboard_module,
    "switch_frame": build_switch_frame,
    "sticks": build_sticks,
    "sticks_base": build_sticks_base,
    "stick_l_cap": lambda: build_stick_cap(-1),
    "stick_r_cap": lambda: build_stick_cap(1),
    "cap_a": lambda: build_cap_one("a"),
    "cap_b": lambda: build_cap_one("b"),
    "cap_x": lambda: build_cap_one("x"),
    "cap_y": lambda: build_cap_one("y"),
    "cap_start": lambda: build_cap_one("start"),
    "cap_select": lambda: build_cap_one("select"),
    "cap_home": lambda: build_cap_one("home"),
    "hinge_pin": build_hinge_pin,
}
