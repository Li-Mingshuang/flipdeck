"""git 传输不通时，用 GitHub API（gh）把本地 HEAD 的改动推成一个 commit。

流程：blobs -> tree(base_tree=远端 main 的 tree) -> commit(parent=远端 main) -> 更新 ref。
用法： python .github/scripts/api_push.py
"""
from __future__ import annotations
import base64
import json
import os
import subprocess
import sys
import tempfile

REPO = "Li-Mingshuang/flipdeck"
BRANCH = "main"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", **kw)
    if r.returncode != 0:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\n{r.stderr[:500]}")
    return r.stdout


def gh(path, method="GET", body=None, jq=None):
    cmd = ["gh", "api", "-X", method, path]
    tmp = None
    if body is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        cmd += ["--input", tmp]
    if jq:
        cmd += ["--jq", jq]
    try:
        return run(cmd).strip()
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def main():
    os.chdir(ROOT)
    local = run(["git", "rev-parse", "HEAD"]).strip()
    parent_local = run(["git", "rev-parse", "HEAD~1"]).strip()
    changed = [f for f in run(["git", "diff", "--name-only", parent_local, local]).splitlines() if f.strip()]
    if not changed:
        print("没有需要推送的改动")
        return 0
    remote = gh(f"repos/{REPO}/git/ref/heads/{BRANCH}", jq=".object.sha")
    base_tree = gh(f"repos/{REPO}/git/commits/{remote}", jq=".tree.sha")
    msg = run(["git", "log", "-1", "--pretty=%B"]).strip()
    print(f"远端 main={remote[:8]}  本地 HEAD={local[:8]}  待推送 {len(changed)} 个文件")

    tree = []
    for path in changed:
        with open(os.path.join(ROOT, path), "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        sha = gh(f"repos/{REPO}/git/blobs", "POST", {"content": b64, "encoding": "base64"}, jq=".sha")
        tree.append({"path": path.replace("\\", "/"), "mode": "100644", "type": "blob", "sha": sha})
        print(f"  blob {path} -> {sha[:8]}")
    new_tree = gh(f"repos/{REPO}/git/trees", "POST", {"base_tree": base_tree, "tree": tree}, jq=".sha")
    new_commit = gh(f"repos/{REPO}/git/commits", "POST",
                    {"message": msg, "tree": new_tree, "parents": [remote]}, jq=".sha")
    gh(f"repos/{REPO}/git/refs/heads/{BRANCH}", "PATCH", {"sha": new_commit, "force": False})
    print(f"已推送：{new_commit[:8]}（API 方式，等 git 网络恢复后本地 reset --hard origin/{BRANCH} 即可对齐）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
