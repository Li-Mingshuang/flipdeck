"""flipdeck-cad 参数表

坐标系（设备坐标系，单位 mm）
    X: 左(-) -> 右(+)      设备宽度方向（= 手机长边方向，横屏游戏时）
    Y: 前(-) -> 后(+)      前 = 玩家一侧；后 = 转轴一侧
    Z: 下(0) -> 上(+)      设备平放时高度方向

局部坐标系
    deck  = 设备坐标系（底面在 z=0）
    lid   = 以"合盖转轴轴线"为原点：z_l=0 在转轴高度，y_l=0 在转轴 y
            合盖(θ=0)时 lid 局部 -> 设备 = 平移 (0, HINGE_Y, HINGE_Z)
            开盖时绕该轴旋转 θ（θ = 台面与盖内表面的夹角，110° 为游戏角）
    cradle= lid 局部在 φ=0（游戏位）下的构型；翻转即绕托盘轴（沿 X，过 (PIVOT_Y,PIVOT_Z)）转 φ

三层机构
    θ : 主合盖角（deck<->lid）     0° = 合盖，110° = 游戏
    φ : 托盘翻转角（lid<cradle）   0° = 手机屏幕朝内(游戏)，180° = 手机屏幕朝外(当普通手机用)
    两轴平行（都沿 X），YZ 平面内是简单连杆关系：无万向节、无过线、可 3D 打印。

两条硬约束（sanity() 会断言）
    A. 托盘/手机翻转时扫掠圆（半径 ~37.9mm，圆心 = 托盘轴）内不能有任何盖的材料，
       所以盖的中间区域不能有腹板 —— 铰链凸耳全部布置在左右两侧（|x|>=80）。
    B. 铰链凸耳圆（半径 8.2，圆心 (52,27)）不能侵入 deck 后壁顶角。
"""

from __future__ import annotations
from dataclasses import dataclass

# ----------------------------------------------------------------------------- 手机
@dataclass(frozen=True)
class Phone:
    name: str
    L: float          # 长边 -> X
    W: float          # 短边 -> Y
    T: float          # 机身厚度
    R: float = 12.0   # 四角圆角
    cam_size: float = 34.0    # 相机凸台外形尺寸（正方形近似）
    cam_depth: float = 4.6    # 相机凸台高度（含镜头）
    cam_x: float = -52.0      # 相机凸台中心 X（cradle 局部坐标）
    cam_y: float = 24.0       # 相机凸台中心 Y


PHONES = {
    "ip12":       Phone("iPhone 12 / 12 Pro", 146.7, 71.5, 7.40, cam_size=37.0, cam_depth=4.2,
                        cam_x=-52.5, cam_y=17.0),
    "ip12promax": Phone("iPhone 12 Pro Max",  160.8, 78.1, 7.40, cam_size=39.0, cam_depth=4.6,
                        cam_x=-58.0, cam_y=19.0),
    "ip16pro":    Phone("iPhone 16 Pro",      149.6, 71.5, 8.25, cam_x=-52.0, cam_y= 24.0),
    "ip16promax": Phone("iPhone 16 Pro Max",  163.0, 77.6, 8.25, cam_x=-57.0, cam_y= 26.0),
    "ip15":       Phone("iPhone 15",          147.6, 71.6, 7.80, cam_x=-51.0, cam_y= 24.0),
    "ip17":       Phone("iPhone 17",          149.6, 71.5, 7.95, cam_x=-52.0, cam_y= 24.0),
    "pixel8":     Phone("Pixel 8",            150.5, 70.8, 8.90, cam_x=  0.0, cam_y= 32.0,
                        cam_size=66.0, cam_depth=3.4),
}
DEFAULT_PHONE = "ip12"


