"""构建：SDF -> 三角网格 -> STL + 渲染 + 报告。

用法示例
    python build.py                        # 全部零件，导出 STL(0.5) + 预览渲染(1.35)
    python build.py --only deck lid        # 只做部分
    python build.py --no-render --no-checks
    python build.py --voxel 0.4 --preview-voxel 1.0
    python build.py --render-dir ip12      # 渲染图输出到 out/ip12（文件被占用时用）
"""

from __future__ import annotations
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flipdeck import params as P
from flipdeck import parts
from flipdeck import poses
from flipdeck.meshlib import mesh_stats, surface_nets, write_stl
from flipdeck.render import render

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")
STL_DIR = os.path.join(OUT, "stl")

COLORS = {
    "deck": (0.60, 0.62, 0.66),
    "lid": (0.26, 0.52, 0.84),
    "cradle": (0.92, 0.56, 0.18),
    "phone": (0.13, 0.14, 0.16),
    "caps_abxy": (0.85, 0.22, 0.24),
    "cap_dpad": (0.20, 0.20, 0.22),
    "caps_small": (0.75, 0.75, 0.78),
    "lever_l": (0.30, 0.70, 0.45),
    "lever_r": (0.30, 0.70, 0.45),
    "hinge_pin": (0.85, 0.85, 0.88),
}

CAPS_BBOX = ((-95.0, -50.0, 5.0), (95.0, 50.0, 24.0))
PIN_BBOX = ((-78.0, 48.0, 23.0), (78.0, 56.0, 31.0))
LEVER_BBOX = ((-92.0, 36.0, 5.0), (92.0, 58.0, 21.0))
# 采样相位候选：体素化时若零件平面正好落在采样平面上会产生歧义单元（非流形边），换相位避开
PHASES = (0.2113, 0.0, 0.137, 0.37, 0.5, 0.618, 0.809)

DECK_PARTS = ["deck", "lid", "cradle", "phone", "cap_dpad", "caps_abxy", "caps_small",
              "lever_l", "lever_r", "switch_frame", "hinge_pin"]
WITH_DECK = ("cap_dpad", "caps_abxy", "caps_small", "lever_l", "lever_r", "switch_frame",
             "hinge_pin")


def bbox_of(name: str, margin: float = 1.0):
    if name.startswith("cap_") or name.startswith("caps_") or name.startswith("stick") \
            or name == "switch_frame":
        lo, hi = CAPS_BBOX
    elif name == "hinge_pin":
        lo, hi = PIN_BBOX
    elif name in ("lever_l", "lever_r"):
        lo, hi = LEVER_BBOX
    else:
        lo, hi = poses.local_bbox(name)
    return (np.asarray(lo, dtype=float) - margin, np.asarray(hi, dtype=float) + margin)


def mesh_part(name: str, voxel: float, margin: float = 1.0, phase: float = 0.2113):
    """phase：把采样原点偏移非整数个体素。"""
    t0 = time.time()
    lo, hi = bbox_of(name, margin)
    verts, tris = surface_nets(parts.PARTS[name](), lo + phase * voxel, hi, voxel)
    return verts, tris, time.time() - t0


def mesh_part_clean(name: str, voxel: float, margin: float = 1.0):
    """STL 导出用：自动挑一个采样相位，使网格完全水密；都不行就取非流形边最少的那次。"""
    best = None
    for phase in PHASES:
        lo, hi = bbox_of(name, margin)
        t0 = time.time()
        v, t = surface_nets(parts.PARTS[name](), lo + phase * voxel, hi, voxel)
        dt = time.time() - t0
        st = mesh_stats(v, t)
        cand = (v, t, dt, st, phase)
        if best is None or st["nonmanifold_edges"] < best[3]["nonmanifold_edges"]:
            best = cand
        if st["watertight"]:
            return cand
    return best


