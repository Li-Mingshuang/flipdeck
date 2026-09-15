"""跑装配干涉/间隙检查（数值验证，不依赖网格器）。"""

from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from flipdeck import params as P
from flipdeck import parts
from flipdeck import poses


def main():
    build = {"deck": parts.build_deck, "lid": parts.build_lid,
             "cradle": parts.build_cradle, "phone": parts.build_phone,
             "deck_keyboard": parts.build_deck_keyboard,
             "keyboard_module": parts.build_keyboard_module}
    print("=" * 100)
    print("### 装配间隙检查（gap<0 = 干涉；容差 -0.15mm）")
    rows = poses.run_pose_checks(build, verbose=True)
    ok, bad = poses.verdict(rows)
    # 模块化变体：键盘底座与盖/手机的干涉 + 键盘模块与舱体的配合
    print("\n### 键盘底座变体（模块化）")
    extra = []
    # (tag, a, b, theta, phi, voxel, 最小允许间隙)
    # 贴合面本来就是 0 接触（和 deck-lid 一样），所以那一项用 -0.15 判定；
    # 键盘模块是"坐在舱底"，也允许 0 接触。
    rows2 = [
        ("键盘底座·合盖", "deck_keyboard", "lid", 0.0, 0.0, 0.8, -0.15),
        ("键盘底座·展开", "deck_keyboard", "lid", 110.0, 0.0, 0.8, 0.2),
        ("键盘底座·手机位", "deck_keyboard", "phone", 0.0, 180.0, 0.8, 0.2),
        ("键盘模块·坐在舱底", "deck_keyboard", "keyboard_module", 0.0, 0.0, 0.4, -0.05),
    ]
    bad2 = []
    for tag, a, b, th, ph_, vox, tol in rows2:
        gap, pos = poses.pair_gap(build, a, b, th, ph_, None, voxel=vox)
        mark = "✔" if gap >= tol else "✘"
        print(f"  {mark} {tag:18s} {a}-{b} gap={gap:+7.2f}mm（要求 >= {tol}）")
        if gap < tol:
            bad2.append(tag)
    print("  ✔ 键盘底座变体全部通过（贴合面 0 接触属正常）" if not bad2
          else f"  ✘ 键盘底座变体未通过：{bad2}")

    print("=" * 100)
    if ok:
        print("结论：全部姿态无干涉 ✔")
    else:
        print(f"结论：发现 {len(bad)} 处干涉 ✘")
        for r in sorted(bad, key=lambda x: x["gap"]):
            print(f"   {r['tag']} {r['pair']} θ={r['theta']:.0f} φ={r['phi']:.0f} "
                  f"gap={r['gap']:+.2f} @ {r['pos']}")
            # 定位：把干涉点换算到两个零件的局部坐标系，看各自是哪个特征
            if r["pos"]:
                p = np.asarray(r["pos"], dtype=float)
                a, b = r["pair"].split("-")
                for name in (a, b):
                    T = poses.pose(name, r["theta"], r["phi"])
                    pl = (p - T[:3, 3]) @ T[:3, :3]
                    val = build[name]()(pl.reshape(1, 1, 1, 3))[0, 0, 0]
                    print(f"       {name}: 局部 ({pl[0]:7.2f},{pl[1]:7.2f},{pl[2]:7.2f})  "
                          f"sdf={val:+.2f}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "clearance_report.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("# 装配间隙报告\n\n")
        for m in P.sanity():
            fh.write(f"- {m}\n")
        fh.write("\n| 场景 | 零件对 | θ | φ | 最小间隙 mm | 位置 |\n|---|---|---|---|---|---|\n")
        for r in rows:
            pos = ("(%.0f, %.0f, %.0f)" % r["pos"]) if r["pos"] else "-"
            fh.write(f"| {r['tag']} | {r['pair']} | {r['theta']:.0f} | {r['phi']:.0f} | "
                     f"{r['gap']:+.2f} | {pos} |\n")
        fh.write(f"\n结论：{'全部姿态无干涉' if ok else f'{len(bad)} 处干涉'}\n")
    print(f"报告已写入 {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
