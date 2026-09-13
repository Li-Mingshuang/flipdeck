"""不开网格器也能查结构：打印关键剖面的 ASCII 图 + 参数自检。

用法： python debug_slices.py [deck|lid|cradle|poses|all]
"""

from __future__ import annotations
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flipdeck import params as P
from flipdeck import parts
from flipdeck.sdf import ascii_slice, xform, compose
from flipdeck import poses

RES = float(os.environ.get("SLICE_RES", "1.6"))
WIDTH = int(os.environ.get("SLICE_WIDTH", "150"))


def show(title, f, **kw):
    print("\n" + "=" * 100)
    print(f"### {title}")
    print(ascii_slice(f, res=RES, width=WIDTH, **kw))


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"

    print("=" * 100)
    print("### 参数自检")
    for m in P.sanity():
        print("  " + m)
    print("### 托盘方向自检 (φ, y, z) —— 手机中心相对托盘轴的位置")
    for row in parts.cradle_phone_dir_ok():
        print("  φ=%6.1f  y_l=%+7.2f  z_l=%+7.2f" % row)

    if what in ("deck", "all"):
        deck = parts.build_deck()
        for z in (5.0, 8.0, 15.0, 19.0, 26.0):
            show(f"deck 水平剖面 z={z}", deck, axis="z", level=z,
                 x0=-95, x1=95, y0=-50, y1=62)
        show("deck 纵向剖面 y=+50（铰链区）", deck, axis="y", level=50.0,
             x0=-92, x1=92, z0=-2, z1=40)
        show("deck 纵向剖面 y=-14（D-pad / ABXY 中心线）", deck, axis="y", level=-14.0,
             x0=-92, x1=92, z0=-2, z1=24)

    if what in ("lid", "all"):
        lid = parts.build_lid()
        for z in (6.5, 0.0, -5.0):
            show(f"lid 水平剖面 z_l={z}", lid, axis="z", level=z,
                 x0=-95, x1=95, y0=-100, y1=14)
        show("lid 纵向剖面 x=86.5（导轨 + 托盘轴承孔）", lid, axis="x", level=86.5,
             y0=-100, y1=14, z0=-12, z1=14)

    if what in ("cradle", "all"):
        cradle = parts.build_cradle()
        for z in (3.0, 3.9, 6.5, 10.0):
            show(f"cradle 水平剖面 z_l={z}", cradle, axis="z", level=z,
                 x0=-90, x1=90, y0=-100, y1=0)
        show("cradle 纵向剖面 x=81（轴颈/棘轮区）", cradle, axis="x", level=81.0,
             y0=-100, y1=0, z0=-6, z1=20)

    if what in ("poses", "all"):
        deck = parts.build_deck()
        lid = parts.build_lid()
        cradle = parts.build_cradle()
        phone = parts.build_phone()

        def merged(theta, phi):
            Tl = poses.pose("lid", theta, phi)
            Tc = poses.pose("cradle", theta, phi)
            return [xform(deck, poses.pose("deck", theta, phi)),
                    xform(lid, Tl),
                    xform(cradle, Tc),
                    xform(phone, Tc)]

        def combined(p, fs):
            out = fs[0](p)
            for f in fs[1:]:
                np.minimum(out, f(p), out=out)
            return out

        for tag, (th, ph) in {"合盖储物(θ=0,φ=0)": (0, 0),
                              "展开游戏(θ=110,φ=0)": (P.OPEN_GAME, 0),
                              "合盖手机模式(θ=0,φ=180)": (0, 180),
                              "换形态中间(θ=110,φ=90)": (P.OPEN_GAME, 90)}.items():
            fs = merged(th, ph)
            comb = lambda p, fs=fs: combined(p, fs)
            show(f"整机组合 {tag}  —— 纵向剖面 x=0", comb, axis="x", level=0.0,
                 y0=-100, y1=80, z0=-20, z1=110)
            show(f"整机组合 {tag}  —— 纵向剖面 x=86.5（机构侧）", comb, axis="x", level=86.5,
                 y0=-100, y1=80, z0=-20, z1=110)


if __name__ == "__main__":
    main()
