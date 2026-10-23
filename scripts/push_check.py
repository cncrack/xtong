#!/usr/bin/env python3
"""push 前门禁：敏感信息扫描 + tracked 文件防护检查 + 测试。

全部通过输出 PUSH CHECK: PASS（退出码 0）；任何硬失败则 FAIL（退出码 1）。
用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/push_check.py   # Windows
    .venv/bin/python scripts/push_check.py           # Linux / macOS / Docker
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- 硬失败：命中即拦截 ---
# 赋值形态的敏感值，如 password = "xxx"、api_key: "xxx"
RE_SECRET_VALUE = re.compile(
    r"(?:password|passwd|pwd|api_key|apikey|secret|token|access_key)"
    r"\s*[=:]\s*[\"'][^\"']{4,}[\"']",
    re.IGNORECASE,
)
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# --- 仅告警：提示复核，不拦截（代码里合法出现的可能性高） ---
RE_WARN = re.compile(r"cookie|storage_state|authorization|bearer", re.IGNORECASE)


def forbidden_tracked_path(p: str) -> bool:
    """tracked 文件里绝不允许出现的路径。"""
    name = Path(p).name.lower()
    return (
        p.startswith("data/")
        or name.endswith((".db", ".db-wal", ".db-shm"))
        or name == ".env"
        or name.startswith(".env.")
        or re.match(r"storage_state[^/]*\.json$", name) is not None
    )


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def extra_needles() -> list[str]:
    """从 data/git-remote.md（本机备忘，不入库）提取真实账号值作为精确拦截词。"""
    memo = ROOT / "data" / "git-remote.md"
    if not memo.exists():
        return []
    return sorted(set(RE_EMAIL.findall(memo.read_text(encoding="utf-8", errors="replace"))))


def outgoing_diff() -> str:
    """待推送内容：origin/main..HEAD；无远端/无新提交时退化为 HEAD 或空。"""
    base = sh("git", "rev-parse", "--verify", "-q", "origin/main")
    head = sh("git", "rev-parse", "HEAD")
    if head.returncode != 0:
        return ""
    h = head.stdout.strip()
    if base.returncode == 0:
        b = base.stdout.strip()
        if b == h:
            return ""  # 无待推送提交
        return sh("git", "diff", f"{b}..{h}").stdout
    return sh("git", "show", h).stdout  # 首次推送场景：至少审最后一个提交


def scan_diff(diff: str, needles: list[str]) -> tuple[list[str], list[str]]:
    problems, warnings = [], []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:]
        if RE_SECRET_VALUE.search(text):
            problems.append(f"疑似敏感赋值: {text.strip()[:80]}")
        for hit in RE_EMAIL.findall(text):
            problems.append(f"疑似邮箱: {hit}")
        if RE_PHONE.search(text):
            problems.append(f"疑似手机号: {RE_PHONE.search(text).group()}")
        for needle in needles:
            if needle and needle in text:
                problems.append(f"命中本机备忘中的账号值: {needle}")
        if RE_WARN.search(text):
            warnings.append(f"含敏感词(需复核): {text.strip()[:80]}")
    return problems, warnings


def run_pytest() -> tuple[bool, str]:
    venv_py = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    py = str(venv_py) if venv_py.exists() else sys.executable
    r = subprocess.run(
        [py, "-m", "pytest", "-q"], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
    return r.returncode == 0, "\n".join(tail)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    problems: list[str] = []

    # 1) tracked 文件防护
    r = sh("git", "ls-files")
    bad = [p for p in r.stdout.splitlines() if forbidden_tracked_path(p)]
    if bad:
        problems.append(f"tracked 文件含禁止路径: {bad}")

    # 2) 待推送 diff 敏感扫描
    diff = outgoing_diff()
    p2, warnings = scan_diff(diff, extra_needles())
    problems += p2
    print(f"[1/3] 敏感扫描: 待审 diff {len(diff)} 字符, 拦截 {len(p2)}, 告警 {len(warnings)}")
    for w in warnings[:5]:
        print(f"      ⚠️ {w}")

    # 3) 测试
    ok, tail = run_pytest()
    print(f"[2/3] 防护检查: tracked 禁止路径 {'✅ 无' if not bad else '❌ ' + str(bad)}")
    print(f"[3/3] pytest: {'✅ 通过' if ok else '❌ 失败'}")
    if not ok:
        problems.append("pytest 未通过（含环境缺依赖）")
        print("      " + tail.replace("\n", "\n      "))

    if problems:
        print("\nPUSH CHECK: FAIL")
        for p in problems[:20]:
            print(f"  ❌ {p}")
        return 1
    print("\nPUSH CHECK: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