def apply_pose(verts: np.ndarray, name: str, theta: float, phi: float):
    pname = "deck" if name in WITH_DECK else name
    T = poses.pose(pname, theta, phi)
    return verts @ T[:3, :3].T + T[:3, 3]


def scene_at(meshes: dict, theta: float, phi: float, names=None):
    names = names or DECK_PARTS
    out = []
    for n in names:
        if n not in meshes:
            continue
        v, t = meshes[n]
        out.append({"verts": apply_pose(v, n, theta, phi), "tris": t,
                    "color": COLORS.get(n, (0.7, 0.7, 0.7))})
    return out


def zoom_scene(scene, pivot, zoom):
    """几何缩放实现特写（渲染器的自定义取景通道已修好，这里作为兜底）。"""
    return [{"verts": (s["verts"] - np.asarray(pivot)) * zoom, "tris": s["tris"],
             "color": s["color"]} for s in scene]


def center_scene(scene):
    allv = np.concatenate([s["verts"] for s in scene])
    c = (allv.min(axis=0) + allv.max(axis=0)) / 2.0
    for s in scene:
        s["verts"] = s["verts"] - c
    return scene


def section_mesh(name: str, keep_lo, keep_hi, voxel: float, phase: float = 0.2113):
    """把零件按坐标区间切开后再网格化，用于剖视渲染。"""
    from flipdeck.sdf import box as sbox, diff as sdiff
    f = parts.PARTS[name]()
    for axis in range(3):
        size = [1e4, 1e4, 1e4]
        c = [0.0, 0.0, 0.0]
        c[axis] = keep_hi[axis] + 5000.0
        f = sdiff(f, sbox(size, center=c))
        c[axis] = keep_lo[axis] - 5000.0
        f = sdiff(f, sbox(size, center=c))
    lo, hi = bbox_of(name, margin=1.0)
    lo = np.maximum(lo, np.asarray(keep_lo) - 1.0)
    hi = np.minimum(hi, np.asarray(keep_hi) + 1.0)
    return surface_nets(f, lo + phase * voxel, hi, voxel)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--voxel", type=float, default=P.VOXEL_BIG)
    ap.add_argument("--preview-voxel", type=float, default=1.35)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--no-stl", action="store_true")
    ap.add_argument("--render-dir", default=None,
                    help="渲染图输出子目录（默认 out/；文件被图片查看器占用时换目录）")
    args = ap.parse_args()

    os.makedirs(STL_DIR, exist_ok=True)
    outdir = os.path.join(OUT, args.render_dir) if args.render_dir else OUT
    os.makedirs(outdir, exist_ok=True)

    names = args.only or ["deck", "lid", "cradle", "phone", "cap_dpad", "caps_abxy",
                          "caps_small", "lever_l", "lever_r", "switch_frame"]
    ph = P.PHONES[P.DEFAULT_PHONE]
    report = ["# flipdeck-cad 构建报告\n",
              f"机型：**{ph.name}**（{ph.L} × {ph.W} × {ph.T} mm）\n",
              f"体素：STL {args.voxel}mm，预览 {args.preview_voxel}mm"
              f"（Surface Nets + 自动采样相位，边角自带约 1 体素圆角）\n"]

    # ---------------- 参数自检
    report.append("\n## 参数自检\n")
    for m in P.sanity():
        report.append(f"- {m}")
        print("  " + m)

    # ---------------- STL
    report.append("\n## 零件（STL 导出）\n")
    report.append("| 零件 | 三角面 | 实体体积 cm³ | 估算耗材 g | 水密 | 边界面 | 非流形边 | "
                  "包围盒 mm | 相位 | 耗时 s | 文件 |")
    report.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for n in names:
        if n == "phone":
            continue
        verts, tris, dt, st, phase = mesh_part_clean(n, args.voxel)
        path = ""
        if not args.no_stl:
            path = os.path.join(STL_DIR, f"{n}.stl")
            write_stl(path, verts, tris)
        bb = st["bbox"]
        bb_s = (f"{bb[0][0]:.0f}..{bb[1][0]:.0f} × {bb[0][1]:.0f}..{bb[1][1]:.0f} × "
                f"{bb[0][2]:.0f}..{bb[1][2]:.0f}")
        fill = 0.35 if st["volume"] > 20000 else 0.9
        grams = st["volume"] / 1000.0 * fill * 1.27
        report.append(f"| {n} | {st['n_tris']} | {st['volume'] / 1000:.1f} | ~{grams:.0f} | "
                      f"{'✔' if st['watertight'] else '✘'} | {st['boundary_edges']} | "
                      f"{st['nonmanifold_edges']} | {bb_s} | {phase:.4f} | {dt:.1f} | "
                      f"{os.path.basename(path) if path else '-'} |")
        print(f"  [STL] {n:12s} tris={st['n_tris']:7d} vol={st['volume'] / 1000:6.1f}cm³ "
              f"watertight={st['watertight']} phase={phase} {dt:.1f}s")

    # ---------------- 间隙检查
    if not args.no_checks:
        print("\n  [检查] 装配间隙 …")
        build = {"deck": parts.build_deck, "lid": parts.build_lid,
                 "cradle": parts.build_cradle, "phone": parts.build_phone}
        rows = poses.run_pose_checks(build, verbose=True)
        ok, bad = poses.verdict(rows)
        report.append("\n## 装配间隙检查（gap<0 为干涉）\n")
        report.append("| 场景 | 零件对 | θ | φ | 最小间隙 mm |")
        report.append("|---|---|---|---|---|")
        for r in rows:
            report.append(f"| {r['tag']} | {r['pair']} | {r['theta']:.0f} | {r['phi']:.0f} | "
                          f"{r['gap']:+.2f} |")
        report.append(f"\n结论：{'全部姿态无干涉 ✔' if ok else f'{len(bad)} 处干涉 ✘'}")

    # ---------------- 渲染
    made = []
    if not args.no_render:
        try:
            print("\n  [渲染] 预览网格 …")
            prev = {n: mesh_part(n, args.preview_voxel)[:2] for n in
                    ["deck", "lid", "cradle", "phone", "cap_dpad", "caps_abxy", "caps_small",
                     "lever_l", "lever_r"]}

            views = [
                ("closed_game", "合盖 · 游戏收纳 (θ=0, φ=0)", 0.0, 0.0, 35, 24),
                ("open_game", "展开 · 游戏中 (θ=110, φ=0)", P.OPEN_GAME, 0.0, 38, 20),
                ("open_game_back", "展开 · 游戏中（后视，看铰链）", P.OPEN_GAME, 0.0, 205, 15),
                ("closed_phone", "合盖 · 手机模式 (θ=0, φ=180)", 0.0, 180.0, 35, 26),
                ("switch_mid", "换形态中间 (θ=110, φ=90)", P.OPEN_GAME, 90.0, 25, 14),
                ("switch_mid_side", "换形态中间（侧视）", P.OPEN_GAME, 45.0, 92, 6),
            ]
            for tag, label, th, pha, az, el in views:
                sc = center_scene(scene_at(prev, th, pha))
                path = os.path.join(outdir, f"render_{tag}.png")
                t0 = time.time()
                render(sc, path, size=(1100, 800), azimuth=az, elevation=el)
                made.append((path, label))
                print(f"  [渲染] {tag:16s} {time.time() - t0:5.1f}s -> {os.path.basename(path)}")

            # 爆炸图
            exp = []
            for n, dz in (("deck", 0.0), ("lid", 55.0), ("cradle", 95.0), ("phone", 140.0)):
                v, t = prev[n]
                exp.append({"verts": apply_pose(v, n, 0.0, 0.0) + np.array([0.0, 0.0, dz]),
                            "tris": t, "color": COLORS[n]})
            path = os.path.join(outdir, "render_exploded.png")
            render(center_scene(exp), path, size=(900, 1100), azimuth=28, elevation=18)
            made.append((path, "爆炸图：deck / lid / cradle / phone"))
            print(f"  [渲染] exploded -> {os.path.basename(path)}")

            # 单件图
            for n in ["deck", "lid", "cradle"]:
                v, t = prev[n]
                path = os.path.join(outdir, f"part_{n}.png")
                render([{"verts": v, "tris": t, "color": COLORS[n]}], path,
                       size=(1000, 720), azimuth=35, elevation=28)
                made.append((path, f"零件 {n}"))
                print(f"  [渲染] part_{n} -> {os.path.basename(path)}")

            # 机构特写：θ=110, φ=0 时托盘轴颈在设备坐标约 (85, 67, 131)
            det = scene_at(prev, P.OPEN_GAME, 0.0, names=["lid", "cradle", "phone"])
            path = os.path.join(outdir, "render_detail_axle.png")
            try:
                render(det, path, size=(1000, 760), azimuth=52, elevation=12,
                       target=(85.0, 67.0, 131.0), ortho_scale=95.0)
            except Exception as exc:
                print(f"  [渲染] detail 自定义取景失败({type(exc).__name__})，退回几何缩放")
                render(zoom_scene(det, (85.0, 67.0, 131.0), 3.2), path,
                       size=(1000, 760), azimuth=52, elevation=12)
            made.append((path, "机构特写：托盘轴颈 + 棘轮定位"))
            print(f"  [渲染] detail -> {os.path.basename(path)}")

            # 剖视：合盖游戏位 / 手机模式（x>0 侧切掉）
            for tag, pha, note in (("closed", 0.0, "合盖游戏位：三层夹心"),
                                   ("phone", 180.0, "手机模式：手机翻到盖外侧")):
                sec = []
                for n in ["deck", "lid", "cradle", "phone"]:
                    v, t = section_mesh(n, (-2.0, -1e3, -1e3), (1e3, 1e3, 1e3),
                                        args.preview_voxel * 1.2)
                    sec.append({"verts": apply_pose(v, n, 0.0, pha), "tris": t,
                                "color": COLORS[n]})
                path = os.path.join(outdir, f"render_section_{tag}.png")
                render(center_scene(sec), path, size=(1200, 700), azimuth=0.0, elevation=4)
                made.append((path, f"剖视：{note}"))
                print(f"  [渲染] section_{tag} -> {os.path.basename(path)}")

            # 翻转过程五连拍（沿铰链轴看 YZ 平面，副本沿 Y 排开）
            seq = []
            for i, pha in enumerate([0.0, 45.0, 90.0, 135.0, 180.0]):
                off = np.array([0.0, (2.0 - i) * 115.0, 0.0])
                for n in ["lid", "cradle", "phone"]:
                    v, t = prev[n]
                    seq.append({"verts": apply_pose(v, n, P.OPEN_GAME, pha) + off,
                                "tris": t, "color": COLORS[n]})
            path = os.path.join(outdir, "render_flip_sequence.png")
            render(center_scene(seq), path, size=(1500, 620), azimuth=0.0, elevation=6,
                   ortho_scale=430.0)
            made.append((path, "托盘翻转过程 φ=0/45/90/135/180（θ=110 开盖状态）"))
            print(f"  [渲染] flip_sequence -> {os.path.basename(path)}")
        except Exception as exc:
            print(f"  [渲染] 失败：{type(exc).__name__}: {exc}")
            report.append(f"\n## 渲染图\n\n渲染阶段失败：{type(exc).__name__}: {exc}\n")

    if made:
        report.append("\n## 渲染图\n")
        for p, label in made:
            report.append(f"- `{os.path.relpath(p, ROOT)}` — {label}")

    with open(os.path.join(OUT, "BUILD_NOTES.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n")
    print(f"\n报告：{os.path.join(OUT, 'BUILD_NOTES.md')}")


if __name__ == "__main__":
    main()
