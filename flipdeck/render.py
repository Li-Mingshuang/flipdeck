# -*- coding: utf-8 -*-
"""flipdeck/render.py —— 纯 numpy + PIL 的软件渲染器。

正交投影 + z-buffer + 平面着色（headlight + 环境光 + 侧向补光），
超采样（SSAA）+ LANCZOS 降采样，输出 PNG 预览图。

接口严格遵循 `flipdeck-cad/SPEC_interfaces.md` 的 "文件 B：flipdeck/render.py"：

    render(parts, path, size=(1000, 760), azimuth=35.0, elevation=22.0,
           target=(0.0, 0.0, 0.0), distance=None, ortho_scale=None,
           bg=(0.09, 0.10, 0.12), ssaa=2, shadows=False) -> path

实现要点
--------
* 相机基：forward 由相机指向 target，right = forward × world_up，up = right × forward。
  投影： ``sx = RW/2 + dot(p-target, right) * spp``，``sy = RH/2 - dot(p-target, up) * spp``
  （图像行 0 在顶部，所以相机 up 方向对应更小的行号 —— 模型不会上下颠倒）。
* 深度：``depth = distance + dot(p-target, forward)``，越小越靠近相机；z-buffer 取 min。
* 光栅化：按三角形屏幕包围盒整体矢量化，按包围盒面积分桶批处理控制内存；
  片元用 ``np.minimum.at`` 做 scatter-min 更新 z-buffer，再用 ``dep == zbuf[fidx]``
  挑出获胜片元写颜色（无重复索引歧义）。
* 半透明件：先渲染不透明件得到 (cb, zb)，再把半透明件渲染到独立图层
  （自己的 z-buffer + alpha 缓冲），最后按 ``lay_zb < zb`` 的位置做 alpha 合成。

只依赖 numpy 与 PIL。
"""
from __future__ import annotations

import math
import os

import numpy as np
from PIL import Image

__all__ = ["render"]

# 光栅化批处理上限（控制峰值内存：每批元素数 ≈ 三角形数 × 包围盒像素数）
_BATCH_TRIS = 1024
_BATCH_ELEMS = 1_500_000


# --------------------------------------------------------------------- 相机

def _camera_basis(azimuth, elevation):
    """返回单位正交相机基 (right, up, forward)，世界坐标系，右手系。

    forward 从相机指向 target；right = normalize(forward × world_up)；
    up = right × forward。azimuth 绕 +Z，elevation 相对 XY 平面（度）。
    """
    az = math.radians(float(azimuth))
    el = math.radians(float(elevation))
    eye_dir = np.array([math.cos(el) * math.cos(az),
                        math.cos(el) * math.sin(az),
                        math.sin(el)], dtype=np.float64)
    forward = -eye_dir
    right = np.cross(forward, np.array([0.0, 0.0, 1.0], dtype=np.float64))
    if np.linalg.norm(right) < 1e-9:            # 正上方 / 正下方俯视的退化情形
        right = np.array([-math.sin(az), math.cos(az), 0.0], dtype=np.float64)
        if np.linalg.norm(right) < 1e-9:
            right = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    up /= np.linalg.norm(up)
    return right, up, forward


# ------------------------------------------------------------------ 输入整理

