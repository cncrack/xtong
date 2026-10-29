"""配置加载：config/settings.toml，缺失项用内置默认值补齐。"""

from __future__ import annotations

import logging
import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

# 配置层自己的 logger：Settings 常在 setup_logging 之前构造，此时 warning 走 stderr（仍可见）
_log = logging.getLogger("xxt.config")

DEFAULTS: dict[str, Any] = {
    "app": {"data_dir": "data"},
    "platform": {
        "name": "chaoxing",
        "login_url": "https://passport2.chaoxing.com/login",
        "space_url": "https://i.chaoxing.com/",
    },
    "llm": {
        "provider": "agnes",
        "base_url": "https://api.agnes-ai.cn/v1",
        "model": "agnes-2.5-flash",
        "api_key_env": "XXT_LLM_API_KEY",
    },
    "learn": {
        "speed": 1.0,
        "daily_max_minutes": 240,
        # 节级重试（2026-10-27 配置化，见 docs/devlog/2026-10-27-11）
        "enter_retries": 2,
        "max_skip_chapters": 3,
        "non_video_streak": 4,
        "non_video_marks": "作业,测验,讨论,阅读,考试,练习,公告",
        "partial_retries": 1,
        "partial_retry_ratio_pct": 60,
        "login_budget_per_day": 2,
    },
    "pace": {
        "gap_browse": [3, 15],
        "gap_short": [30, 90],
        "gap_away": [180, 600],
        "gap_weights": [70, 20, 10],
        "focus_minutes": [25, 60],
        "rest_minutes": [5, 15],
        "daily_window": ["08:00", "23:00"],
        "speed_range": [1.0, 1.25],
    },
    "quiz": {"enabled": False, "mode": "semi", "work_enabled": False,
             "work_mode_default": "owner"},
    "quota": {
        "daily_minutes": 240,
        "daily_tasks": 60,
        # 作用范围：**只作用于“每日额度上限”的抽样**。每次学习会话启动时在这个区间
        # 抽一个系数 × 额度，作为当天预算（时长与任务数各抽一次）；抽到 0.85 就是
        # 204 分钟，少掉的名额当天作废、不累积到次日。不影响完成状态判定、作业、
        # 倍速、重试次数。账号 ✎ 里填了额度时，浮动作用在账号级额度上而非全局值。
        "float_ratio": [0.85, 1.0],
    },
    # 记账对齐（对账，2026-10-29 用户要求“刷之前先和平台对账”）。
    # 背景：平台是唯一事实来源，但旧写回只更名称序号、不回写完成状态，导致
    # “平台已完成、本地记待学”的缺口永不自愈（实测账号#2《知行合一》70/70 已完成、
    # 本地仍记 29 节待学），调度因而反复白跑真实页面。
    # 本段**只影响本地完成状态与平台标识的对齐**，不改变额度/倍速/重试/作业任何行为。
    "reconcile": {
        # 刷前对账开关。作用对象：**全部账号的每次学习会话**（xxt learn / mlearn /
        # 面板 ▶）。章节树那一页本来就要打开，顺手读平台标记，零额外页面访问。
        # 只会把“本地待学 → 已完成”单向升级，会话内绝不降级。
        "pre_session": True,
        # 降级开关（已完成 → 待学）。作用对象：**只有 `xxt chapters` 命令**。
        # 默认关：学习路径不用它（误判会立刻引发整门课重刷）；需要时显式开。
        "demote": False,
        # 护栏 1・覆盖度下限（%）。作用对象：demote 开时的 chapters 命令——
        # 本次抓到的节数 < 本地已记录节数 × 此比例 → 判定“树没抓全”，禁止降级。
        "demote_min_coverage_pct": 90,
        # 护栏 2・单次降级比例上限（%）。作用对象同上：一次要打的节数超过本次
        # 抓到的节数 × 此比例 → 判定“完成图标未渲染 / 页面改版”，只报警不降级。
        "demote_max_ratio_pct": 20,
    },
    "rotation": {
        "courses_per_day": [1, 3],
        "priorities": {},
    },
    # 守护重试（2026-10-27 配置化 + 随机退避；面板⏱守护与节级策略可改）
    "auto": {
        "max_tries": 2,
        "retry_minutes": 15,
        "confirm_seconds": 25,
        "retry_jitter_pct": 40,
        "retry_max_minutes": 90,
        "hard_tries_cap": 4,
        # Q44（2026-10-28 用户拍板：默认不要）：解除“今日收工”后是否立刻开跑。
        # 关（默认）= 仍按定时/学习窗口排队（防封优先）；开 = 下一轮巡检即发起。
        "reopen_run_now": False,
    },
    # 浏览器形态（Q36，2026-10-28 接线）：headless 由 core/browser.py 统一读取，不再是死配置。
    # 关掉后所有刷课/抓取/作业都会弹窗（调试用）；人工登录始终有头，不受此项影响。
    "stealth": {"headless": True},
}


