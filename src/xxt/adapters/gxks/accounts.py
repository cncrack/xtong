"""账号表（高校考试网适配层）：多账号的第一块地基。

设计约束（PLAN-GXKS §7.3.1 / D12）：

- **一账号 = 一 Chrome = 一端口 = 一 user-data-dir**。账号表把这三样绑在一起，
  调度器只认端口，不去碰浏览器进程本身（起停浏览器仍由人/面板负责）。
- **代号制**：`label` 只允许 `[a-z0-9_-]`，写日志/文件名一律用它；真实姓名（`display`）
  只用于界面展示，**不得进文件名、不得进 git**。
- **向后兼容**：没有 `accounts.local.json` 时，回落到老的 `account.local.json`
  （`{"name","pw"}` 单账号），这样已经跑通的单账号流程不受影响。

凭据只从 `data/` 读（gitignore），本模块**不打印任何凭据值**。

## 面板里的增删改（2026-10-30）

写入口是 `save_account()` / `delete_account()`，三条硬约束：

1. **原子写**：先写临时文件再 `os.replace`，中途崩了不会留下半张表（半张表 = 账号凭空消失）；
2. **写前校验**：代号合法、端口不重复、数值在合理区间——**不静默钳制**，错了就报错让用户改；
3. **不覆盖别人的改动**：每次写之前重读一遍文件，只改动目标那一条，其它账号原样保留
   （面板与命令行可能同时在编辑）。
"""

from __future__ import annotations

import json
import os
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
            # `0` = 不限（PLAN-GXKS §11.6 已定口径），所以只有**负数/非法**才回落到默认值
            daily_sections=secs if secs >= 0 else DEFAULT_SECTIONS_PER_DAY,
            daily_minutes=mins if mins >= 0 else DEFAULT_MINUTES_PER_DAY,
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


# ------------------------------------------------------------------ 写：增删改

#: 面板/脚本都会用的硬边界（越界**报错**，不静默钳制——静默钳制会让人以为改成功了）。
#: `daily_sections` / `daily_minutes` 的 `0` = 不限（PLAN-GXKS §11.6 已定口径），所以下界是 0。
LIMITS = {
    "daily_sections": (0, 40),
    "daily_minutes": (0, 1440),
    "port": (1024, 65535),
}

#: 报错里给人看的中文名（面板直接把 `AccountError` 原文显示给用户）
_CN = {"daily_sections": "日节数上限", "daily_minutes": "日时长上限（分钟）",
       "port": "调试端口"}


class AccountError(ValueError):
    """账号表写入被拒（带人可读原因，面板直接显示给用户）。"""


def _read_doc(p: Path) -> tuple[dict, list[dict]]:
    """读文件里的**原始文档**与账号列表。

    保留用户手写的顶层键（比如备注）：只替换 `accounts`，别把整份文件重写成
    `{"accounts": ...}` 而顺手丢掉别的东西。
    """
    if not p.exists():
        return {}, []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                      # noqa: BLE001  坏文件当空表，写前会覆盖
        return {}, []
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        raw, doc = data["accounts"], dict(data)
    elif isinstance(data, list):
        raw, doc = data, {}
    elif isinstance(data, dict) and ("name" in data or "pw" in data):
        raw, doc = [data], {}
    else:
        return {}, []
    return doc, [item for item in raw if isinstance(item, dict)]


def _read_raw(p: Path) -> list[dict]:
    """读文件里的原始账号列表（不做合法性过滤，保留用户写的其它字段）。"""
    return _read_doc(p)[1]


