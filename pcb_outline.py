"""生成 PCB 板框（DXF / SVG / PNG）+ 板级装配干涉自检。

输出（out/pcb/）
    flipdeck_pcb_outline.dxf    板框：外轮廓 + 2 个摇杆让位孔 + 4 个安装孔
    flipdeck_pcb_outline.svg    同上的矢量预览（带标注）
    flipdeck_pcb_layout.png     位号布局预览图（直接能看）
    PCB_FIT_REPORT.md           自检报告（按钮焊盘/口袋让位/安装孔/接口对位）

用法： python pcb_outline.py
"""

from __future__ import annotations
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flipdeck import params as P

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "pcb")

# ---------------------------------------------------------------- 板框参数（全部来自 params）
KEEPOUT_R = P.STICK_POCKET_D / 2 + P.PCB_KEEPOUT_MARGIN      # 16.5
STICK_XY = ((P.STICK_X, P.STICK_Y), (-P.STICK_X, P.STICK_Y))
MOUNTS = P.PCB_BOSS_XY
MCU_W, MCU_D = 22.5, 18.0                                    # ESP32-S3 SuperMini 外形
MCU_XY = (0.0, 18.0)
MCU_ANT_KEEPOUT = (MCU_W, 6.0)                               # 天线净空（板前缘一块不铺铜）

# 按键开关中心（= 键帽中心；焊盘在 ±3.0）
SW_ABXY = P.switch_positions()["abxy"]
SW_DPAD = P.switch_positions()["dpad"]
SW_SMALL = P.switch_positions()["small"]
SW_PAD = 3.0                                                 # 开关体半宽（6x6）

SILK_NOTES = [
    "flipdeck PCB v1  (iPhone 12 cradle)",
    f"outline {P.PCB_W:.0f} x {P.PCB_D:.0f} mm, R{P.PCB_R:.0f}",
    "mount holes 4x d2.4 @ (+-40, +-30)",
    "USB-C bottom-mount at left edge y=-6",
    "stick keep-outs 2x d33 @ (+-56, +14)",
]


def rounded_rect(w, d, r, n=24):
    """返回圆角矩形外轮廓点列（逆时针）。"""
    hx, hy = w / 2 - r, d / 2 - r
    pts = []
    for cx, cy, a0 in ((hx, hy, 0.0), (-hx, hy, 90.0), (-hx, -hy, 180.0), (hx, -hy, 270.0)):
        a = np.deg2rad(a0 + np.linspace(0, 90, n))
        pts += [(cx + r * np.cos(t), cy + r * np.sin(t)) for t in a]
    return pts


def circle(cx, cy, r, n=48):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return [(cx + r * np.cos(t), cy + r * np.sin(t)) for t in a]


