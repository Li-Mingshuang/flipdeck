"""把 PROGRESS.md 里的"当前状态"区块按仓库真实状态重写（幂等，可反复跑）。

用法： python status.py
区块由 <!-- AUTO:STATUS:BEGIN --> 和 <!-- AUTO:STATUS:END --> 包住，只替换这一段，
其余文字（计划、决策、待办）是手写的，脚本不碰。
"""

from __future__ import annotations
import datetime as dt
import glob
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
PROGRESS = os.path.join(ROOT, "PROGRESS.md")
BEGIN = "<!-- AUTO:STATUS:BEGIN -->"
END = "<!-- AUTO:STATUS:END -->"


def read(path, limit=None):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        txt = fh.read()
    return txt[:limit] if limit else txt


def final_line(txt, key="结论"):
    if not txt:
        return None
    for line in reversed(txt.splitlines()):
        if key in line:
            return re.sub(r"^\W+", "", line).strip()
    return None


def part_table():
    """返回 (表格行, 水密通过数, 总数)。"""
    txt = read("out/BUILD_NOTES.md")
    if not txt:
        return ["（还没跑过 build.py）"], 0, 0
    rows, inside, ok, total = [], False, 0, 0
    for line in txt.splitlines():
        if line.startswith("## 零件"):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 6 and cells[0] not in ("零件", "") and not set(cells[0]) <= set("-: "):
                total += 1
                ok += 1 if "✔" in cells[4] else 0
                name = cells[0]
                stl = cells[10] if len(cells) > 10 else "-"
                stl_mb = ""
                p = os.path.join(ROOT, "out", "stl", stl) if stl.endswith(".stl") else ""
                if p and os.path.exists(p):
                    stl_mb = f" ({os.path.getsize(p) / 1e6:.1f}MB)"
                rows.append(f"| {name} | {cells[1]} | {cells[2]} | {cells[4]} | `{stl}`{stl_mb} |")
    if not rows:
        return ["（读取零件表失败）"], 0, 0
    head = ["| 零件 | 三角面 | 实体体积 cm³ | 水密 | STL |", "|---|---|---|---|---|"]
    return head + rows, ok, total


def stl_sizes():
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "out", "stl", "*.stl"))):
        mb = os.path.getsize(p) / 1e6
        ts = dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M")
        out.append(f"`{os.path.basename(p)}` {mb:.1f}MB ({ts})")
    return out or ["（无 STL）"]


def renders():
    for d in ("out/ip12", "out"):
        files = sorted(glob.glob(os.path.join(ROOT, d, "*.png")))
        files = [f for f in files if "selftest" not in f and "bench" not in f and "smoke" not in f]
        if files:
            ts = max(os.path.getmtime(f) for f in files)
            return (d, len(files), dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"))
    return ("（无）", 0, "-")


def build_status():
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    clear = final_line(read("out/clearance_report.md"))
    pcb = final_line(read("out/pcb/PCB_FIT_REPORT.md"))
    table, ok, total = part_table()
    rdir, rnum, rtime = renders()
    lines = [BEGIN, f"### 当前状态（脚本自动生成：{now}，运行 `python status.py` 刷新）", ""]
    lines.append(f"- 机型：**iPhone 12 / 12 Pro**（146.7 × 71.5 × 7.4 mm），已出全套 STL 与渲染")
    if total:
        mark = "✔ 全部水密" if ok == total else f"✘ 有 {total - ok} 件不水密"
        lines.append(f"- 打印件：**{total} 件**，{mark}（边界边 0 / 非流形边 0 才算通过）")
    else:
        lines.append("- 打印件：（还没跑过 `python build.py`）")
    lines.append(f"- 装配干涉：{clear or '（未跑，运行 `python check_poses.py`）'}")
    lines.append(f"- 板框/开关架自检：{pcb or '（未跑，运行 `python pcb_outline.py`）'}")
    lines.append(f"- 渲染图：`{rdir}/` 共 {rnum} 张（{rtime}）")
    lines.append("")
    lines.append("**打印件明细（`out/stl/`）**：")
    lines.append("")
    lines.extend(table)
    lines.append("")
    lines.append("**文档**：`PROGRESS.md`（本文，进度+计划+决策）、`README.md`（结构/尺寸/物料）、"
                 "`DIY_GUIDE.md`（打印+装配+分期）、`ELECTRONICS.md`（采购+引脚表+固件）、"
                 "`out/BUILD_NOTES.md`、`out/clearance_report.md`、`out/pcb/PCB_FIT_REPORT.md`")
    lines.append("")
    lines.append("**一键复现**：`python build.py --render-dir ip12`（出 STL+渲染+报告，约 6 分钟）／"
                 "`python check_poses.py`（只跑干涉，约 4 分钟）／`python pcb_outline.py`（板框+自检）／"
                 "`python debug_slices.py`（剖面 ASCII 自检）／`python status.py`（刷新本文状态块）")
    lines.append("")
    lines.append(END)
    return "\n".join(lines)


def main():
    if not os.path.exists(PROGRESS):
        raise SystemExit(f"没找到 {PROGRESS}，先创建它（带 AUTO 标记）")
    txt = open(PROGRESS, encoding="utf-8").read()
    if BEGIN not in txt or END not in txt:
        raise SystemExit("PROGRESS.md 里缺少 AUTO:STATUS 标记")
    pre, rest = txt.split(BEGIN, 1)
    _, post = rest.split(END, 1)
    new = pre + build_status() + post
    open(PROGRESS, "w", encoding="utf-8", newline="\n").write(new)
    print(f"已刷新 {PROGRESS}")
    for line in build_status().splitlines():
        if line.startswith("- ") or line.startswith("**"):
            print("  " + line)


if __name__ == "__main__":
    main()
