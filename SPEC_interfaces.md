# flipdeck-cad 接口规范（内部约定，勿改）

## 环境事实
- Windows。Python 解释器：`C:\Softwares\miniconda3\python.exe`（3.12.9）
- **可用依赖只有 `numpy` 和 `PIL`（Pillow）**。没有 scipy / trimesh / manifold3d / matplotlib / cadquery / OpenSCAD / FreeCAD。
- **没有网络**：`pip install` 会超时，绝对不要尝试安装任何包。
- 运行脚本用 pwsh，例如：`& "C:\Softwares\miniconda3\python.exe" "C:\myFiles\codes\deepseek\flipdeck-cad\flipdeck\meshlib.py"`
- 文件沙箱是 workspace-write：只能写 `C:\myFiles\codes\deepseek\` 下的文件。
- 工作目录：`C:\myFiles\codes\deepseek\flipdeck-cad\`

## 通用几何约定
- 单位 **毫米**，浮点。
- 右手坐标系，**Z 轴朝上**。
- 网格 = `(verts, tris)`：`verts` 是 `np.ndarray` shape `(N,3)` float64；`tris` 是 `np.ndarray` shape `(M,3)` int32，索引 `verts`，**三角形顶点从外部看是逆时针（外法线朝外）**。
- 每个网格必须是**闭合、水密、流形**（每条边恰好被 2 个三角形共享），不能有孤立顶点或退化三角形。

## SDF 约定（两个文件都要遵守）
一个 SDF 是 `f(p: np.ndarray[(...,3)]) -> np.ndarray[...]`，返回有符号距离：
- `f < 0` 在实体内部，`f > 0` 在外部，单位毫米。
- 必须支持任意形状的 broadcast 输入网格，即内部所有运算都用 numpy 向量化（不要写 python 逐点循环）。
- 不要求严格精确的欧氏距离，但**零等值面必须准确**（这是切出来的面）。

## 文件 A：`flipdeck/meshlib.py`（负责人 A）
只创建/修改这一个文件，不要碰别的文件。

```python
def sample_grid(sdf, lo, hi, voxel):
    """在 p = lo + (i,j,k)*voxel 的规则网格上求 sdf。
    lo/hi: (3,) 世界坐标 mm；voxel: float mm。
    返回 (F, axes) 。"""
    # F: np.ndarray shape (nx,ny,nz) float32/float64 = sdf(p) 在网格点的值
    # 网格点数满足 nx = int(ceil((hi[0]-lo[0])/voxel)) + 1，其余轴同理
    # axes: (xs, ys, zs) 一维坐标数组，长度分别 nx, ny, nz

def surface_nets(sdf, lo, hi, voxel):
    """SDF -> 闭合三角网格（Surface Nets / Naive Dual Contouring 风格）。
    返回 (verts, tris)：verts (N,3) float64，tris (M,3) int32，满足上面"通用几何约定"。
    - 每个跨越零等值面的体素单元最多产生 1 个顶点（顶点取该单元内 12 条棱上交点的平均）。
    - 四边形的生成与绕向必须保证外法线朝外。
    - 必须闭合：不允许边界处出现开放边（在 lo/hi 周围留出足够 padding，使零等值面
      完全落在采样区域内；如果零等值面碰到采样边界，可以抛 ValueError 提示扩大 bounds）。"""

def write_stl(path, verts, tris):
    """写二进制 STL（80 字节头 + uint32 三角形数 + 每个三角形 50 字节）。返回 path。"""

def read_stl(path):
    """读回二进制 STL，返回 (verts, tris)。仅用于自测/验证。"""

def mesh_stats(verts, tris):
    """返回 dict，至少包含：
    n_tris(int), n_verts(int), volume(float, 由散度定理算出的闭合体积 mm^3),
    watertight(bool), bbox(tuple((6,) 的 min/max)),
    boundary_edges(int, 只被 1 个三角形共享的边数),
    nonmanifold_edges(int, 被 >2 个三角形共享的边数)"""

def voxel_occupancy(sdf, lo, hi, voxel):
    """返回 (occ, axes)，occ = (F < 0) 的布尔体素（在体素中心求值）。供干涉检查用。"""
```

自测要求（写在文件末尾 `if __name__ == "__main__":`，必须真的跑一遍并把结果打印出来）：
1. 球：`sdf = |p| - 20`，bounds `[-25,-25,-25]..[25,25,25]`，voxel 0.5：`mesh_stats` 的
   - `watertight is True`，`boundary_edges == 0`，`nonmanifold_edges == 0`
   - 体积误差 < 2%（真值 4/3·π·20³ ≈ 33510）
   - bbox 每个方向与 ±20 的偏差 < 1 个 voxel
2. 立方体 `max(|x|-15,|y|-10,|z|-5)`，voxel 0.5：体积误差 < 3%（真值 30·20·10=6000），水密。
3. 一个带 Ø4 通孔的方块（`union(box, -cylinder(r=2))`，用最朴素写法即可）：水密，且孔在 STL 里确实存在（可在孔轴线上采样网格点做点-内部判定，或用 `voxel_occupancy` 检查轴心处为空）。
4. 性能：一个 170×90×25 mm 的盒子，voxel 0.4，`surface_nets` 在 60 秒内完成，且内存不炸。

交付时在最终回复里给出：文件路径、每个自测的实测数字（体积误差、水密性、耗时）、以及任何未达标项。

## 文件 B：`flipdeck/render.py`（负责人 B）
只创建/修改这一个文件，不要碰别的文件。

```python
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
    包围球直径 * 1.15。size 是最终 PNG 尺寸，ssaa 是超采样倍数（内部按
    size*ssaa 渲染再降采样）。
    要求：
    - 平面着色：面法线 · 光照方向，光照方向来自相机方向略偏上（headlight）+
      环境光 + 一点侧向补光，让相邻面能看出层次；不要出现全黑或全白。
    - z-buffer 正确（近处遮挡远处），不透明件之间不能互相穿帮。
    - 用 PIL 做降采样（Image.resize 用 LANCZOS）和 PNG 保存。
    - 无输入三角形时也要能正常输出一张背景色 PNG。"""
```

自测要求（同样写在 `if __name__ == "__main__":`，必须跑并把结果打印/落盘）：
1. 渲染一个立方体 + 一个球 + 一个圆柱（三个不同颜色，位置错开），存到 `out/selftest_render.png`（1000×760），要求：
   - 文件存在、尺寸正确
   - 图像不是纯背景色（统计非背景像素占比 > 3%）
   - 三个物体在图像上互不重叠且都可见（可用连通域或颜色计数粗略验证：每种颜色像素数 > 500）
2. 渲染性能：把上面的场景复制成约 150k 三角形（可网格细分或平铺），要求 1000×760、ssaa=2 下 60 秒内完成，并打印耗时。
3. 验证 z-buffer：球放在立方体前面时，球的像素不被立方体覆盖（颜色计数验证）。

交付时在最终回复里给出：文件路径、自测数字（非背景像素占比、各颜色像素数、耗时）、以及任何未达标项。

## 禁止事项
- 不要 pip install，不要联网，不要用 OpenSCAD/FreeCAD（不存在）。
- 不要修改 `SPEC_interfaces.md`、`params.py`、`sdf.py`、`parts.py`、`poses.py`、`build.py`（这些由别人负责）。
- 不要读/改 `out/` 里除自己自测产物以外的文件。