# ---------------------------------------------------------------- 自检
def fit_checks():
    rows = []
    ok = True

    def add(name, value, need, good):
        nonlocal ok
        ok = ok and good
        rows.append((name, value, need, "✔" if good else "✘"))

    half_w, half_d = P.PCB_W / 2, P.PCB_D / 2
    add("板宽/深", f"{P.PCB_W:.0f} × {P.PCB_D:.0f}", "内腔 170 × 79（留 ≥1.0mm）",
        P.DECK_W - 2 * P.CAVITY_INSET - P.PCB_W >= 2.0 and
        P.DECK_D - 2 * P.CAVITY_INSET - P.PCB_D >= 2.0)
    add("板厚 + 开关高", f"{P.PCB_THICK:.1f} + 2.0 = {P.PCB_THICK + 2:.1f}",
        f"≤ 内腔净高 {P.CAVITY_TOP - P.FLOOR:.1f}", P.PCB_THICK + 2.0 <= P.CAVITY_TOP - P.FLOOR)

    # 每个开关的 4 个焊盘点必须在板内、且离开摇杆口袋
    worst_pocket, worst_name = 1e9, ""
    inside_all = True
    for tag, lst in (("ABXY", SW_ABXY), ("DPAD", SW_DPAD), ("SMALL", SW_SMALL)):
        for (bx, by) in lst:
            for dx, dy in ((SW_PAD, 0), (-SW_PAD, 0), (0, SW_PAD), (0, -SW_PAD)):
                px, py = bx + dx, by + dy
                if abs(px) > half_w - 0.5 or abs(py) > half_d - 0.5:
                    inside_all = False
                for (sx, sy) in STICK_XY:
                    dist = float(np.hypot(px - sx, py - sy)) - KEEPOUT_R
                    if dist < worst_pocket:
                        worst_pocket, worst_name = dist, f"{tag}({bx:.0f},{by:.0f})"
    add("开关焊盘在板内", "全部", f"|x|≤{half_w - 0.5:.1f}, |y|≤{half_d - 0.5:.1f}", inside_all)
    add(f"焊盘到摇杆让位圆(最近 {worst_name})", f"{worst_pocket:+.2f}mm", "≥ 0.5mm", worst_pocket >= 0.5)

    # 安装孔与 deck 上的 PCB 柱同心、且在板内
    holes_ok = all(abs(hx) < half_w - 3 and abs(hy) < half_d - 3 for hx, hy in MOUNTS)
    add("4× 安装孔对准 deck 的 PCB 柱", str(MOUNTS), "同心 Ø2.4 / 柱 Ø4.6", holes_ok)

    # 安装孔不能落进摇杆让位
    hole_min = min(float(np.hypot(hx - sx, hy - sy)) - KEEPOUT_R
                   for hx, hy in MOUNTS for sx, sy in STICK_XY)
    add("安装孔到摇杆让位圆", f"{hole_min:+.2f}mm", "≥ 0.5mm", hole_min >= 0.5)

    # USB-C 位置 vs deck 侧壁开孔（y 中心、z 中心）
    usb_ok = abs(P.PCB_USBC_Y - P.USBC_Y) < 0.6
    add("USB-C 母座对位", f"y={P.PCB_USBC_Y:.1f} (板底朝下)",
        f"deck 侧孔 y={P.USBC_Y:.1f} ±{P.USBC_CUT[0] / 2:.1f}, z={P.USBC_Z:.1f}", usb_ok)

    # MCU 模块不压开关、不压摇杆
    mcu_clear = True
    for lst in (SW_ABXY, SW_DPAD, SW_SMALL):
        for (bx, by) in lst:
            if (abs(bx - MCU_XY[0]) < MCU_W / 2 + SW_PAD + 1.0 and
                    abs(by - MCU_XY[1]) < MCU_D / 2 + SW_PAD + 1.0):
                mcu_clear = False
    for (sx, sy) in STICK_XY:
        if (abs(sx - MCU_XY[0]) < MCU_W / 2 + KEEPOUT_R and
                abs(sy - MCU_XY[1]) < MCU_D / 2 + KEEPOUT_R):
            mcu_clear = False
    add("主控模块位置净空", f"{MCU_W:.1f}×{MCU_D:.1f} @ {MCU_XY}", "不压开关/让位圆", mcu_clear)

    # 电池空间（板下、避开摇杆口袋）
    bat_w = 2 * (P.STICK_X - KEEPOUT_R)
    bat_h = P.DECK_D - 2 * P.CAVITY_INSET
    add("板下电池可用空间", f"{bat_w:.0f} × {bat_h:.0f} × {P.PCB_TOP - P.FLOOR:.1f}",
        "建议 ≤ 60 × 50 × 5.5 软包", bat_w >= 60 and bat_h >= 50)
    return ok, rows


# ---------------------------------------------------------------- DXF / SVG / PNG
def write_dxf(path):
    def line(x1, y1, x2, y2, layer):
        return (f"0\nLINE\n8\n{layer}\n10\n{x1:.4f}\n20\n{y1:.4f}\n30\n0.0\n"
                f"11\n{x2:.4f}\n21\n{y2:.4f}\n31\n0.0\n")

    def poly(pts, layer, closed=True):
        out = [f"0\nLWPOLYLINE\n8\n{layer}\n90\n{len(pts)}\n70\n{1 if closed else 0}\n"]
        for (x, y) in pts:
            out.append(f"10\n{x:.4f}\n20\n{y:.4f}\n")
        return "".join(out)

    ents = [poly(rounded_rect(P.PCB_W, P.PCB_D, P.PCB_R), "EDGE_CUTS")]
    for (sx, sy) in STICK_XY:
        ents.append(poly(circle(sx, sy, KEEPOUT_R), "EDGE_CUTS_INNER"))
    for (hx, hy) in MOUNTS:
        ents.append(poly(circle(hx, hy, P.PCB_MOUNT_HOLE / 2), "HOLES"))
    # USB-C 与电源拨钮位置（丝印）
    ents.append(poly([(-P.PCB_W / 2, P.PCB_USBC_Y - 4.5), (-P.PCB_W / 2 + 6.5, P.PCB_USBC_Y - 4.5),
                      (-P.PCB_W / 2 + 6.5, P.PCB_USBC_Y + 4.5), (-P.PCB_W / 2, P.PCB_USBC_Y + 4.5)],
                     "SILK"))
    ents.append(poly([(P.PCB_W / 2 - 6.0, P.POWER_Y - 3.5), (P.PCB_W / 2, P.POWER_Y - 3.5),
                      (P.PCB_W / 2, P.POWER_Y + 3.5), (P.PCB_W / 2 - 6.0, P.POWER_Y + 3.5)],
                     "SILK"))
    # MCU 与插座位置
    ents.append(poly([(MCU_XY[0] - MCU_W / 2, MCU_XY[1] - MCU_D / 2),
                      (MCU_XY[0] + MCU_W / 2, MCU_XY[1] - MCU_D / 2),
                      (MCU_XY[0] + MCU_W / 2, MCU_XY[1] + MCU_D / 2),
                      (MCU_XY[0] - MCU_W / 2, MCU_XY[1] + MCU_D / 2)], "SILK"))
    for (hx, hy) in P.PCB_STICK_HEADER_XY:
        ents.append(poly(circle(hx, hy, 3.0), "SILK"))
    with open(path, "w", encoding="ascii") as fh:
        fh.write("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n")
        fh.write("".join(ents))
        fh.write("0\nENDSEC\n0\nEOF\n")
    return path