def _legacy_as_row(legacy: Path) -> dict | None:
    """把老的 `account.local.json`（单账号）转成一行账号表记录。

    **为什么必须有这一步**（2026-10-30 实测踩到）：`data/accounts.local.json` 不存在时，
    `load_accounts()` 会回落到 legacy 文件，面板上能看到那个账号。此时在面板上「新增账号」，
    如果直接以空表为底写新文件，**legacy 里那个真实账号就从面板上消失了**
    （文件一建出来，回落分支就不再走）——用户会以为"账号被删了"。
    """
    if not legacy.exists():
        return None
    try:
        old = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        return None
    if not isinstance(old, dict):
        return None
    name = str(old.get("name") or "").strip()
    pw = str(old.get("pw") or "").strip()
    if not name and not pw:
        return None
    if _bad_placeholder(name) or _bad_placeholder(pw):
        return None
    return {"label": "default", "port": DEFAULT_PORT,
            "name": name, "pw": pw, "display": ""}


def _read_raw_with_legacy(p: Path) -> list[dict]:
    """读账号表；**新表不存在时**把 legacy 账号折成一行带进来（防止"新增即丢号"）。"""
    rows = _read_raw(p)
    if rows or p.exists():
        return rows
    row = _legacy_as_row(default_legacy_path())
    return [row] if row else []


def _write_raw(p: Path, rows: list[dict]) -> None:
    """原子写：临时文件 + `os.replace`。半张表会让账号“凭空消失”，不能直接覆盖。"""
    doc, _ = _read_doc(p)
    doc["accounts"] = rows
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, p)