def _normalise_parts(parts):
    """把用户传入的 parts 统一成内部结构，并剔除越界/退化三角形。"""
    out = []
    if parts is None:
        return out
    for p in parts:
        v = np.asarray(p["verts"], dtype=np.float64).reshape(-1, 3)
        t = np.asarray(p["tris"], dtype=np.int64).reshape(-1, 3)
        if t.size:
            ok = ((t >= 0) & (t < v.shape[0])).all(axis=1)
            ok &= (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
            t = t[ok]
        col = np.asarray(p.get("color", (0.8, 0.8, 0.8)), dtype=np.float64).reshape(-1)
        col = np.clip(np.concatenate([col[:3], np.full(max(0, 3 - col.size), 0.8)])[:3], 0.0, 1.0)
        alpha = float(p.get("alpha", 1.0))
        alpha = min(max(alpha, 0.0), 1.0)
        out.append({"verts": v, "tris": t, "color": col, "alpha": alpha})
    return out


# --------------------------------------------------------------- 光栅化批次

def _iter_batches(xt, yt, dt, W, H):
    """按屏幕包围盒面积分桶，逐批 yield 光栅化片段。

    xt/yt: (M,3) float64 屏幕像素坐标（像素中心取整数坐标）
    dt   : (M,3) float64 深度，或 None（仅要覆盖掩码时）
    yield (fidx, dep, gtri)：
        fidx (nfrag,) int64   —— 像素线性索引（行优先，行 0 在图像顶部）
        dep  (nfrag,) float64 —— 深度（越小越近），dt 为 None 时是 None
        gtri (nfrag,) int64   —— 片段所属三角形在输入数组中的下标

    健壮性（任意 target/ortho_scale/distance 下都不越界）：
    * 只保留 |面积| > 1e-12 且 9 个坐标全为有限值的三角形；
    * 包围盒用浮点先夹到视口 [0,W-1]×[0,H-1] 再转 int64
      （避免 NaN/±inf/1e300 转整数时的未定义行为）；
    * 网格按"批次内最大包围盒"开，但每个片元还要再与**本三角形自身**的已裁剪
      包围盒取交 —— 跨视口边界的三角形在视口外仍会被判成"内部"，只用批次网格
      会生成行 ≥ H / 列 ≥ W 的像素，导致平坦索引越界（曾经的 IndexError）；
    * 行方向切成条带，保证单次分配的元素数不超过 _BATCH_ELEMS（内存上限）。
    """
    M = int(xt.shape[0])
    if M == 0:
        return
    x0, x1, x2 = xt[:, 0], xt[:, 1], xt[:, 2]
    y0, y1, y2 = yt[:, 0], yt[:, 1], yt[:, 2]
    area2 = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    ok = np.isfinite(area2) & (np.abs(area2) > 1e-12)
    ok &= np.isfinite(x0) & np.isfinite(x1) & np.isfinite(x2)
    ok &= np.isfinite(y0) & np.isfinite(y1) & np.isfinite(y2)
    if not ok.all():
        sel = np.flatnonzero(ok)
        if sel.size == 0:
            return
        xt = xt[sel]; yt = yt[sel]
        if dt is not None:
            dt = dt[sel]
        x0, x1, x2 = xt[:, 0], xt[:, 1], xt[:, 2]
        y0, y1, y2 = yt[:, 0], yt[:, 1], yt[:, 2]
        area2 = area2[sel]

    # 浮点包围盒 -> 夹到视口（NaN 参与比较恒为 False，会在 vis 里被丢掉）
    xlo = np.clip(np.floor(np.minimum(np.minimum(x0, x1), x2)), 0.0, float(W - 1))
    xhi = np.clip(np.ceil(np.maximum(np.maximum(x0, x1), x2)) - 1.0, 0.0, float(W - 1))
    ylo = np.clip(np.floor(np.minimum(np.minimum(y0, y1), y2)), 0.0, float(H - 1))
    yhi = np.clip(np.ceil(np.maximum(np.maximum(y0, y1), y2)) - 1.0, 0.0, float(H - 1))
    vis = (xlo <= xhi) & (ylo <= yhi)
    sel = np.flatnonzero(vis)
    if sel.size == 0:
        return
    x0, x1, x2 = x0[sel], x1[sel], x2[sel]
    y0, y1, y2 = y0[sel], y1[sel], y2[sel]
    xmin = xlo[sel].astype(np.int64)
    xmax = xhi[sel].astype(np.int64)
    ymin = ylo[sel].astype(np.int64)
    ymax = yhi[sel].astype(np.int64)
    area2 = area2[sel]
    if dt is not None:
        d0, d1, d2 = dt[sel, 0], dt[sel, 1], dt[sel, 2]

    area = (xmax - xmin + 1) * (ymax - ymin + 1)    # 包围盒像素数 = 光栅化开销
    order = np.argsort(area, kind="stable")        # 小三角形先渲染，批次更紧凑
    asort = area[order]
    n = int(sel.size)
    i = 0
    while i < n:
        j = min(n, i + _BATCH_TRIS)
        while j > i + 1 and (j - i) * int(asort[j - 1]) > _BATCH_ELEMS:
            j = i + max(1, (j - i) // 2)
        q = order[i:j]                              # 本批三角形（在 sel 局部坐标下）
        i = j
        gtri = sel[q]                               # 映射回原始输入下标

        xa = xmin[q]; ya = ymin[q]
        xext = xmax[q] - xa                         # 本三角形自身的网格列上限
        yext = ymax[q] - ya                         # 本三角形自身的网格行上限
        ax0, ax1, ax2 = x0[q], x1[q], x2[q]
        ay0, ay1, ay2 = y0[q], y1[q], y2[q]
        aa2 = area2[q]
        if dt is not None:
            dd0, dd1, dd2 = d0[q], d1[q], d2[q]
        wmax = int(xext.max()) + 1
        hmax = int(yext.max()) + 1

        # 内存上限：行方向切条带，使单次分配的元素数 <= _BATCH_ELEMS
        nq = int(q.size)
        rows_per = max(1, _BATCH_ELEMS // max(1, nq * wmax))

        dx = np.arange(wmax, dtype=np.float64)
        PX = (xa[:, None].astype(np.float64) + dx[None, :])[:, None, :]     # (nq,1,wmax)

        for ys0 in range(0, hmax, rows_per):
            hs = min(rows_per, hmax - ys0)
            dy = np.arange(ys0, ys0 + hs, dtype=np.float64)
            PY = (ya[:, None].astype(np.float64) + dy[None, :])[:, :, None]  # (nq,hs,1)

            e0 = (ax2 - ax1)[:, None, None] * (PY - ay1[:, None, None])
            e0 = e0 - (ay2 - ay1)[:, None, None] * (PX - ax1[:, None, None])
            e1 = (ax0 - ax2)[:, None, None] * (PY - ay2[:, None, None])
            e1 = e1 - (ay0 - ay2)[:, None, None] * (PX - ax2[:, None, None])
            e2 = (ax1 - ax0)[:, None, None] * (PY - ay0[:, None, None])
            e2 = e2 - (ay1 - ay0)[:, None, None] * (PX - ax0[:, None, None])

            # 两种绕向都接受（外法线由网格约定保证，z-buffer 负责正确遮挡）
            inside = ((e0 >= 0) & (e1 >= 0) & (e2 >= 0)) | ((e0 <= 0) & (e1 <= 0) & (e2 <= 0))
            if not inside.any():
                continue
            ii, jj, kk = np.nonzero(inside)
            # 关键：片元必须落在"本三角形自身的已裁剪包围盒"内。
            # 网格是按批次最大包围盒开的，跨视口边界的三角形在视口外仍会被判为内部，
            # 少了这一步就会生成 行 >= H / 列 >= W 的越界平坦索引。
            keep = ((jj + ys0) <= yext[ii]) & (kk <= xext[ii])
            if not keep.all():
                ii = ii[keep]; jj = jj[keep]; kk = kk[keep]
                if ii.size == 0:
                    continue
            fidx = (ya[ii] + jj + ys0) * W + (xa[ii] + kk)
            # 保险：以上裁剪已保证 fidx 合法，这里再兜一次底，越界片元直接丢弃
            if fidx.size and (int(fidx.min()) < 0 or int(fidx.max()) >= W * H):
                inb = (fidx >= 0) & (fidx < W * H)
                ii = ii[inb]; jj = jj[inb]; kk = kk[inb]; fidx = fidx[inb]
                if fidx.size == 0:
                    continue
            if dt is None:
                yield fidx, None, gtri[ii]
                continue
            ev0 = e0[ii, jj, kk]
            ev1 = e1[ii, jj, kk]
            ev2 = e2[ii, jj, kk]
            inv = 1.0 / aa2[ii]
            dep = (ev0 * dd0[ii] + ev1 * dd1[ii] + ev2 * dd2[ii]) * inv
            fin = np.isfinite(dep)                  # 深度非有限（退化/溢出）的片元丢弃，
            if not fin.all():                       # 否则 NaN 会污染 z-buffer
                ii = ii[fin]; fidx = fidx[fin]; dep = dep[fin]
                if fidx.size == 0:
                    continue
            yield fidx, dep, gtri[ii]


def _rasterize(cbx, zbx, W, H, xt, yt, dt, col, abx=None, alpha_val=1.0):
    """把三角形写入颜色缓冲 cbx((W*H,3) float32) 与深度缓冲 zbx((W*H,) float64)。

    abx 不为 None 时同时把获胜片元的 alpha 写进 abx（半透明图层用）。
    返回写入的片元数。颜色/深度都是"最近者胜"。
    """
    written = 0
    for fidx, dep, tri in _iter_batches(xt, yt, dt, W, H):
        np.minimum.at(zbx, fidx, dep)               # scatter-min 更新 z-buffer
        win = dep <= zbx[fidx]                      # == 最小值者即获胜片元
        wf = fidx[win]
        if wf.size == 0:
            continue
        cbx[wf] = col[tri[win]]
        if abx is not None:
            abx[wf] = alpha_val
        written += int(wf.size)
    return written


def _rasterize_mask(mbx, W, H, xt, yt):
    """只写覆盖掩码（阴影用）。mbx 是 (W*H,) 的 bool。"""
    for fidx, _dep, _tri in _iter_batches(xt, yt, None, W, H):
        mbx[fidx] = True


# ------------------------------------------------------------------- 着色

def _shade_normals(v, t, cam_back, lights):
    """平面着色：返回每个三角形的 (M,3) float32 颜色倍率基础上的基色乘子。

    lights = (L_key, L_fill, L_rim)。背向相机的面翻转法线（双面着色），
    避免出现全黑背面。
    """
    v0 = v[t[:, 0]]
    v1 = v[t[:, 1]]
    v2 = v[t[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 1e-12)
    flip = (n @ cam_back) < 0.0
    n *= np.where(flip[:, None], -1.0, 1.0)
    l_key, l_fill, l_rim = lights
    key = np.clip(n @ l_key, 0.0, None)
    fill = np.clip(n @ l_fill, 0.0, None)
    rim = np.clip(n @ l_rim, 0.0, None)
    shade = 0.22 + 0.68 * key + 0.16 * fill + 0.06 * rim     # 环境 + 主光 + 补光
    np.clip(shade, 0.10, 1.0, out=shade)
    return shade.astype(np.float32)


# -------------------------------------------------------------------- 保存

def _save_png(cb, RW, RH, W, H, ss, path):
    arr = np.clip(np.asarray(cb, dtype=np.float32).reshape(RH, RW, 3), 0.0, 1.0)
    img = Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8))
    if ss > 1:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img = img.resize((W, H), resample)          # 超采样降采样
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    img.save(path)


# -------------------------------------------------------------------- 主函数

def render(parts, path, size=(1000, 760), azimuth=35.0, elevation=22.0,
           target=(0.0, 0.0, 0.0), distance=None, ortho_scale=None,
           bg=(0.09, 0.10, 0.12), ssaa=2, shadows=False):
    """纯 numpy + PIL 的软件渲染器（正交投影 + z-buffer + 平面着色）。

    parts: list[dict]，每个 dict:
        {"verts": (N,3) float64, "tris": (M,3) int32, "color": (r,g,b) 0..1}
        可选 "alpha": float（默认 1.0，用于手机/被遮挡件半透明预览）
    path: 输出 PNG 路径，返回 path。
    相机：绕 target 的球坐标 —— azimuth 度（绕 Z 轴），elevation 度（相对 XY 平面）。
    distance 为 None 时自动取所有点包围球半径 * 3；ortho_scale 为 None 时自动取
    包围球直径 * 1.15（并保证所有点都在画面内）。
    size 是最终 PNG 尺寸，ssaa 是超采样倍数（内部按 size*ssaa 渲染再降采样）。
    """
    # 退化/非有限输入（NaN/±Inf 顶点、退化为零面积的三角形、镜头推进零件内部等）
    # 都是允许的：这些片元会被丢弃而不是报错，所以这里静音对应的 numpy 浮点警告
    # （否则 -W error / 严格测试环境下会变成异常）。
    with np.errstate(all="ignore"):
        return _render_impl(parts, path, size, azimuth, elevation, target, distance,
                            ortho_scale, bg, ssaa, shadows)


def _render_impl(parts, path, size, azimuth, elevation, target, distance,
                 ortho_scale, bg, ssaa, shadows):
    """render() 的实际实现（参数校验/取景/光栅化/合成/存盘）。"""
    W = int(round(float(size[0])))
    H = int(round(float(size[1])))
    if W < 1 or H < 1:
        raise ValueError("size 必须是正整数")
    ss = max(1, int(ssaa))
    RW, RH = W * ss, H * ss

    tgt = np.asarray(target, dtype=np.float64).reshape(3)
    col_bg = np.clip(np.asarray(bg, dtype=np.float64).reshape(3), 0.0, 1.0)

    pcs = _normalise_parts(parts)

    cb = np.empty((RH * RW, 3), dtype=np.float32)
    cb[:] = col_bg.astype(np.float32)
    zb = np.full(RH * RW, np.inf, dtype=np.float64)

    vlist = [p["verts"] for p in pcs if p["verts"].shape[0]]
    if not vlist:                                   # 无输入三角形：输出背景色 PNG
        _save_png(cb, RW, RH, W, H, ss, path)
        return path
    allv = np.concatenate(vlist, axis=0)

    # ---- 自动取景 ----
    lo = allv.min(axis=0)
    hi = allv.max(axis=0)
    bcenter = 0.5 * (lo + hi)
    radius = float(np.sqrt(((allv - bcenter) ** 2).sum(axis=1)).max())
    if not np.isfinite(radius) or radius <= 0.0:
        radius = 1.0
    if distance is None:
        distance = radius * 3.0
    distance = float(distance)

    right, up, fwd = _camera_basis(azimuth, elevation)
    cam_back = -fwd                                 # 由 target 指向相机（headlight 主方向）
    rel = allv - tgt
    need_x = float(np.abs(rel @ right).max())
    need_y = float(np.abs(rel @ up).max())
    if ortho_scale is None:
        # 包围球直径 * 1.15；同时保证（target 不在场景中心时）所有点都在画面内
        world_w = max(2.0 * need_x, 2.0 * need_y * (float(RW) / float(RH)), 2.0 * radius)
        ortho_scale = world_w * 1.15
    ortho_scale = float(ortho_scale)
    if not np.isfinite(ortho_scale) or ortho_scale <= 0.0:
        ortho_scale = 2.0 * radius * 1.15
    spp = float(RW) / ortho_scale                   # 每毫米多少渲染像素

    # ---- 光照：相机方向略偏上的 headlight + 侧向补光 + 反向轮廓光 ----
    l_key = cam_back + 0.45 * up + 0.25 * right
    l_key /= np.linalg.norm(l_key)
    l_fill = 0.35 * cam_back + 0.90 * right + 0.35 * up
    l_fill /= np.linalg.norm(l_fill)
    l_rim = 0.10 * cam_back - 0.80 * right - 0.50 * up
    l_rim /= np.linalg.norm(l_rim)
    lights = (l_key, l_fill, l_rim)

    def project(v):
        """世界坐标 (N,3) -> (sx, sy, depth)，图像行 0 在顶部。"""
        r = v - tgt
        xc = r @ right
        yc = r @ up
        dc = r @ fwd
        return (RW * 0.5 + xc * spp,
                RH * 0.5 - yc * spp,
                distance + dc)

    # ---- 逐件投影 + 平面着色 ----
    drawn = []
    for p in pcs:
        v, t = p["verts"], p["tris"]
        if t.shape[0] == 0:
            continue
        sx, sy, dp = project(v)
        xt = np.stack([sx[t[:, 0]], sx[t[:, 1]], sx[t[:, 2]]], axis=1)
        yt = np.stack([sy[t[:, 0]], sy[t[:, 1]], sy[t[:, 2]]], axis=1)
        dt = np.stack([dp[t[:, 0]], dp[t[:, 1]], dp[t[:, 2]]], axis=1)
        shade = _shade_normals(v, t, cam_back, lights)
        col = (p["color"][None, :] * shade[:, None]).astype(np.float32)
        ctr = v.mean(axis=0)
        drawn.append({"xt": xt, "yt": yt, "dt": dt, "col": col,
                      "alpha": p["alpha"], "verts": v, "tris": t,
                      "depth": float(distance + (ctr - tgt) @ fwd)})   # 质心深度（图层排序用）

    # ---- 可选：地面阴影（把网格沿主光方向投到 z = zmin 平面，压暗背景） ----
    if shadows and drawn:
        lz = float(l_key[2])
        if lz < -0.05:                              # 光要从上方来才能投到地面上
            zmin = float(allv[:, 2].min())
            mask = np.zeros(RH * RW, dtype=bool)
            for d in drawn:
                v = d["verts"]
                t = d["tris"]
                tt = (zmin - v[:, 2]) / lz
                gp = v + l_key[None, :] * tt[:, None]
                sx, sy, _ = project(gp)
                xt = np.stack([sx[t[:, 0]], sx[t[:, 1]], sx[t[:, 2]]], axis=1)
                yt = np.stack([sy[t[:, 0]], sy[t[:, 1]], sy[t[:, 2]]], axis=1)
                _rasterize_mask(mask, RW, RH, xt, yt)
            # 先压暗背景；随后绘制的实体/半透明件自然会盖在阴影上面
            cb[mask] *= 0.45

    # ---- 不透明件：z-buffer ----
    for d in drawn:
        if d["alpha"] >= 1.0:
            _rasterize(cb, zb, RW, RH, d["xt"], d["yt"], d["dt"], d["col"])

    # ---- 半透明件：独立图层 + 最近者胜，再按 alpha 合成 ----
    transp = [d for d in drawn if d["alpha"] < 1.0]
    if transp:
        transp.sort(key=lambda d: -d["depth"])      # 远处的先画（稳定的确定性顺序）
        lay_cb = np.empty_like(cb)
        lay_cb[:] = col_bg.astype(np.float32)
        lay_zb = np.full(RH * RW, np.inf, dtype=np.float64)
        lay_a = np.zeros(RH * RW, dtype=np.float32)
        for d in transp:
            _rasterize(lay_cb, lay_zb, RW, RH, d["xt"], d["yt"], d["dt"], d["col"],
                       abx=lay_a, alpha_val=d["alpha"])
        front = lay_zb < zb                         # 半透明面在不透明面之前才可见
        a = (lay_a * front).astype(np.float32)[:, None]
        cb = cb * (1.0 - a) + lay_cb * a

    _save_png(cb, RW, RH, W, H, ss, path)
    return path


# ===================================================================== 自测

if __name__ == "__main__":
    import sys
    import time

    _HERE = os.path.dirname(os.path.abspath(__file__))
    OUT = os.path.abspath(os.path.join(_HERE, "..", "out"))
    os.makedirs(OUT, exist_ok=True)

    # ---------------------------------------------------------- 测试网格生成
    def _fix_winding(verts, tris):
        """闭合网格外法线朝外的检查：散度定理体积为负则翻转绕向。"""
        v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
        vol = float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)
        if vol < 0:
            tris = tris[:, [0, 2, 1]]
            vol = -vol
        return tris, vol

    def box(center, size):
        c = np.asarray(center, float)
        h = np.asarray(size, float) * 0.5
        sgn = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
        v = c + (sgn * 2.0 - 1.0) * h
        q = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        t = []
        for a, b, cc, d in q:
            t += [[a, b, cc], [a, cc, d]]
        t = np.asarray(t, np.int32)
        t, _ = _fix_winding(v, t)
        return v, t

    def uv_sphere(center, r, nu, nv):
        c = np.asarray(center, float)
        K = nv - 1
        u = np.arange(nu) * (2.0 * np.pi / nu)
        vv = np.linspace(-np.pi / 2, np.pi / 2, nv + 1)[1:-1]
        uu, vg = np.meshgrid(u, vv, indexing="ij")          # (nu,K)
        ring = np.stack([np.cos(vg) * np.cos(uu), np.cos(vg) * np.sin(uu), np.sin(vg)], -1)
        ring = ring.reshape(-1, 3)
        P = np.empty((K * nu + 2, 3))
        P[0] = (0.0, 0.0, -1.0)
        P[1 + K * nu] = (0.0, 0.0, 1.0)
        P[1:1 + K * nu] = ring
        verts = c + r * P
        row = (np.arange(K * nu).reshape(nu, K) + 1)        # row[i,k]
        i = np.arange(nu)
        i2 = (i + 1) % nu
        south = np.zeros(nu, np.int32)
        north = np.full(nu, K * nu + 1, np.int32)
        t1 = np.stack([south, row[i, 0], row[i2, 0]], 1)
        a0 = row[i][:, :-1]; a0b = row[i2][:, :-1]
        a1 = row[i][:, 1:]; a1b = row[i2][:, 1:]
        t2 = np.concatenate([np.stack([a0, a1, a1b], -1), np.stack([a0, a1b, a0b], -1)], 0)
        t3 = np.stack([north, row[i2, -1], row[i, -1]], 1)
        t = np.concatenate([t1, t2.reshape(-1, 3), t3], 0).astype(np.int32)
        t, _ = _fix_winding(verts, t)
        return verts, t

    def cylinder(center, r, h, nseg, nz=1):
        c = np.asarray(center, float)
        th = np.arange(nseg) * (2.0 * np.pi / nseg)
        zs = np.linspace(-h * 0.5, h * 0.5, nz + 1)
        side = np.stack([np.cos(th), np.sin(th)], -1)       # (nseg,2)
        P = []
        idx = np.zeros((nz + 1, nseg), np.int32)
        for k in range(nz + 1):
            idx[k] = len(P) + np.arange(nseg)
            P += [c + np.array([side[j, 0] * r, side[j, 1] * r, zs[k]]) for j in range(nseg)]
        P = np.asarray(P, float)
        cbot = len(P); P = np.vstack([P, c + np.array([0, 0, zs[0]])])
        ctop = len(P); P = np.vstack([P, c + np.array([0, 0, zs[-1]])])
        i = np.arange(nseg); i2 = (i + 1) % nseg
        tris = []
        for k in range(nz):
            a0 = idx[k][i]; a0b = idx[k][i2]
            a1 = idx[k + 1][i]; a1b = idx[k + 1][i2]
            tris.append(np.stack([a0, a0b, a1b], 1))
            tris.append(np.stack([a0, a1b, a1], 1))
        tris.append(np.stack([np.full(nseg, cbot), idx[0][i2], idx[0][i]], 1))
        tris.append(np.stack([np.full(nseg, ctop), idx[-1][i], idx[-1][i2]], 1))
        t = np.concatenate(tris, 0).astype(np.int32)
        t, vol = _fix_winding(P, t)
        return P, t, vol

    def mesh_vol(verts, tris):
        v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
        return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)

    COLORS = [(0.78, 0.26, 0.22), (0.22, 0.58, 0.85), (0.88, 0.72, 0.20)]
    BG = (0.09, 0.10, 0.12)

    def classify(arr):
        """按颜色方向把像素分类：返回 (bg_mask, label_masks, labels)。

        物体像素 = 基色 × 标量亮度，所以方向（单位向量）与基色一致；
        用单位向量余弦 > 0.995（约 5.7°）判定，抗 8bit 量化与抗锯齿混色。
        """
        flat = arr.reshape(-1, 3)
        is_bg = np.linalg.norm(flat - np.asarray(BG), axis=1) < 0.035
        lum = np.linalg.norm(flat, axis=1)
        unit = np.divide(flat, lum[:, None], out=np.zeros_like(flat), where=lum[:, None] > 1e-9)
        cos = np.stack([unit @ (np.asarray(c, float) / np.linalg.norm(c)) for c in COLORS], 1)
        best = cos.argmax(1)
        labels = np.where((cos.max(1) > 0.995) & (lum > 0.08) & (~is_bg),
                          best, -1).astype(np.int8)
        return is_bg, [labels == k for k in range(len(COLORS))], labels

    def n_components(mask, W=1000, min_size=100):
        """4 连通域个数（种子 BFS）；mask 是扁平的 (H*W,) 布尔数组。
        先做 1 像素膨胀再统计，并忽略 < min_size 的碎屑（降采样振铃像素）。"""
        mm = mask.reshape(-1, W)
        d = mm.copy()
        d[1:, :] |= mm[:-1, :]; d[:-1, :] |= mm[1:, :]
        d[:, 1:] |= mm[:, :-1]; d[:, :-1] |= mm[:, 1:]
        d[1:, 1:] |= mm[:-1, :-1]; d[:-1, :-1] |= mm[1:, 1:]
        d[1:, :-1] |= mm[:-1, 1:]; d[:-1, 1:] |= mm[1:, :-1]
        dm = d.reshape(-1)
        seen = np.zeros(dm.size, dtype=bool)
        sizes = []
        for p0 in np.flatnonzero(dm).tolist():
            if seen[p0]:
                continue
            seen[p0] = True
            stack = [p0]
            n = 0
            while stack:
                p = stack.pop()
                n += 1
                py, px = divmod(p, W)
                for q in (p - W, p + W, p - 1, p + 1):
                    if q < 0 or q >= dm.size:
                        continue
                    qy, qx = divmod(q, W)
                    if abs(qy - py) + abs(qx - px) != 1:
                        continue
                    if dm[q] and not seen[q]:
                        seen[q] = True
                        stack.append(q)
            sizes.append(n)
        return len([s for s in sizes if s >= min_size])

    def load(path):
        im = Image.open(path).convert("RGB")
        return np.asarray(im, np.float32) / 255.0, im.size

    def fit_ortho(parts, az, el, tgt=(0, 0, 0), W=1000, H=760, ss=2):
        """复现 render() 的自动取景，便于多张图用同一投影做对比。"""
        allv = np.concatenate([p["verts"] for p in parts], 0)
        r_, u_, f_ = _camera_basis(az, el)
        rel = allv - np.asarray(tgt, float)
        rad = np.sqrt(((allv - 0.5 * (allv.min(0) + allv.max(0))) ** 2).sum(1)).max()
        return max(2 * np.abs(rel @ r_).max(),
                   2 * np.abs(rel @ u_).max() * (W * ss) / (H * ss),
                   2 * rad) * 1.15

    results = {}
    print("=" * 74)
    print("flipdeck/render.py 自测   (numpy %s, PIL %s)" % (np.__version__, Image.__version__))
    print("输出目录:", OUT)
    print("=" * 74)

    # ---------------------------------------------------------------- 自测 1
    print("\n[自测 1] 立方体 + 球 + 圆柱（三色、位置错开），1000x760")
    vc, tc = box((0, 0, 0), (40, 40, 40))
    vs, ts = uv_sphere((0, 0, 0), 20, 48, 32)
    vy, ty, voly = cylinder((0, 0, 0), 13, 34, 48, 4)
    print("   网格：立方体 %d tri (V=%.1f)，球 %d tri (V=%.1f, 真值 %.1f)，"
          "圆柱 %d tri (V=%.1f, 真值 %.1f)"
          % (len(tc), mesh_vol(vc, tc), len(ts), mesh_vol(vs, ts), 4 / 3 * np.pi * 20 ** 3,
             len(ty), voly, np.pi * 13 ** 2 * 34))

    R, U, F = _camera_basis(35.0, 22.0)
    off_c = -62.0 * R
    off_y = 62.0 * R
    sc = (vc + off_c, tc, COLORS[0])
    ss_ = (vs + np.array([0.0, 0.0, 26.0]), ts, COLORS[1])
    sy_ = (vy + off_y, ty, COLORS[2])
    scene1 = [{"verts": a, "tris": b, "color": c} for a, b, c in (sc, ss_, sy_)]
    # 合成图与"单件图"使用同一投影，才能逐物体核对可见像素数
    OS1 = fit_ortho(scene1, 35.0, 22.0)
    cam1 = dict(size=(1000, 760), azimuth=35.0, elevation=22.0, bg=BG,
                ssaa=2, ortho_scale=OS1)
    p1 = os.path.join(OUT, "selftest_render.png")
    t0 = time.time()
    render(scene1, p1, **cam1)
    dt1 = time.time() - t0
    img1, sz1 = load(p1)
    is_bg, masks, labels = classify(img1)
    nbg = float((~is_bg).mean())
    cnt = [int(m.sum()) for m in masks]
    print("   文件: %s  存在=%s  尺寸=%s  耗时=%.2f s"
          % (p1, os.path.exists(p1), sz1, dt1))
    print("   非背景像素占比 = %.2f%%  (要求 > 3%%)" % (nbg * 100))
    for k, nm in enumerate(("立方体(红)", "球(蓝)", "圆柱(黄)")):
        print("   %s 像素数 = %d  (要求 > 500)" % (nm, cnt[k]))

    # 互不重叠：单独渲染每个物体（同一投影），比较可见像素数是否一致
    solo_cnt = []
    for k, one in enumerate(scene1):
        pk = os.path.join(OUT, "selftest_solo_%d.png" % k)
        render([one], pk, **cam1)
        imk, _ = load(pk)
        _, msk, _ = classify(imk)
        solo_cnt.append(int(msk[k].sum()))
    same = [abs(cnt[k] - solo_cnt[k]) <= max(0.02 * solo_cnt[k], 40) for k in range(3)]
    # 三个物体的屏幕包围盒两两不相交（说明确实错开摆放）
    bb = []
    for m in masks:
        ys, xs = np.nonzero(m.reshape(760, 1000))
        bb.append((int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())))
    disjoint = all(bb[i][1] < bb[j][0] or bb[j][1] < bb[i][0] or
                   bb[i][3] < bb[j][2] or bb[j][3] < bb[i][2]
                   for i in range(3) for j in range(i + 1, 3))
    ncomp = [n_components(m) for m in masks]
    print("   单件图可见像素 = %s  与合成图一致（差异<2%%）= %s" % (solo_cnt, same))
    print("   屏幕包围盒 = %s" % (bb,))
    print("   每种颜色连通域个数(>=100px) = %s (各为 1 表示单块、无碎片杂色)" % ncomp)
    ok1 = all(same) and all(c > 500 for c in cnt) and nbg > 0.03
    print("   -> 三物体互不重叠且都完整可见: %s" % (ok1 and all(n == 1 for n in ncomp)))

    # 上下方向：球在 +Z 上方，应出现在图像更靠上的位置
    mean_rows = [float(np.flatnonzero(m).mean() // 1000) for m in masks]
    print("   图像平均行号（越小越靠上）: 立方体=%.1f 球=%.1f 圆柱=%.1f  -> 球在立方体上方: %s"
          % (mean_rows[0], mean_rows[1], mean_rows[2], mean_rows[1] < mean_rows[0]))

    # 立体感：同一物体内部亮度差异
    for k, nm in enumerate(("立方体", "球", "圆柱")):
        px = img1.reshape(-1, 3)[masks[k]]
        lum = px @ np.array([0.299, 0.587, 0.114])
        print("   %s 亮度: min=%.3f p10=%.3f p50=%.3f p90=%.3f max=%.3f  极差=%.3f"
              % (nm, lum.min(), np.percentile(lum, 10), np.percentile(lum, 50),
                 np.percentile(lum, 90), lum.max(), lum.max() - lum.min()))
    uniq = len(np.unique((img1 * 255).astype(np.uint8).reshape(-1, 3), axis=0))
    unlab = int(((~is_bg) & (labels < 0)).sum())
    print("   图像内不同 RGB 值 = %d（纯色块只会有个位数）；抗锯齿过渡像素 = %d"
          % (uniq, unlab))
    # 球的上半部分应比下半部分亮（headlight 略偏上）
    sy_, sx_ = np.nonzero(masks[1].reshape(760, 1000))
    lums = ((img1 @ np.array([0.299, 0.587, 0.114])).reshape(-1))[sy_ * 1000 + sx_]
    midr = float(sy_.mean())
    up_l = lums[sy_ < midr].mean()
    lo_l = lums[sy_ >= midr].mean()
    print("   球上半/下半平均亮度 = %.3f / %.3f -> 上方偏亮: %s" % (up_l, lo_l, up_l > lo_l))
    results["selftest1"] = dict(non_bg=nbg, counts=cnt, time=dt1, size=sz1)

    # 方向核对（防止上下颠倒 / 左右镜像）：单件放在已知世界坐标，看它落在图像哪一侧
    print("\n[自测 1b] 方向核对：+Z 应在上方，世界 +Y 应在右侧（azimuth=35°、elevation=22°）")
    axis_ok = []
    for label, off, cond in (("+Z(0,0,80)", (0, 0, 80), lambda r, c: r < 380 and abs(c - 500) < 40),
                             ("-Z(0,0,-80)", (0, 0, -80), lambda r, c: r > 380 and abs(c - 500) < 40),
                             ("+Y(0,80,0)", (0, 80, 0), lambda r, c: c > 640),
                             ("+X(80,0,0)", (80, 0, 0), lambda r, c: c < 360)):
        vm, tm = box(off, (16, 16, 16))
        pa = os.path.join(OUT, "selftest_axis_%s.png" % label[:2].replace("+", "p").replace("-", "m"))
        render([{"verts": vm, "tris": tm, "color": COLORS[1]}], pa,
               size=(1000, 760), azimuth=35.0, elevation=22.0, bg=BG, ssaa=2)
        a, _ = load(pa)
        m = np.linalg.norm(a.reshape(-1, 3) - np.asarray(BG), axis=1) >= 0.035
        ys, xs = np.nonzero(m.reshape(760, 1000))
        r, c = (float(ys.mean()), float(xs.mean())) if ys.size else (-1, -1)
        good = ys.size > 100 and cond(r, c)
        axis_ok.append(good)
        print("   %-11s 质心=(行 %.0f, 列 %.0f) 像素=%5d -> %s"
              % (label, r, c, int(ys.size), "通过" if good else "未通过"))
    results["axes_ok"] = all(axis_ok)

    # ---------------------------------------------------------------- 自测 2
    print("\n[自测 2] 性能：~150k 三角形，1000x760，ssaa=2")
    vS, tS = uv_sphere((-62, 0, 0), 58, 320, 121)          # 2*320*120 = 76800 tri
    vS2, tS2 = uv_sphere((62, 0, 0), 58, 320, 121)         # 76800 tri
    scene2 = scene1 + [{"verts": vS, "tris": tS, "color": (0.35, 0.75, 0.50)},
                       {"verts": vS2, "tris": tS2, "color": (0.65, 0.45, 0.85)}]
    nt = sum(len(p["tris"]) for p in scene2)
    p2 = os.path.join(OUT, "selftest_perf.png")
    t0 = time.time()
    render(scene2, p2, size=(1000, 760), azimuth=35.0, elevation=22.0, bg=BG, ssaa=2)
    dt2 = time.time() - t0
    img2, sz2 = load(p2)
    print("   三角形总数 = %d，尺寸 = %s，耗时 = %.2f s  (要求 < 60 s)" % (nt, sz2, dt2))
    is_bg2, _, _ = classify(img2)
    print("   非背景像素占比 = %.2f%%（150k 三角形铺满画面大半，属于偏重的场景）"
          % ((~is_bg2).mean() * 100))
    results["selftest2"] = dict(ntris=nt, time=dt2)

    # ---------------------------------------------------------------- 自测 3
    print("\n[自测 3] z-buffer：球在立方体前面 / 后面")
    cube = {"verts": vc, "tris": tc, "color": COLORS[0]}
    EYE = np.array([np.cos(np.radians(22)) * np.cos(np.radians(35)),
                    np.cos(np.radians(22)) * np.sin(np.radians(35)),
                    np.sin(np.radians(22))])
    # 用立方体场景的自动取景，保证前后两张图投影完全一致
    OS3 = fit_ortho([cube], 35.0, 22.0)
    cam = {"size": (1000, 760), "azimuth": 35.0, "elevation": 22.0,
           "bg": BG, "ssaa": 2, "ortho_scale": OS3}

    def render3(parts, name):
        pth = os.path.join(OUT, name)
        render(parts, pth, **cam)
        a, _ = load(pth)
        bgm, msk, _ = classify(a)
        return a, bgm, msk

    a_solo_c, _, mk_c = render3([cube], "selftest_zbuffer_cube.png")
    m_cube = mk_c[0]
    ok_report = []
    for tag, sgn, expect in (("front", +1, "球"), ("back", -1, "立方体")):
        sphc = {"verts": vs + EYE * (34.0 * sgn), "tris": ts, "color": COLORS[1]}
        a_solo_s, _, mk_s = render3([sphc], "selftest_zbuffer_sphere_%s.png" % tag)
        m_sph = mk_s[1]
        a_both, _, mk_b = render3([cube, sphc], "selftest_zbuffer_both_%s.png" % tag)
        m_sph_b, m_cube_b = mk_b[1], mk_b[0]
        ov = int((m_cube & m_sph).sum())
        if expect == "球":
            hit = int((m_sph & m_sph_b).sum()) / max(1, int(m_sph.sum()))
            verdict = hit >= 0.98
            print("   球在前: 重叠区=%d px；球轮廓内仍为球色的比例=%.4f (要求>=0.98) -> %s"
                  % (ov, hit, "通过" if verdict else "未通过"))
            print("           重叠区里被立方体覆盖的像素=%d (要求≈0) -> %s"
                  % (int((m_sph & m_cube_b).sum()),
                     "通过" if int((m_sph & m_cube_b).sum()) <= 0.01 * ov else "未通过"))
        else:
            hit = int((m_sph & m_cube_b).sum()) / max(1, ov)
            verdict = hit >= 0.9
            print("   球在后: 重叠区=%d px；重叠区被立方体遮挡的比例=%.4f (要求>=0.9) -> %s"
                  % (ov, hit, "通过" if verdict else "未通过"))
        ok_report.append(verdict)
        results["selftest3_" + tag] = dict(overlap=ov)
    print("   (重叠区 %d px，说明两张投影确实互相覆盖，测试非空转)" % int((m_cube & m_sph).sum()))
    results["selftest3_ok"] = all(ok_report)

    # ---------------------------------------------------------------- 自测 4
    print("\n[自测 4] 自定义 target/ortho_scale（镜头推进零件内部）+ 越界健壮性")
    # 最小复现用例：跨视口下边界的薄三角形 + 同批次的大三角形（旧代码在此 IndexError）
    Wz, Hz = 2000, 1520
    cbx = np.zeros((Wz * Hz, 3), dtype=np.float32)
    zbx = np.full(Wz * Hz, np.inf, dtype=np.float64)
    xt_min = np.array([[10.0, 810.0, 10.0], [100.0, 101.0, 102.0]])
    yt_min = np.array([[10.0, 10.0, 810.0], [1519.5, 5000.0, 1519.5]])
    _rasterize(cbx, zbx, Wz, Hz, xt_min, yt_min, np.zeros((2, 3)),
               np.ones((2, 3), dtype=np.float32))
    print("   最小复现用例(跨下边界三角形, 旧代码 IndexError): 已安全通过, 无越界访问")

    def read_bin_stl(path):
        """自测用的极简二进制 STL 读取（不依赖 meshlib）。失败返回 None。"""
        try:
            with open(path, "rb") as fh:
                fh.read(80)
                raw = fh.read(4)
                if len(raw) < 4:
                    return None
                n = int(np.frombuffer(raw, np.uint32)[0])
                rec = fh.read(50 * n)
                if len(rec) != 50 * n or n <= 0:
                    return None
            rec = np.frombuffer(rec, np.uint8).reshape(n, 50)
            v = np.frombuffer(rec[:, 12:48].tobytes(), np.float32)
            v = v.reshape(n, 3, 3).astype(np.float64).reshape(-1, 3)
            return v, np.arange(3 * n, dtype=np.int64).reshape(n, 3)
        except OSError:
            return None

    # 零件 STL 在各自局部坐标系里，按 poses.py 的 θ=110/φ=0（展开游戏）姿态摆放
    xf_pose, pose_src = (lambda nm, w: w), "原始坐标"
    try:
        sys.path.insert(0, os.path.dirname(_HERE))
        from flipdeck import poses as _PS
        def xf_pose(nm, w, _p=_PS):
            T = _p.pose(nm, 110.0, 0.0)
            return w @ T[:3, :3].T + T[:3, 3]
        pose_src = "poses.pose(θ=110, φ=0)"
    except Exception as exc:                        # 姿态模块不可用时退回原始坐标
        print("   (提示: 未能导入 poses.py: %s -> 使用原始坐标)" % exc)

    stl_dir = os.path.join(OUT, "stl")
    stl_parts = []
    for nm, col in (("lid", COLORS[0]), ("cradle", COLORS[1])):
        got = read_bin_stl(os.path.join(stl_dir, nm + ".stl"))
        if got is None:
            break
        stl_parts.append({"verts": xf_pose(nm, got[0]), "tris": got[1], "color": col})

    if len(stl_parts) != 2:
        print("   跳过: 找不到 %s\\{lid,cradle}.stl" % stl_dir)
    else:
        ntri = sum(len(p["tris"]) for p in stl_parts)
        zoom = dict(size=(1000, 760), azimuth=52.0, elevation=12.0,
                    target=(85.0, 67.0, 131.0), ortho_scale=95.0)
        p4 = os.path.join(OUT, "selftest_zoom_target.png")
        t0 = time.time()
        render(stl_parts, p4, **zoom)               # 修复前这里抛 IndexError
        dt4 = time.time() - t0
        img4, sz4 = load(p4)
        is_bg4, _, _ = classify(img4)
        nbg4 = float((~is_bg4).mean())
        print("   零件 = lid.stl + cradle.stl (%d 三角形, 姿态 %s)" % (ntri, pose_src))
        print("   参数 = azimuth=52 elevation=12 target=(85,67,131) ortho_scale=95")
        print("   文件 %s 存在=%s 尺寸=%s 耗时=%.2f s" % (p4, os.path.exists(p4), sz4, dt4))
        print("   非背景像素占比 = %.2f%%  (要求 > 3%%) -> %s"
              % (nbg4 * 100, "通过" if nbg4 > 0.03 else "未通过"))
        results["selftest4"] = dict(non_bg=nbg4, tris=ntri, time=dt4)

        # 20 组 target/ortho_scale（含 target 在模型外 500mm、ortho_scale 5..5000）
        rng = np.random.default_rng(20250913)
        combos = [(52.0, 12.0, (85.0, 67.0, 131.0), 95.0),        # 你的原始参数
                  (52.0, 12.0, (585.0, 67.0, 131.0), 95.0),       # target 在模型外 500mm(+X)
                  (52.0, 12.0, (85.0, 67.0, -369.0), 5.0),        # ortho_scale=5（极近）
                  (52.0, 12.0, (0.0, 0.0, 0.0), 5000.0)]          # ortho_scale=5000（极远）
        while len(combos) < 20:
            combos.append((float(rng.uniform(-180.0, 180.0)), float(rng.uniform(-89.0, 89.0)),
                           (float(rng.uniform(-500.0, 560.0)), float(rng.uniform(-500.0, 560.0)),
                            float(rng.uniform(-500.0, 560.0))),
                           float(10.0 ** rng.uniform(np.log10(5.0), np.log10(5000.0)))))
        combos = combos[:20]
        ps = os.path.join(OUT, "selftest_zoom_sweep.png")
        ok_n = 0
        for (az, el, tg, osc) in combos:
            try:
                render(stl_parts, ps, size=(640, 480), azimuth=az, elevation=el,
                       target=tg, ortho_scale=osc, ssaa=2)
                ok_n += 1
            except Exception as exc:
                print("   未通过: az=%.1f el=%.1f target=(%.0f,%.0f,%.0f) ortho_scale=%.1f -> %s: %s"
                      % (az, el, tg[0], tg[1], tg[2], osc, type(exc).__name__, exc))
        print("   target/ortho_scale 扫描（640x480 ssaa=2）: %d/20 通过"
              "（含 target 在模型外 500mm、ortho_scale 从 5 到 5000）" % ok_n)
        # 极端参数也不崩：把镜头完全推进零件内部 / 模型一半在画面外 / 退化 ortho_scale
        hard = [(52.0, 12.0, (85.0, 67.0, 131.0), 5.0),
                (52.0, -89.0, (0.0, 0.0, 0.0), 5000.0),
                (0.0, 0.0, (85.0, 67.0, 131.0), 1e-3),
                (180.0, 90.0, (1e4, -1e4, 1e4), 1e6)]
        h_ok = 0
        for (az, el, tg, osc) in hard:
            try:
                render(stl_parts, ps, size=(320, 240), azimuth=az, elevation=el,
                       target=tg, ortho_scale=osc, ssaa=1, distance=1e-6)
                h_ok += 1
            except Exception as exc:
                print("   极端参数未通过 ortho_scale=%g: %s: %s" % (osc, type(exc).__name__, exc))
        print("   极端参数（ortho_scale 1e-3/1e6、target 1e4、elevation ±90、distance 1e-6）: %d/4 通过" % h_ok)

    # ---------------------------------------------------------------- 半透明
    print("\n[附加] 半透明件（alpha=0.45）与空场景健壮性")
    pc = os.path.join(OUT, "selftest_alpha.png")
    render([{"verts": vc, "tris": tc, "color": COLORS[0]},
            {"verts": vs + EYE * 34.0, "tris": ts, "color": COLORS[1], "alpha": 0.45}],
           pc, **cam)
    a_al, _ = load(pc)
    print("   半透明图存在=%s 非背景占比=%.2f%%"
          % (os.path.exists(pc), (np.linalg.norm(a_al.reshape(-1, 3) - np.asarray(BG), axis=1)
                                  >= 0.035).mean() * 100))
    pe = os.path.join(OUT, "selftest_empty.png")
    render([], pe, size=(320, 240), bg=BG)
    a_e, sz_e = load(pe)
    print("   空场景: 存在=%s 尺寸=%s 纯背景色=%s"
          % (os.path.exists(pe), sz_e, bool(np.allclose(a_e, np.asarray(BG), atol=3 / 255))))

    print("\n" + "=" * 74)
    print("汇总: 非背景占比 %.2f%% | 颜色像素 %s | 耗时(测试1) %.2fs (测试2) %.2fs"
          % (nbg * 100, cnt, dt1, dt2))
    print("=" * 74)