def write_svg(path):
    m = 12.0
    W, H = P.PCB_W + 2 * m, P.PCB_D + 2 * m

    def P2(x, y):
        return f"{x + W / 2:.2f},{H / 2 - y:.2f}"

    def poly(pts, color, width=0.6, fill="none", dash=""):
        d = " ".join(P2(x, y) for x, y in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        return (f'<polygon points="{d}" fill="{fill}" stroke="{color}" '
                f'stroke-width="{width}"{da}/>')

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W * 3:.0f}" '
             f'height="{H * 3:.0f}" viewBox="0 0 {W:.2f} {H:.2f}">',
             f'<rect width="{W:.2f}" height="{H:.2f}" fill="#101216"/>',
             poly(rounded_rect(P.PCB_W, P.PCB_D, P.PCB_R), "#7fd1ff", 0.8)]
    for (sx, sy) in STICK_XY:
        parts.append(poly(circle(sx, sy, KEEPOUT_R), "#ff9a3c", 0.6, dash="1.5,1.0"))
    for (hx, hy) in MOUNTS:
        parts.append(poly(circle(hx, hy, P.PCB_MOUNT_HOLE / 2), "#ffffff", 0.6))
    parts.append(f'<rect x="{W / 2 - P.PCB_W / 2:.2f}" y="{H / 2 - P.PCB_USBC_Y - 4.5:.2f}" '
                 f'width="6.5" height="9.0" fill="#43d17a" opacity="0.85"/>')
    parts.append(f'<rect x="{W / 2 + P.PCB_W / 2 - 6:.2f}" y="{H / 2 - P.POWER_Y - 3.5:.2f}" '
                 f'width="6.0" height="7.0" fill="#43d17a" opacity="0.85"/>')
    parts.append(f'<rect x="{W / 2 - MCU_W / 2:.2f}" y="{H / 2 - MCU_XY[1] - MCU_D / 2:.2f}" '
                 f'width="{MCU_W}" height="{MCU_D}" fill="#c9a2ff" opacity="0.75"/>')
    for (hx, hy) in P.PCB_STICK_HEADER_XY:
        parts.append(f'<circle cx="{W / 2 + hx:.2f}" cy="{H / 2 - hy:.2f}" r="3" '
                     f'fill="none" stroke="#9fe870" stroke-width="0.6"/>')
    for (bx, by) in SW_ABXY + SW_DPAD + SW_SMALL:
        parts.append(f'<rect x="{W / 2 + bx - SW_PAD:.2f}" y="{H / 2 - by - SW_PAD:.2f}" '
                     f'width="{2 * SW_PAD}" height="{2 * SW_PAD}" fill="none" '
                     f'stroke="#ffd166" stroke-width="0.35"/>')
    for i, (sx, sy) in enumerate(STICK_XY):
        parts.append(f'<text x="{W / 2 + sx - 8:.1f}" y="{H / 2 - sy + 1.2:.1f}" '
                     f'fill="#ff9a3c" font-size="3.2" font-family="monospace">STICK{i + 1}</text>')
    parts.append(f'<text x="{W / 2 - MCU_W / 2:.1f}" y="{H / 2 - MCU_XY[1]:.1f}" '
                 f'fill="#e7dcff" font-size="3.0" font-family="monospace">MCU</text>')
    parts.append(f'<text x="{W / 2 - P.PCB_W / 2 + 1:.1f}" y="{H / 2 - P.PCB_USBC_Y - 5.4:.1f}" '
                 f'fill="#8ff0b0" font-size="3.0" font-family="monospace">USB-C</text>')
    parts.append(f'<text x="{W / 2 - P.PCB_W / 2 + 1:.1f}" y="{H / 2 - P.PCB_D / 2 - 3:.1f}" '
                 f'fill="#8aa0b4" font-size="3.0" font-family="monospace">'
                 f'flipdeck PCB outline  {P.PCB_W:.0f} x {P.PCB_D:.0f} mm</text>')
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))
    return path


