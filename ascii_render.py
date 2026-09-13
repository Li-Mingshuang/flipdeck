"""把网格投影成 ASCII 图 —— 我自己（无图像输入的模型）用来肉眼校验装配关系。

用法： python ascii_render.py [pose] [--width 150]
"""

from __future__ import annotations
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flipdeck import params as P
from flipdeck import parts
from flipdeck import poses

import build as B

CHARS = {"deck": "#", "lid": "=", "cradle": "o", "phone": "@",
         "caps_abxy": "+", "cap_dpad": "%", "caps_small": ".", "lever_l": "L",
         "lever_r": "l", "hinge_pin": "|"}


def camera(az_deg: float, el_deg: float):
    az, el = np.deg2rad(az_deg), np.deg2rad(el_deg)
    d = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(d, up)
    right /= np.linalg.norm(right)
    up2 = np.cross(right, d)
    return d, right, up2


def project(verts, az, el, width=150, aspect=0.48):
    d, right, up = camera(az, el)
    u = verts @ right
    v = verts @ up
    depth = verts @ d
    u0, u1 = u.min(), u.max()
    v0, v1 = v.min(), v.max()
    span = max(u1 - u0, (v1 - v0) / aspect)
    res = span / width
    cols = width
    rows = max(8, int(round((v1 - v0) / res / aspect) if aspect else int((v1 - v0) / res)))
    return u, v, depth, (u0 + u1) / 2, (v0 + v1) / 2, res, cols, rows, (u1 - u0), (v1 - v0)


def ascii_view(scene, az=35.0, el=24.0, width=150, aspect=0.48, name=""):
    allv = np.concatenate([s["verts"] for s in scene])
    d, right, up = camera(az, el)
    U = allv @ right
    V = allv @ up
    D = allv @ d
    u0, u1 = U.min(), U.max()
    v0, v1 = V.min(), V.max()
    scale = max((u1 - u0) / width, (v1 - v0) / (width * aspect))
    cols = int((u1 - u0) / scale) + 2
    rows = int((v1 - v0) / scale) + 2
    zbuf = np.full((rows, cols), np.inf)
    cbuf = np.full((rows, cols), " ", dtype="<U1")

    off = 0
    for s in scene:
        v = s["verts"]
        t = s["tris"]
        ch = CHARS.get(s.get("name", ""), s.get("char", "*"))
        pu = (v @ right - u0) / scale
        pv = (v @ up - v0) / scale
        pd = v @ d
        for tri in t:
            i0, i1, i2 = tri
            ax, ay = pu[i0], pv[i0]
            bx, by = pu[i1], pv[i1]
            cx, cy = pu[i2], pv[i2]
            x_lo = max(0, int(np.floor(min(ax, bx, cx))))
            x_hi = min(cols - 1, int(np.ceil(max(ax, bx, cx))))
            y_lo = max(0, int(np.floor(min(ay, by, cy))))
            y_hi = min(rows - 1, int(np.ceil(max(ay, by, cy))))
            if x_hi < x_lo or y_hi < y_lo:
                continue
            xs = np.arange(x_lo, x_hi + 1)
            ys = np.arange(y_lo, y_hi + 1)
            gx, gy = np.meshgrid(xs, ys, indexing="ij")
            denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(denom) < 1e-12:
                continue
            w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / denom
            w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / denom
            w2 = 1.0 - w0 - w1
            m = (w0 >= -0.02) & (w1 >= -0.02) & (w2 >= -0.02)
            if not m.any():
                continue
            zz = w0 * pd[i0] + w1 * pd[i1] + w2 * pd[i2]
            sub_z = zbuf[y_lo:y_hi + 1, x_lo:x_hi + 1].T
            sub_c = cbuf[y_lo:y_hi + 1, x_lo:x_hi + 1].T
            upd = m & (zz < sub_z)
            sub_z[upd] = zz[upd]
            sub_c[upd] = ch
            zbuf[y_lo:y_hi + 1, x_lo:x_hi + 1] = sub_z.T
            cbuf[y_lo:y_hi + 1, x_lo:x_hi + 1] = sub_c.T

    lines = [f"### {name}  az={az} el={el}  图例: "
             + " ".join(f"{k}={v}" for k, v in CHARS.items())]
    for r in range(rows - 1, -1, -1):
        lines.append("".join(cbuf[r]).rstrip())
    return "\n".join(lines)


def build_scene(theta, phi, voxel=1.25, names=None):
    names = names or ["deck", "lid", "cradle", "phone", "cap_dpad", "caps_abxy",
                      "caps_small", "lever_l", "lever_r"]
    scene = []
    for n in names:
        v, t = B.mesh_part(n, voxel)[:2]
        pname = "deck" if n in ("cap_dpad", "caps_abxy", "caps_small", "lever_l", "lever_r") else n
        scene.append({"name": n, "verts": B.apply_pose(v, pname, theta, phi), "tris": t})
    return scene


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "open_game"
    width = 150
    poses_map = {
        "closed_game": (0.0, 0.0, [(38, 22), (92, 4)]),
        "open_game": (P.OPEN_GAME, 0.0, [(38, 18), (0, 4)]),
        "closed_phone": (0.0, 180.0, [(38, 22), (92, 2)]),
        "switch_mid": (P.OPEN_GAME, 90.0, [(30, 12), (92, 4)]),
    }
    theta, phi, views = poses_map[which]
    for az, el in views:
        scene = build_scene(theta, phi, voxel=1.25)
        print(ascii_view(scene, az=az, el=el, width=width,
                         name=f"{which} θ={theta} φ={phi}"))


if __name__ == "__main__":
    main()
