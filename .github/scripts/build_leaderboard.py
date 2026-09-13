#!/usr/bin/env python3
"""从 GitHub Issues 汇总各游戏 Top10 排行榜。

- 读取仓库里所有 issue（不限状态），识别标题/正文里的成绩标记
- 每个玩家（issue 作者）每款游戏只保留最好成绩
- 写出：LEADERBOARD.md、docs/leaderboard.json，并把 Top10 注入 README.md 的标记区

由 .github/workflows/leaderboard.yml 调用（也支持本地手动跑：需要 GITHUB_TOKEN）。
自测：python .github/scripts/build_leaderboard.py --selftest
"""

from __future__ import annotations
import datetime as dt
import json
import os
import re
import sys
import urllib.request

API = "https://api.github.com"
GAMES = [("run", "跑酷"), ("tetris", "俄罗斯方块"), ("break", "打砖块")]
ALIASES = {
    "run": ["run", "跑酷", "platformer", "平台"],
    "tetris": ["tetris", "俄罗斯方块", "方块"],
    "break": ["break", "breakout", "打砖块", "砖块"],
}
MAX_SCORE = 100_000_000          # 明显不可能的成绩直接丢掉
TITLE_RE = re.compile(r"\[\s*SCORE\s*\]", re.I)
TOP_N = 10
BEGIN = "<!-- LEADERBOARD:BEGIN -->"
END = "<!-- LEADERBOARD:END -->"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def api(path: str, token: str):
    req = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "flipdeck-leaderboard",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_issues(repo: str, token: str) -> list:
    out, page = [], 1
    while True:
        batch = api(f"/repos/{repo}/issues?state=all&per_page=100&page={page}", token)
        if not isinstance(batch, list):
            break
        out += [i for i in batch if "pull_request" not in i]
        if len(batch) < 100:
            break
        page += 1
    return out


def parse_issue(iss: dict):
    """从一条 issue 里解析出 (game, score)，解析不出返回 None。"""
    title = iss.get("title") or ""
    body = iss.get("body") or ""
    text = (title + "\n" + body).lower()
    looks_like_score = bool(TITLE_RE.search(title)) or bool(re.search(r"(分数|score)\s*[:：]", body, re.I))
    if not looks_like_score:
        return None
    game = None
    for key, names in ALIASES.items():
        if any(n.lower() in text for n in names):
            game = key
            break
    if game is None:
        return None
    m = (re.search(r"(?:分数|score)\s*[:：]\s*[*\s]*(\d[\d,]*)", body, re.I)
         or re.search(r"(?:分数|score)\s*[:：]\s*[*\s]*(\d[\d,]*)", title, re.I))
    if m:
        score = int(m.group(1).replace(",", ""))
    else:                                   # 退路：正文里最大的数字
        nums = [int(x.replace(",", "")) for x in re.findall(r"\b\d[\d,]{2,}\b", body)]
        if not nums:
            return None
        score = max(nums)
    if not (0 < score <= MAX_SCORE):
        return None
    user = (iss.get("user") or {}).get("login") or "?"
    return {
        "game": game, "score": score, "user": user,
        "url": iss.get("html_url") or "",
        "created": (iss.get("created_at") or "")[:10],
        "number": iss.get("number"),
    }


def rank(entries: list) -> dict:
    """每玩家每游戏只留最高分，取 Top10。"""
    best = {}
    for e in entries:
        k = (e["game"], e["user"])
        if k not in best or e["score"] > best[k]["score"]:
            best[k] = e
    out = {g: [] for g, _ in GAMES}
    for e in sorted(best.values(), key=lambda x: -x["score"]):
        out.setdefault(e["game"], []).append(e)
    return {g: v[:TOP_N] for g, v in out.items()}


