"""账号表（高校考试网适配层）：多账号的第一块地基。

设计约束（PLAN-GXKS §7.3.1 / D12）：

- **一账号 = 一 Chrome = 一端口 = 一 user-data-dir**。账号表把这三样绑在一起，
  调度器只认端口，不去碰浏览器进程本身（起停浏览器仍由人/面板负责）。
- **代号制**：`label` 只允许 `[a-z0-9_-]`，写日志/文件名一律用它；真实姓名（`display`）
  只用于界面展示，**不得进文件名、不得进 git**。
- **向后兼容**：没有 `accounts.local.json` 时，回落到老的 `account.local.json`
  （`{"name","pw"}` 单账号），这样已经跑通的单账号流程不受影响。

凭据只从 `data/` 读（gitignore），本模块**不打印任何凭据值**。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from xxt.config import find_project_root

_ACCOUNTS_FILE = "accounts.local.json"
_LEGACY_FILE = "account.local.json"


def default_path() -> Path:
    """账号表默认位置：跟着**项目根**（`XXT_ROOT` / exe 目录 / 仓库根），不跟当前工作目录。

    相对路径 `data/...` 在换目录启动时会指向另一个文件，表现出来就是“账号表突然空了”（2026-10-30 修正）。
    """
    return find_project_root() / "data" / _ACCOUNTS_FILE


def default_legacy_path() -> Path:
    """旧单账号文件（回落用），同样跟项目根。"""
    return find_project_root() / "data" / _LEGACY_FILE


_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
_PLACEHOLDER_HINTS = ("在这里填", "填学号", "填密码", "TODO", "your_")

DEFAULT_SECTIONS_PER_DAY = 10        # 用户 2026-10-29 定的日上限（可配置）
DEFAULT_MINUTES_PER_DAY = 600        # 10 节 × 60 分钟上限
DEFAULT_PORT = 9222


@dataclass(frozen=True)
class Account:
    """一个学习账号。`courses` 为空 = 跑该账号能看到的**全部课程**。"""

    label: str
    name: str = ""
    pw: str = ""
    port: int = DEFAULT_PORT
    profile: str = ""
    display: str = ""
    courses: tuple[int, ...] = ()
    daily_sections: int = DEFAULT_SECTIONS_PER_DAY
    daily_minutes: int = DEFAULT_MINUTES_PER_DAY
    enabled: bool = True
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def configured(self) -> bool:
        """有账号密码才算“可自动重登”；否则掉线只能人工。"""
        return bool(self.name and self.pw)


def _bad_placeholder(value: str) -> bool:
    return any(h in value for h in _PLACEHOLDER_HINTS)


def parse_accounts(data: object) -> list[Account]:
    """从 JSON 对象解析账号列表；非法条目**跳过并计数**，不整表失败。

    跳过规则：没有 `label` / `label` 不合规 / 重复（按出现顺序保留第一个）/
    端口重复（同上）/ 占位符凭据。
    """
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        raw = data["accounts"]
    elif isinstance(data, list):
        raw = data
    elif isinstance(data, dict) and ("name" in data or "pw" in data):
        raw = [data]                      # 单账号写法，也接受
    else:
        return []

    out: list[Account] = []
    seen_labels: set[str] = set()
    seen_ports: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip().lower()
        if not _LABEL_RE.match(label) or label in seen_labels:
            continue
        name = str(item.get("name") or "").strip()
        pw = str(item.get("pw") or "").strip()
        if name and _bad_placeholder(name):
            name = ""
        if pw and _bad_placeholder(pw):
            pw = ""
        try:
            port = int(item.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        if port in seen_ports:
            continue
        courses: tuple[int, ...] = ()
        raw_courses = item.get("courses")
        if isinstance(raw_courses, list):
            nums = []
            for c in raw_courses:
                try:
                    nums.append(int(c))
                except (TypeError, ValueError):
                    continue
            courses = tuple(nums)
        try:
            secs = int(item.get("daily_sections", DEFAULT_SECTIONS_PER_DAY))
        except (TypeError, ValueError):
            secs = DEFAULT_SECTIONS_PER_DAY
        try:
            mins = int(item.get("daily_minutes", DEFAULT_MINUTES_PER_DAY))
        except (TypeError, ValueError):
            mins = DEFAULT_MINUTES_PER_DAY

        seen_labels.add(label)
        seen_ports.add(port)
        out.append(Account(
            label=label,
            name=name,
            pw=pw,
            port=port,
            profile=str(item.get("profile") or "").strip(),
            display=str(item.get("display") or "").strip(),
            courses=courses,
            daily_sections=secs if secs > 0 else DEFAULT_SECTIONS_PER_DAY,
            daily_minutes=mins if mins > 0 else DEFAULT_MINUTES_PER_DAY,
            enabled=bool(item.get("enabled", True)),
            extra={k: v for k, v in item.items() if k not in {
                "label", "name", "pw", "port", "profile", "display", "courses",
                "daily_sections", "daily_minutes", "enabled"}},
        ))
    return out


def load_accounts(path: str | Path | None = None,
                  legacy_path: str | Path | None = None) -> list[Account]:
    """读账号表。新表缺失/为空时回落到旧单账号文件（兼容已在跑的环境）。"""
    p = Path(path) if path is not None else default_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001  坏文件按“没有”处理
            data = None
        accounts = parse_accounts(data)
        if accounts:
            return accounts

    legacy = Path(legacy_path) if legacy_path is not None else default_legacy_path()
    if legacy.exists():
        try:
            old = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            old = None
        if isinstance(old, dict):
            name = str(old.get("name") or "").strip()
            pw = str(old.get("pw") or "").strip()
            if name and pw and not (_bad_placeholder(name) or _bad_placeholder(pw)):
                return [Account(label="default", name=name, pw=pw)]
    return []


def find_by_label(accounts: list[Account], label: str) -> Account | None:
    key = (label or "").strip().lower()
    for a in accounts:
        if a.label == key:
            return a
    return None


def is_valid_label(label: str) -> bool:
    """代号是否合法（= 能否安全用作目录名 / 配置键）。`browser.profile_dir()` 也认它。"""
    return bool(_LABEL_RE.match(str(label or "")))


def resolve_port(label: str, *, accounts: list[Account] | None = None,
                 fallback: int = DEFAULT_PORT) -> int:
    """账号对应的 CDP 端口。

    端口是**账号属性**（一账号一 Chrome 一端口），不是全局配置：脚本和面板都从
    这里取，就不会出现“面板以为在 9300、账号表写着 9222”那种两边各猜一个端口、
    点了没反应的故障。`fallback` 只在账号表里找不到该代号时用。
    """
    accs = load_accounts() if accounts is None else accounts
    acc = find_by_label(accs, label)
    return acc.port if acc else fallback


def summary(accounts: list[Account]) -> str:
    """一行摘要（只含代号与端口，**不含凭据**）。"""
    if not accounts:
        return "（未配置账号）"
    parts = []
    for a in accounts:
        flags = []
        if not a.enabled:
            flags.append("停用")
        if not a.configured:
            flags.append("无凭据(掉线即停)")
        if a.courses:
            flags.append("课程" + "/".join(str(c) for c in a.courses))
        tail = ("；" + "、".join(flags)) if flags else ""
        parts.append(f"{a.label}@:{a.port}{tail}")
    return " | ".join(parts)
