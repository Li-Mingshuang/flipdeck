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
             "cradle": parts.build_cradle, "phone": parts.build_phone}
    print("=" * 100)
    print("### 装配间隙检查（gap<0 = 干涉；容差 -0.15mm）")
    rows = poses.run_pose_checks(build, verbose=True)
    ok, bad = poses.verdict(rows)
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