# ----------------------------------------------------------------------------- 板厚/工艺
WALL = 2.5          # 外壳壁厚
FLOOR = 2.0         # 底壳厚
PRINT_CLEAR = 0.35  # 打印运动副单边间隙
VOXEL_BIG = 0.50    # deck/lid/cradle 体素（STL 导出用；配合 0.2113 体素相位偏移可得完全水密网格）
VOXEL_SMALL = 0.28  # 小件体素

# ----------------------------------------------------------------------------- deck（控制下半部）
DECK_W = 179.0          # X 总宽
DECK_D = 88.0           # Y 总深
DECK_H = 19.0           # 高度（边缘顶面，也是与盖的贴合面；井深随之 6.0）
DECK_FRONT = -DECK_D / 2
DECK_BACK = DECK_D / 2

WELL_W = 160.0          # 控制井
WELL_D = 76.0
WELL_FLOOR = 13.0       # 井底 z（= 控制面板顶面）
WELL_R = 7.0

CAVITY_INSET = 4.5      # 内腔相对外轮廓内缩
CAVITY_TOP = 10.5       # 内腔顶（= 控制面板底面，面板厚 2.5）

STICK_X = 56.0
STICK_Y = 14.0
STICK_POCKET_D = 32.0
STICK_POCKET_FLOOR = 5.5
STICK_SCREW_BC = 28.0
STICK_SCREW_PILOT = 1.9
STICK_SCREW_DEPTH = 3.0

DPAD_X, DPAD_Y = -56.0, -17.0
DPAD_HOLE = 27.0
ABXY_CX, ABXY_CY = 56.0, -17.0
ABXY_R = 11.0
ABXY_HOLE = 10.4
SMALL_Y = -33.0
SMALL_HOLE = 6.4
SMALL_XS = (-14.0, 0.0, 14.0)

# --- 按键/开关的竖向预算（画 PCB 用）
SWITCH_TOP = 10.0        # 贴片轻触开关顶面（PCB 顶面 z=8 + 2.0mm 开关）
CAP_GAP = 0.4            # 键帽底面与开关顶面的间隙
CAP_BOTTOM = SWITCH_TOP + CAP_GAP
CAP_H = 5.0              # 键帽高度（其中约 2.5mm 落在 Ø10.4 孔里）
CAP_STEM = 0.4           # 键帽中心小柱（把行程传下去）
PCB_TOP = 8.0            # PCB 顶面（坐在 PCB 柱顶）
PCB_THICK = 1.6

SHOULDER_X = (72.0, 88.0)
SHOULDER_Y = (44.0, 52.0)
SHOULDER_Z = (10.0, 16.0)

USBC_CUT = (10.0, 4.2)
USBC_Y = -6.0
USBC_Z = 6.0
POWER_CUT = (7.0, 3.0)
POWER_Y = 22.0
POWER_Z = 8.0

PCB_BOSS_XY = ((40.0, 30.0), (-40.0, 30.0), (40.0, -30.0), (-40.0, -30.0))
PCB_BOSS_D = 4.6
PCB_BOSS_PILOT = 1.9
PCB_BOSS_TOP = 8.0

# 铰链（凸耳全部在两侧，避免进入托盘翻转扫掠圆）
HINGE_Y = 49.0                # 转轴轴线 y（越靠前，合盖后凸耳伸出越少）
HINGE_Z = DECK_H + 7.0        # 转轴轴线 z（比底座顶面高 7：恰好容纳凸耳半径 + 余量）
HINGE_BARREL_R = 7.0          # 凸耳外半径（细化：原 8.2）
HINGE_PIN_D = 4.0             # 销轴（M4 螺杆）直径
HINGE_BORE = 4.3              # 销孔
HINGE_X_DECK = (30.0, 79.0)   # deck 侧凸耳 |x| 范围
HINGE_X_LID = (80.0, 88.5)    # lid 侧凸耳 |x| 范围
HINGE_GAP = 1.0               # 两侧凸耳之间间隙（放尼龙垫圈提供摩擦）
HINGE_WEB_X = (30.0, 70.0)    # deck 侧腹板 |x| 范围（避开肩键杠杆）
HINGE_DETENT_R = 5.0          # 合盖角定位凹坑分布圆
HINGE_DETENT_D = 3.2
HINGE_DETENT_DEPTH = 1.3
HINGE_ANGLES = (0.0, 110.0, 180.0)