def write_png(path, scale=5):
    from PIL import Image, ImageDraw
    m = 14
    W, H = int((P.PCB_W + 2 * m) * scale), int((P.PCB_D + 2 * m) * scale)

    def P2(x, y):
        return (W / 2 + x * scale, H / 2 - y * scale)

    im = Image.new("RGB", (W, H), (16, 18, 22))
    dr = ImageDraw.Draw(im)
    dr.polygon([P2(x, y) for x, y in rounded_rect(P.PCB_W, P.PCB_D, P.PCB_R)],
               outline=(127, 209, 255), width=3)
    for (sx, sy) in STICK_XY:
        dr.polygon([P2(x, y) for x, y in circle(sx, sy, KEEPOUT_R)], outline=(255, 154, 60))
    for (hx, hy) in MOUNTS:
        r = P.PCB_MOUNT_HOLE / 2 * scale
        cx, cy = P2(hx, hy)
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255))
    x0, y0 = P2(-P.PCB_W / 2, P.PCB_USBC_Y + 4.5)
    x1, y1 = P2(-P.PCB_W / 2 + 6.5, P.PCB_USBC_Y - 4.5)
    dr.rectangle([x0, y0, x1, y1], fill=(67, 209, 122))
    x0, y0 = P2(P.PCB_W / 2 - 6, P.POWER_Y + 3.5)
    x1, y1 = P2(P.PCB_W / 2, P.POWER_Y - 3.5)
    dr.rectangle([x0, y0, x1, y1], fill=(67, 209, 122))
    x0, y0 = P2(MCU_XY[0] - MCU_W / 2, MCU_XY[1] + MCU_D / 2)
    x1, y1 = P2(MCU_XY[0] + MCU_W / 2, MCU_XY[1] - MCU_D / 2)
    dr.rectangle([x0, y0, x1, y1], fill=(160, 120, 230))
    for (hx, hy) in P.PCB_STICK_HEADER_XY:
        cx, cy = P2(hx, hy)
        r = 3 * scale
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(159, 232, 112))
    for (bx, by) in SW_ABXY + SW_DPAD + SW_SMALL:
        x0, y0 = P2(bx - SW_PAD, by + SW_PAD)
        x1, y1 = P2(bx + SW_PAD, by - SW_PAD)
        dr.rectangle([x0, y0, x1, y1], outline=(255, 209, 102))
    im.save(path)
    return path


def main():
    os.makedirs(OUT, exist_ok=True)
    ok, rows = fit_checks()
    dxf = write_dxf(os.path.join(OUT, "flipdeck_pcb_outline.dxf"))
    svg = write_svg(os.path.join(OUT, "flipdeck_pcb_outline.svg"))
    png = write_png(os.path.join(OUT, "flipdeck_pcb_layout.png"))
    rep = os.path.join(OUT, "PCB_FIT_REPORT.md")
    with open(rep, "w", encoding="utf-8") as fh:
        fh.write("# PCB 板框与装配自检\n\n")
        fh.write(f"机壳：{P.PHONES[P.DEFAULT_PHONE].name}\n\n")
        fh.write("| 项目 | 实测 | 要求 | 结果 |\n|---|---|---|---|\n")
        for name, value, need, mark in rows:
            fh.write(f"| {name} | {value} | {need} | {mark} |\n")
        fh.write(f"\n结论：{'全部通过 ✔' if ok else '有项目未通过 ✘'}\n")
    print("板框自检：")
    for name, value, need, mark in rows:
        print(f"  {mark} {name}: {value}  (要求 {need})")
    print(f"\nDXF  {dxf}\nSVG  {svg}\nPNG  {png}\n报告 {rep}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