def find_project_root() -> Path:
    """定位项目根：XXT_ROOT 环境变量 > exe 所在目录 > 向上查找 config/settings.toml 或 .git。"""
    import sys

    env = os.environ.get("XXT_ROOT")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):  # PyInstaller 打包后：exe 所在目录即根
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "settings.toml").exists() or (parent / ".git").exists():
            return parent
    # 源码布局 src/xxt/config.py → 项目根
    return here.parents[2]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


class Settings:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or find_project_root()).resolve()
        data = deepcopy(DEFAULTS)
        path = self.root / "config" / "settings.toml"
        if path.exists():
            with open(path, "rb") as f:
                _deep_merge(data, tomllib.load(f))
        # DB 全局覆盖层（app_config 表，面板"设置"写入，v0.6 三层配置的第 2 层）
        # 无库/无表时跳过——CLI 首次 init 前也能用。但**失败必须留痕**：
        # 静默 pass 曾让"面板改了额度、实际没生效"无任何线索（devlog 24 排查耗掉十分钟）。
        try:
            import json
            import sqlite3

            db_file = (self.root / data["app"]["data_dir"] / "xxt.db").resolve()
            if db_file.exists():
                _conn = sqlite3.connect(db_file)
                try:
                    for k, v in _conn.execute("SELECT key, value FROM app_config"):
                        try:
                            parsed = json.loads(v)
                        except Exception:
                            # 兼容裸字符串：面板早期/个别接口直接存原文（llm.base_url、llm.model、
                            # llm.key_enc）。此前这里 continue 会让这些覆盖项**永久不生效**，
                            # 表现为"面板切了 Provider，实际仍请求旧地址旧模型"（2026-10-25 定位）。
                            parsed = v
                            _log.debug("覆盖层键 %s 非 JSON，按裸字符串处理", k)
                        node: dict[str, Any] = data
                        parts = k.split(".")
                        for p in parts[:-1]:
                            node = node.setdefault(p, {})
                        node[parts[-1]] = parsed
                finally:
                    _conn.close()
        except Exception as exc:
            _log.warning("DB 覆盖层读取失败，本次回退到 config/settings.toml + 内置默认值：%r",
                         exc)
        self.raw = data
        self.data_dir = (self.root / data["app"]["data_dir"]).resolve()
        self.db_path = self.data_dir / "xxt.db"
        self.log_dir = self.data_dir / "logs"
        self.platform: dict[str, Any] = data["platform"]
        self.llm: dict[str, Any] = data["llm"]
        self.learn: dict[str, Any] = data["learn"]
        self.pace: dict[str, Any] = data["pace"]
        self.quiz: dict[str, Any] = data["quiz"]
        self.quota: dict[str, Any] = data["quota"]
        self.reconcile: dict[str, Any] = data["reconcile"]
        self.rotation: dict[str, Any] = data["rotation"]
        self.stealth: dict[str, Any] = data["stealth"]

    @property
    def default_state_path(self) -> Path:
        """默认账号（id=1）的浏览器会话文件（与 accounts/<id>/ 约定统一）。"""
        return self.data_dir / "accounts" / "1" / "state.json"