def _check_range(name: str, value: int) -> int:
    lo, hi = LIMITS[name]
    cn = _CN.get(name, name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AccountError(f"{cn}必须是整数")
    if not (lo <= value <= hi):
        raise AccountError(f"{cn}必须在 {lo}~{hi} 之间（收到 {value}）")
    return value


def save_account(
    fields: dict,
    *,
    path: str | Path | None = None,
    original_label: str = "",
) -> Account:
    """新增或修改一个账号，返回写入后的 `Account`。

    - `original_label` 为空 = 新增；给了 = 修改那一条（代号可以改，等于重命名）。
    - **只改目标那一条**，其它账号原样保留（面板与命令行可能同时在编辑）。
    - 端口重复一律拒：一账号一端口是硬约束，撞了会表现为“两个账号共用一个 Chrome”。
    """
    p = Path(path) if path is not None else default_path()
    # 用 `_read_raw_with_legacy`：新表还不存在时，legacy 里那个账号必须先带进来，
    # 否则"新增一个账号"会把原来的账号挤出面板（2026-10-30 实测踩到）
    rows = _read_raw_with_legacy(p)

    label = str(fields.get("label") or "").strip().lower()
    if not is_valid_label(label):
        raise AccountError("代号只能用 a-z 0-9 _ -，且以字母或数字开头、不超过 24 个字符")

    key = str(original_label or "").strip().lower()
    idx = next((i for i, r in enumerate(rows)
                if str(r.get("label") or "").strip().lower() == key), -1)
    if key and idx < 0:
        raise AccountError(f"账号表里没有 {key}（可能被别的窗口删掉了，刷新面板再看）")
    if idx < 0 and any(str(r.get("label") or "").strip().lower() == label for r in rows):
        raise AccountError(f"代号 {label} 已经存在")

    # 没给就用**该账号原来的值**（不是全局默认值）：否则只改端口的一次编辑
    # 会把用户设的额度悄悄重置回 10/600
    old = rows[idx] if idx >= 0 else {}

    port = fields.get("port", old.get("port", DEFAULT_PORT))
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise AccountError("端口必须是数字") from None
    _check_range("port", port)
    for i, r in enumerate(rows):
        if i == idx:
            continue
        try:
            other = int(r.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            other = DEFAULT_PORT
        if other == port:
            raise AccountError(
                f"端口 {port} 已被 {r.get('label')} 占用——一账号一端口，"
                "请换一个（如 9223 / 9224）")

    secs = fields.get("daily_sections", old.get("daily_sections", DEFAULT_SECTIONS_PER_DAY))
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        raise AccountError("日节数上限必须是数字") from None
    _check_range("daily_sections", secs)

    mins = fields.get("daily_minutes", old.get("daily_minutes", DEFAULT_MINUTES_PER_DAY))
    try:
        mins = int(mins)
    except (TypeError, ValueError):
        raise AccountError("日时长上限必须是数字") from None
    _check_range("daily_minutes", mins)

    courses: list[int] = []
    raw_courses = fields.get("courses")
    if isinstance(raw_courses, str):
        raw_courses = [c for c in re.split(r"[,\s]+", raw_courses) if c]
    if isinstance(raw_courses, list):
        for c in raw_courses:
            try:
                courses.append(int(c))
            except (TypeError, ValueError):
                raise AccountError(f"课程 value 必须是数字（收到 {c!r}）") from None

    row: dict = {"label": label}
    if idx >= 0:
        row.update(rows[idx])                     # 先继承旧字段（含用户手写的额外字段）
    row.update({
        "label": label,
        "port": port,
        "daily_sections": secs,
        "daily_minutes": mins,
    })
    # `enabled` 只有**显式给了**才改：编辑表单没带这个字段时，
    # 不能把用户停用的账号悄悄重新启用（那会让停用形同虚设）
    if "enabled" in fields:
        row["enabled"] = bool(fields.get("enabled"))
    else:
        row.setdefault("enabled", True)
    # 同样：没给 `display` 就保留原值，别把用户填的显示名洗成空
    if "display" in fields:
        row["display"] = str(fields.get("display") or "").strip()   # 显式空 = 清空
    else:
        row.setdefault("display", "")
    profile = str(fields.get("profile") or "").strip()
    if profile:
        row["profile"] = profile
    elif "profile" in row and fields.get("clear_profile"):
        row.pop("profile", None)
    # 课程同理：只有显式给了 `courses` 才动。否则一次"只改额度"的编辑
    # 会把用户配的 `courses: [32]` 悄悄清成"跑全部课程"（多跑课 = 多耗额度）
    if "courses" in fields:
        if courses:
            row["courses"] = courses
        else:
            row.pop("courses", None)              # 显式空 = 跑该账号能看到的全部课程

    # 凭据：**只认显式给了值才改**。空字符串 = “不要动”（面板不回显密码，回显即泄露）
    for cred in ("name", "pw"):
        val = str(fields.get(cred) or "")
        if val:
            row[cred] = val
    if fields.get("clear_credentials"):
        row.pop("name", None)
        row.pop("pw", None)

    if idx >= 0:
        rows[idx] = row
    else:
        rows.append(row)
    _write_raw(p, rows)

    saved = find_by_label(parse_accounts({"accounts": rows}), label)
    if saved is None:                             # 写完还解析不出来 = 写入有 bug，别假装成功
        raise AccountError("写入后账号表解析不出这条记录，请检查文件内容")
    return saved


def delete_account(label: str, *, path: str | Path | None = None) -> bool:
    """删一个账号。返回是否真的删掉了。**不删 profile 目录**（登录态要留着，人可能后悔）。"""
    p = Path(path) if path is not None else default_path()
    # 带 legacy：否则删 legacy 里那个账号会“删不动”（文件里根本没有它）
    rows = _read_raw_with_legacy(p)
    key = str(label or "").strip().lower()
    keep = [r for r in rows if str(r.get("label") or "").strip().lower() != key]
    if len(keep) == len(rows):
        return False
    _write_raw(p, keep)
    return True


def set_enabled(label: str, enabled: bool, *,
                path: str | Path | None = None) -> bool:
    """启用/停用一个账号（只改 `enabled`，不碰其它字段）。"""
    p = Path(path) if path is not None else default_path()
    rows = _read_raw_with_legacy(p)
    key = str(label or "").strip().lower()
    hit = False
    for r in rows:
        if str(r.get("label") or "").strip().lower() == key:
            r["enabled"] = bool(enabled)
            hit = True
    if not hit:
        return False
    _write_raw(p, rows)
    return True


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