# ----------------------------------------------------------------------------- lid（盖 + 框）
LID_Z_INNER = DECK_H - HINGE_Z          # -7.0  盖内表面（贴合 deck 顶面）
PLATE_OFFSET = 8.5                      # 托盘板底面到盖内表面的距离（手机厚 + 沉台 + 间隙 + 余量）
PLATE_T = 6.6                           # 板厚（减薄：原 7.0）；翻转 180° 后占同一层
LID_H = PLATE_OFFSET + PLATE_T          # 盖高 = 8.5 + 6.6 = 15.1（原 16.0）
LID_Z_OUTER = LID_Z_INNER + LID_H       # 盖外表面
LID_RAIL_X = (84.5, 88.5)               # 两侧导轨 |x| 范围
LID_BODY_Y = (-94.0, -9.2)              # 盖框前后范围（lid 局部）
LID_BAR_FRONT = (-94.0, -90.0)
LID_BAR_BACK = (-12.0, -9.2)

PIVOT_Y = -51.0               # 托盘轴（lid 局部 y = 手机中心线）
PIVOT_Z = LID_Z_INNER + PLATE_OFFSET + PLATE_T / 2   # 板中面；翻转后占同一层
PIVOT_BORE = 6.3
AXLE_D = 6.0

PLATE_W = 160.0
PLATE_D = 74.5
PLATE_R = 6.0
PHONE_RECESS = 0.8
PHONE_FIT = 0.35
PHONE_GAP = 0.10              # 手机背面与沉台底的间隙
# 托盘围边：把手机"嵌"进托盘，这样 φ=0 朝内、φ=180 朝外，两个方向手机都不凸出框
RIM_H = 7.4                   # 围边高度：比手机屏幕高出 0.7mm（薄唇边，既包住手机又不硌手）
RIM_STEP_Z = 4.0              # 围边下半段的高度（这一段外轮廓内缩，避免翻转时扫掠半径过大）
RIM_INSET = 1.2               # 下半段内缩量
RIM_NOTCH_W = 26.0            # ±X 两端手指缺口宽度（沿 Y）
RIM_NOTCH_D = 5.2            # 缺口要切穿到手机挖空面以内，否则会留下 0.05mm 亚体素薄片

MAGSAFE_RING_OUT = 56.0
MAGSAFE_RING_IN = 44.0
MAGSAFE_RING_DEPTH = 1.7
LATCH_MAG_D = 8.0
LATCH_MAG_DEPTH = 2.2
LATCH_MAG_Y = (-34.0, 36.0)   # 设备坐标系 y（两侧导轨底面各 2 个）

# 托盘棘轮（导轨内侧面凸点 + 托盘轴颈端面凹坑）
DETENT_R = 6.0
DETENT_D = 2.8
DETENT_DEPTH = 0.7
FLIP_ANGLES = (0.0, 110.0, 180.0)
BOSS_R = 8.0
BOSS_X = (78.0, 83.5)
STUB_X = (83.5, 88.7)
NUT_POCKET = (5.6, 2.2)       # M3 方螺母坑

# 贴合面密封条槽（必须与外壳上收边同心地内缩，否则圆角处会切穿形成薄片）
SHELL_TOP_INSET = 1.6      # 上收边棱柱相对外轮廓的内缩
SHELL_TOP_R = 5.0          # 上收边圆角
GASKET_INSET = 3.1         # 槽外缘内缩（= 上收边 + 1.5mm 实体）
GASKET_W = 1.5             # 槽宽
GASKET_DEPTH = 1.0         # 槽深
GASKET_R_OUT = SHELL_TOP_R - (GASKET_INSET - SHELL_TOP_INSET)   # 3.5