def render_md(table: dict, updated: str) -> str:
    L = ["> 成绩通过 **GitHub Issue** 提交，由 **GitHub Actions 自动汇总** —— 有人提交就会自动更新这份榜单和 README。",
         "> 提交方式：在 [演示页](https://li-mingshuang.github.io/flipdeck/) 玩完点「提交成绩」，或者手动开一个标题带 `[SCORE]` 的 issue。",
         "> 同一玩家每款游戏只记最好成绩；分数靠自觉，这是个友好排行榜。", ""]
    for g, name in GAMES:
        rows = table.get(g) or []
        L += [f"## 🎮 {name}（`{g}`）", ""]
        if not rows:
            L += ["_还没有成绩 —— 来占个第一？_", ""]
            continue
        L += ["| # | 玩家 | 分数 | 提交 |", "|---|---|---|---|"]
        for i, r in enumerate(rows, 1):
            medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else str(i)
            L.append(f"| {medal} | [@{r['user']}]({r['url']}) | **{r['score']}** | {r['created']} |")
        L.append("")
    L += [f"_最后更新：{updated}（UTC）_", ""]
    return "\n".join(L)


def inject_readme(top_md: str) -> None:
    path = os.path.join(ROOT, "README.md")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        txt = fh.read()
    if BEGIN not in txt or END not in txt:
        print("！README.md 缺少 LEADERBOARD 标记，跳过注入")
        return
    head, rest = txt.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + BEGIN + "\n" + top_md + "\n" + END + tail)


def main():
    selftest = "--selftest" in sys.argv
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    if selftest:
        issues = [
            {"title": "[SCORE] tetris 12000", "body": "### 游戏\n\ntetris\n\n### 分数\n\n12000",
             "user": {"login": "alice"}, "html_url": "u1", "created_at": "2026-09-01T00:00:00Z", "number": 1},
            {"title": "[SCORE] tetris 180", "body": "分数：180", "user": {"login": "alice"},
             "html_url": "u2", "created_at": "2026-09-02T00:00:00Z", "number": 2},
            {"title": "[SCORE] 俄罗斯方块 9400", "body": "### 分数\n\n9400", "user": {"login": "bob"},
             "html_url": "u3", "created_at": "2026-09-03T00:00:00Z", "number": 3},
            {"title": "[SCORE] breakout 3500", "body": "score: 3500", "user": {"login": "carol"},
             "html_url": "u4", "created_at": "2026-09-04T00:00:00Z", "number": 4},
            {"title": "随便聊聊", "body": "分数：99999999", "user": {"login": "spam"},
             "html_url": "u5", "created_at": "2026-09-05T00:00:00Z", "number": 5},
            {"title": "[SCORE] run 777", "body": "### 游戏\n\nrun\n\n### 分数\n\n777", "user": {"login": "dan"},
             "html_url": "u6", "created_at": "2026-09-06T00:00:00Z", "number": 6},
        ]
    else:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN", "")
        if not repo or not token:
            print("需要 GITHUB_REPOSITORY 和 GITHUB_TOKEN（本地跑可以 gh auth token）")
            return 1
        try:
            issues = fetch_issues(repo, token)
        except Exception as exc:
            print(f"读取 issues 失败：{exc}")
            return 1
        print(f"仓库 {repo}：共 {len(issues)} 条 issue")
    parsed = [p for p in (parse_issue(i) for i in issues) if p]
    table = rank(parsed)
    print(f"识别到成绩 {len(parsed)} 条 -> " +
          "，".join(f"{g}:{len(table.get(g, []))}" for g, _ in GAMES))
    md = render_md(table, now)
    with open(os.path.join(ROOT, "LEADERBOARD.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# 🏆 flipdeck 排行榜（各游戏 Top 10）\n\n" + md)
    payload = {"updated": now, "games": table}
    for sub in ("docs", "web"):                      # docs/ 给 Pages，web/ 给本地预览
        os.makedirs(os.path.join(ROOT, sub), exist_ok=True)
        with open(os.path.join(ROOT, sub, "leaderboard.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    top_md = "\n".join(md.split("\n")[md.split("\n").index(""):]).strip()
    inject_readme(top_md)
    print("已写 LEADERBOARD.md / docs/leaderboard.json / README.md 标记区")
    if selftest:
        assert len(table["tetris"]) == 2 and table["tetris"][0]["user"] == "alice"
        assert table["tetris"][0]["score"] == 12000
        assert len(table["break"]) == 1 and table["break"][0]["score"] == 3500
        assert len(table["run"]) == 1
        assert all(r["user"] != "spam" for g in table for r in table[g]), "无标记的 issue 不该计入"
        print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
