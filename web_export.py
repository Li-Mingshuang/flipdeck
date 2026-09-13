"""导出网页用的轻量 STL（体素粗一档，三角形少、加载快）。

输出： web/stl/*.stl
用法： python web_export.py [voxel]
"""

from __future__ import annotations
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build as B
from flipdeck.meshlib import mesh_stats, surface_nets, write_stl

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "stl")
# 每个文件对应网页里的一个可独立动画的零件
PARTS = ["deck", "lid", "cradle", "phone",
         "cap_dpad", "cap_a", "cap_b", "cap_x", "cap_y",
         "cap_start", "cap_select", "cap_home",
         "lever_l", "lever_r", "sticks_base", "stick_l_cap", "stick_r_cap"]


def main():
    voxel = float(sys.argv[1]) if len(sys.argv) > 1 else 2.6
    os.makedirs(OUT, exist_ok=True)
    print(f"网页用 STL，体素 {voxel}mm -> {OUT}")
    total = 0
    for n in PARTS:
        t0 = time.time()
        v, t = surface_nets(B.parts.PARTS[n](), *B.bbox_of(n, margin=2.0), voxel)
        st = mesh_stats(v, t)
        path = os.path.join(OUT, f"{n}.stl")
        write_stl(path, v, t)
        mb = os.path.getsize(path) / 1e6
        total += mb
        print(f"  {n:11s} tris={st['n_tris']:6d}  {mb:5.2f}MB  {time.time() - t0:4.1f}s")
    print(f"合计 {total:.2f}MB")


if __name__ == "__main__":
    main()