# ----------------------------------------------------------------------------- PCB（板框见 pcb_outline.py）
PCB_W = 168.0                 # 板宽（|x| <= 84，内腔 170 宽 → 单边 1mm 让位）
PCB_D = 76.0                  # 板深（|y| <= 38，内腔 79 深）
PCB_R = 5.0                   # 四角圆角
PCB_KEEPOUT_MARGIN = 0.5      # 板边到摇杆口袋壁的让位
PCB_MOUNT_HOLE = 2.4          # 安装孔（对准 deck 的 PCB 柱）
PCB_USBC_Y = USBC_Y           # USB-C 母座中心（贴板底，朝下）
PCB_STICK_HEADER_XY = ((30.0, 14.0), (-30.0, 14.0))   # 两个摇杆排线插座位置
SWITCH_BODY = 6.0             # 6x6 轻触开关本体
SWITCH_WINDOW = 6.6           # 开关架上的窗口（本体 + 0.6 间隙）
SWITCH_TAB = 0.3              # 卡扣凸台深度（压住开关不掉）


def switch_positions() -> dict:
    """所有需要装开关的位置（= 键帽中心），板框与打印开关架共用这一份定义。"""
    abxy = [(ABXY_CX + dx, ABXY_CY + dy)
            for dx, dy in ((ABXY_R, 0), (-ABXY_R, 0), (0, ABXY_R), (0, -ABXY_R))]
    dpad = [(DPAD_X + dx, DPAD_Y + dy) for dx, dy in ((0, 8), (0, -8), (8, 0), (-8, 0))]
    small = [(sx, SMALL_Y) for sx in SMALL_XS]
    return {"abxy": abxy, "dpad": dpad, "small": small}


def all_switch_positions() -> list:
    d = switch_positions()
    return d["abxy"] + d["dpad"] + d["small"]

# ----------------------------------------------------------------------------- 姿态
OPEN_GAME = 110.0
POSE_CLOSED_GAME = (0.0, 0.0)
POSE_OPEN_GAME = (OPEN_GAME, 0.0)
POSE_CLOSED_PHONE = (0.0, 180.0)
POSE_SWITCH_MID = (OPEN_GAME, 90.0)


def sweep_radius(phone: Phone | None = None) -> tuple[float, float]:
    """翻转扫掠半径（托盘, 手机）。托盘要把新加的围边算进去。"""
    ph = phone or PHONES[DEFAULT_PHONE]
    plate = 0.0
    # 上半段：满轮廓；下半段外轮廓内缩 RIM_INSET，扫掠半径要按缩后的算
    for dz, half in ((PLATE_T / 2, PLATE_D / 2),
                     (PLATE_T / 2 + RIM_H - RIM_STEP_Z, PLATE_D / 2),
                     (PLATE_T / 2 + RIM_H, PLATE_D / 2 - RIM_INSET)):
        plate = max(plate, (half ** 2 + dz ** 2) ** 0.5)
    z_lo = PIVOT_Z - PLATE_T / 2
    z_back = z_lo + PHONE_RECESS - PHONE_GAP
    z_ext = max(abs(z_back - ph.T - PIVOT_Z), abs(z_back - PIVOT_Z))
    phone_r = ((ph.W / 2) ** 2 + z_ext ** 2) ** 0.5
    return plate, phone_r


