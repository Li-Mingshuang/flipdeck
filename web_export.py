"""导出网页用的轻量 STL（体素粗一档，三角形少、加载快）。

输出： web/stl/*.stl
用法： python web_export.py [voxel]
"""

from __future__ import annotations
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build as B
from flipdeck import params as P
from flipdeck.meshlib import mesh_stats, surface_nets, write_stl

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "stl")
# 每个文件对应网页里的一个可独立动画的零件
PARTS = ["deck", "lid", "cradle", "phone",
         "cap_dpad", "cap_a", "cap_b", "cap_x", "cap_y",
         "cap_start", "cap_select", "cap_home",
         "lever_l", "lever_r", "sticks_base", "stick_l_cap", "stick_r_cap"]


def write_params_json():
    """把网页要用的机构常数导出，避免 viewer 里手抄导致漂移。"""
    ph = P.PHONES[P.DEFAULT_PHONE]
    z_back = P.PIVOT_Z - P.PLATE_T / 2 + P.PHONE_RECESS - P.PHONE_GAP
    data = {
        "phone_name": ph.name, "L": ph.L, "W": ph.W, "T": ph.T,
        "hinge": [0.0, P.HINGE_Y, P.HINGE_Z],
        "pivot": [0.0, P.PIVOT_Y, P.PIVOT_Z],
        "open_game": P.OPEN_GAME,
        "stick_z": P.STICK_POCKET_FLOOR + 0.1 + 6.0 + 1.0,
        "abxy": {"cx": P.ABXY_CX, "cy": P.ABXY_CY, "r": P.ABXY_R},
        "dpad": {"x": P.DPAD_X, "y": P.DPAD_Y},
        "plate": {"w": P.PLATE_W, "d": P.PLATE_D, "t": P.PLATE_T},
        "deck": {"w": P.DECK_W, "d": P.DECK_D, "h": P.DECK_H},
        "lid_h": P.LID_H,
        "phone_z_back": round(z_back, 3),
        "totals": {
            "closed_game": round(P.HINGE_Z + P.LID_Z_OUTER, 1),
            "closed_phone": round(P.HINGE_Z + P.LID_Z_OUTER + ph.T + P.PHONE_RECESS + P.PHONE_GAP, 1),
            "daily_no_deck": round(P.LID_H + ph.T, 1),
        },
    }
    path = os.path.join(os.path.dirname(OUT), "params.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"  params.json -> {path}")
    return data


def main():
    voxel = float(sys.argv[1]) if len(sys.argv) > 1 else 2.6
    os.makedirs(OUT, exist_ok=True)
    print(f"网页用 STL，体素 {voxel}mm -> {OUT}")
    write_params_json()
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
