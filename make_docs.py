"""把网页预览打包成 GitHub Pages 用的 docs/ 目录。

docs/index.html  <- web/flipdeck_viewer.html（内容不变，`stl/` 相对路径自然成立）
docs/stl/*.stl   <- web/stl/*.stl
docs/.nojekyll   <- 让 GitHub Pages 不做 Jekyll 处理

用法： python make_docs.py   （改了 viewer 或重新导出 web STL 之后跑一次）
"""

from __future__ import annotations
import glob
import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")


def main():
    os.makedirs(os.path.join(DOCS, "stl"), exist_ok=True)
    src = os.path.join(ROOT, "web", "flipdeck_viewer.html")
    dst = os.path.join(DOCS, "index.html")
    shutil.copyfile(src, dst)
    n = 0
    size = 0
    for p in glob.glob(os.path.join(ROOT, "web", "stl", "*.stl")):
        shutil.copyfile(p, os.path.join(DOCS, "stl", os.path.basename(p)))
        n += 1
        size += os.path.getsize(p)
    pj = os.path.join(ROOT, "web", "params.json")
    if os.path.exists(pj):
        shutil.copyfile(pj, os.path.join(DOCS, "params.json"))
    else:
        print("！缺少 web/params.json，先跑 python web_export.py")
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    print(f"docs/index.html  {os.path.getsize(dst) / 1000:.1f} KB")
    print(f"docs/stl/        {n} 个文件，{size / 1e6:.2f} MB（+ params.json）")
    print("好了：GitHub Pages 源设为 main 分支 /docs 即可。")


if __name__ == "__main__":
    main()