def cradle_fit(phone: Phone) -> dict:
    """托盘（= 唯一与机型相关的零件）能否装下这台手机。

    "一壳多机"的接口标准就是托盘外廓 PLATE_W × PLATE_D × PLATE_T：
      deck 与 lid 的几何**完全不引用手机参数**（代码里已验证），
      换机型只需要换 cradle —— 前提是手机能塞进这个外廓且四周挡墙 >= MIN_WALL。
    """
    wall_x = (PLATE_W - (phone.L + 2 * PHONE_FIT)) / 2.0
    wall_y = (PLATE_D - (phone.W + 2 * PHONE_FIT)) / 2.0
    need_w = phone.L + 2 * PHONE_FIT + 2 * MIN_WALL
    need_d = phone.W + 2 * PHONE_FIT + 2 * MIN_WALL
    return dict(wall_x=wall_x, wall_y=wall_y, ok=min(wall_x, wall_y) >= MIN_WALL - 1e-6,
                need_w=need_w, need_d=need_d,
                max_w=PLATE_W - 2 * PHONE_FIT - 2 * MIN_WALL,
                max_d=PLATE_D - 2 * PHONE_FIT - 2 * MIN_WALL)


MIN_WALL = 1.00      # 托盘挡墙最小实体厚度（0.4 喷嘴约 2.5 圈壁；挡墙只做定位，不承力）


