#!/usr/bin/env python3
"""push 前门禁：敏感信息扫描 + tracked 文件防护检查 + 脱敏扫描 + 测试。

全部通过输出 PUSH CHECK: PASS（退出码 0）；任何硬失败则 FAIL（退出码 1）。

真值一律来自 data/（不入库）：账号邮箱在 `data/git-remote.md`，真实姓名/校名在
`data/redact_terms.local.txt`。脚本本身不含任何敏感值，别人 clone 时缺文件即自动跳过。
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


def redact_terms() -> list[str]:
    """本机脱敏词表：真实姓名/校名（`data/redact_terms.local.txt`，每行一条，`#` 注释）。

    2026-10-28 Q37：文档里曾散落真实姓名/校名。词表只在本机，脚本本身不含真值；
    文件不存在时返回空表（别人 clone 不受影响）。
    """
    f = ROOT / "data" / "redact_terms.local.txt"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return sorted(set(out))


def local_needles() -> list[str]:
    """从 data/git-remote.md（本机备忘，不入库）提取真实账号值作为精确拦截词。"""
    memo = ROOT / "data" / "git-remote.md"
    if not memo.exists():
        return []
    return sorted(set(RE_EMAIL.findall(memo.read_text(encoding="utf-8", errors="replace"))))


def scan_tracked_text(terms: list[str]) -> list[str]:
    """全部 tracked 文本文件全文扫脱敏词（不只是本次 diff）。

    历史文档里的姓名不会出现在 diff 里——只扫 diff 等于永远抓不到；这里扫的是
    **当前仓库内容**。二进制（图片/字体）解码失败即跳过。
    """
    if not terms:
        return []
    bad: list[str] = []
    for rel in sh("git", "ls-files").stdout.splitlines():
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for term in terms:
            n = text.count(term)
            if n:
                bad.append(f"{rel}: 含真实姓名/校名 ×{n}")
    return bad


def staged_diff() -> str:
    """已 `git add` 但尚未提交的改动。

    2026-10-27 修：原先只扫 `origin/main..HEAD`，于是“add 之后、commit 之前”
    跑门禁会显示“待审 diff 与上次完全相同”——看似通过，实际本次改动一行没审。
    而 push 前若工作区还有 staged 改动，它们同样会被推上去，必须一并扫。
    """
    return sh("git", "diff", "--cached").stdout


def outgoing_diff() -> str:
    """待推送内容：origin/main..HEAD（+ 已 staged 未提交的改动）。"""
    base = sh("git", "rev-parse", "--verify", "-q", "origin/main")
    head = sh("git", "rev-parse", "HEAD")
    parts: list[str] = []
    if head.returncode == 0:
        h = head.stdout.strip()
        if base.returncode == 0:
            b = base.stdout.strip()
            if b != h:  # 有待推送提交
                parts.append(sh("git", "diff", f"{b}..{h}").stdout)
        else:
            parts.append(sh("git", "show", h).stdout)  # 首次推送：至少审最后一个提交
    staged = staged_diff()
    if staged.strip():
        parts.append(staged)
    return "\n".join(p for p in parts if p)


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
                # 不打印原值：终端输出常被粘进对话/日志，打出来等于又泄露一次
                problems.append(f"命中本机敏感词（{len(needle)} 字，原值不显示）")
        if RE_WARN.search(text):
            warnings.append(f"含敏感词(需复核): {text.strip()[:80]}")
    return problems, warnings


def run_syntax_check() -> tuple[bool, str]:
    """全量语法检查（AST 解析，不产生 .pyc）：语法错误必须在 push 前暴露。

    教训（2026-10-25）：login.py 缩进错误因延迟导入 + 无测试覆盖，43 个测试全绿仍漏到分发 exe。
    """
    import ast

    bad: list[str] = []
    for pattern in ("src/**/*.py", "scripts/**/*.py"):
        for p in sorted(ROOT.glob(pattern)):
            try:
                ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            except SyntaxError as exc:
                bad.append(f"{p.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
    return not bad, "\n".join(bad)


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
    terms = redact_terms()
    p2, warnings = scan_diff(diff, local_needles() + terms)
    problems += p2

    # 3) tracked 全文脱敏扫描（历史文档里的姓名不会进 diff，必须单独扫）
    p3 = scan_tracked_text(terms)
    problems += p3

    # 4) 语法检查（AST，覆盖延迟导入模块）
    syn_ok, syn_tail = run_syntax_check()

    # 5) 测试
    ok, tail = run_pytest()
    print(f"[1/5] 防护检查: tracked 禁止路径 {'✅ 无' if not bad else '❌ ' + str(bad)}")
    print(f"[2/5] 敏感扫描: 待审 diff {len(diff)} 字符, 拦截 {len(p2)}, 告警 {len(warnings)}")
    for w in warnings[:5]:
        print(f"      ⚠️ {w}")
    print(f"[3/5] 脱敏扫描: tracked 文件 "
          + ("✅ 无真实姓名/校名" if not p3 else f"❌ {len(p3)} 处")
          + (f"（词表 {len(terms)} 条）" if terms else "（无本机词表，已跳过）"))
    print(f"[4/5] 语法检查: {'✅ 通过' if syn_ok else '❌ 失败'}")
    print(f"[5/5] pytest: {'✅ 通过' if ok else '❌ 失败'}")
    if not syn_ok:
        problems.append("语法检查未通过（延迟导入模块也在此暴露）")
        print("      " + syn_tail.replace("\n", "\n      "))
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