def sanity() -> list[str]:
    ph = PHONES[DEFAULT_PHONE]
    msgs = []

    # 一壳多机：deck/lid 与机型无关，只有 cradle 随手机变；这里把边界算出来
    fit = cradle_fit(ph)
    msgs.append(f"托盘接口标准 {PLATE_W}×{PLATE_D}×{PLATE_T}（deck/lid 与机型无关）："
                f"可装最大 {fit['max_w']:.1f} × {fit['max_d']:.1f} mm 的手机；"
                f"当前机型挡墙 X {fit['wall_x']:.2f} / Y {fit['wall_y']:.2f} mm")
    assert fit["ok"], "当前机型的托盘挡墙太薄，需要放大 PLATE_W/D"
    msgs.append("  各机型适配：" + "；".join(
        f"{p.name} {'✔' if cradle_fit(p)['ok'] else '✘需 PLATE ' + format(cradle_fit(p)['need_w'], '.0f') + '×' + format(cradle_fit(p)['need_d'], '.0f')}"
        for p in PHONES.values()))

    window_w = 2 * LID_RAIL_X[0]
    window_d = LID_BAR_BACK[0] - LID_BAR_FRONT[1]
    msgs.append(f"lid 窗口 {window_w:.1f} x {window_d:.1f}；手机 {ph.L:.1f} x {ph.W:.1f} -> "
                f"X余量 {(window_w - ph.L) / 2:.2f}，Y余量 {(window_d - ph.W) / 2:.2f}")
    assert window_w - ph.L > 4.0 and window_d - ph.W > 0.5

    plate_r, phone_r = sweep_radius(ph)
    r_max = max(plate_r, phone_r)
    d_front = abs(PIVOT_Y - LID_BAR_FRONT[1])
    d_back = abs(LID_BAR_BACK[0] - PIVOT_Y)
    msgs.append(f"翻转扫掠半径 托盘 {plate_r:.2f} / 手机 {phone_r:.2f}；到前梁 {d_front:.2f}、"
                f"后梁 {d_back:.2f} -> 最小余量 {min(d_front, d_back) - r_max:.2f}mm")
    assert min(d_front, d_back) > r_max

    # 铰链凸耳必须在托盘扫掠圆之外：凸耳圆心距托盘轴 51.3mm，半径 8.2
    knuckle_axis_d = (PIVOT_Y ** 2 + PIVOT_Z ** 2) ** 0.5
    msgs.append(f"铰链轴线到托盘轴 {knuckle_axis_d:.1f}mm，凸耳半径 {HINGE_BARREL_R} -> "
                f"距扫掠圆余量 {knuckle_axis_d - HINGE_BARREL_R - r_max:.2f}mm；"
                f"凸耳 |x| ∈ {HINGE_X_LID} vs 托盘板 |x|<= {PLATE_W / 2:.0f}")
    assert knuckle_axis_d - HINGE_BARREL_R > r_max
    assert HINGE_X_LID[0] >= PLATE_W / 2

    # 铰链凸耳 vs deck 后壁顶角
    corner = ((DECK_BACK - HINGE_Y) ** 2 + (DECK_H - HINGE_Z) ** 2) ** 0.5
    msgs.append(f"deck 后壁顶角到铰链轴线 {corner:.2f}，凸耳半径 {HINGE_BARREL_R} -> "
                f"余量 {corner - HINGE_BARREL_R:.2f}mm")
    assert corner > HINGE_BARREL_R + 1.0

    # 肩键顶端 vs 凸耳
    sh = ((SHOULDER_Y[0] - HINGE_Y) ** 2 + (SHOULDER_Z[1] - HINGE_Z) ** 2) ** 0.5
    msgs.append(f"肩键顶端到铰链轴线 {sh:.2f} -> 余量 {sh - HINGE_BARREL_R:.2f}mm")
    assert sh > HINGE_BARREL_R + 1.0

    # 合盖游戏位：手机屏幕平面 vs 井内最高件
    screen_z = HINGE_Z + LID_Z_INNER
    stick_top = STICK_POCKET_FLOOR + 7.5 + 3.5      # 摇杆模块 + 帽（估算）
    cap_top = CAP_BOTTOM + CAP_H
    msgs.append(f"合盖游戏位：手机屏幕平面 z={screen_z:.1f}；摇杆帽顶约 z={stick_top:.1f}、"
                f"键帽顶 z={cap_top:.1f} -> 净空 "
                f"{screen_z - max(stick_top, cap_top):.1f}mm")
    assert screen_z - max(stick_top, cap_top) > 1.0

    # 开关焊盘 vs 摇杆口袋让位圆（画 PCB / 打印开关架时的硬约束）
    keep_r = STICK_POCKET_D / 2 + PCB_KEEPOUT_MARGIN
    worst, where = 1e9, ""
    for (bx, by) in all_switch_positions():
        for dx, dy in ((3.0, 0), (-3.0, 0), (0, 3.0), (0, -3.0)):
            px, py = bx + dx, by + dy
            for sx in (STICK_X, -STICK_X):
                dist = ((px - sx) ** 2 + (py - STICK_Y) ** 2) ** 0.5 - keep_r
                if dist < worst:
                    worst, where = dist, f"({bx:.0f},{by:.0f})"
    msgs.append(f"开关焊盘到摇杆让位圆(Ø{2 * keep_r:.0f}) 最近 {worst:+.2f}mm @ {where}")
    assert worst >= 0.3, "按键开关焊盘压进摇杆口袋，需前移按键簇或缩小口袋"

    # 密封条槽：外壁实体厚度 & 圆角处是否会被切穿（曾经出过亚体素薄片）
    gasket_wall = GASKET_INSET - SHELL_TOP_INSET
    msgs.append(f"密封条槽：外壁实体 {gasket_wall:.2f}mm（需 >= 1.0）、槽宽 {GASKET_W}、"
                f"深 {GASKET_DEPTH}；槽内缘距控制井 {min(DECK_W / 2 - GASKET_INSET - GASKET_W - WELL_W / 2, DECK_D / 2 - GASKET_INSET - GASKET_W - WELL_D / 2):.2f}mm")
    assert gasket_wall >= 1.0, "密封条槽外壁太薄"
    assert GASKET_DEPTH >= 0.8, "密封条槽太浅"

    # 整机外形
    import math
    open_h = HINGE_Z + abs(LID_BODY_Y[0]) * math.sin(math.radians(OPEN_GAME))
    msgs.append(f"整机厚度：合盖游戏 {HINGE_Z + LID_Z_OUTER:.1f}mm；"
                f"合盖手机模式 {HINGE_Z + LID_Z_OUTER + ph.T + PHONE_RECESS + PHONE_GAP:.1f}mm；"
                f"展开游戏(θ=110°) 高 {open_h:.0f}mm × 深 {DECK_D / 2 + 94 * math.cos(math.radians(20)):.0f}mm")
    return msgs


if __name__ == "__main__":
    for m in sanity():
        print(m)
    print("sweep_radius:", sweep_radius())
