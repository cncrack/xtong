"""OpenAI 兼容 LLM 客户端（PLAN §4：GLM/DeepSeek/Qwen/本地随时切换，零改码）。

Key 来源（唯一正道）：加密落库（app_config: llm.key_enc，AES-GCM，主密钥同账号体系）。
面板粘贴 Key 保存即写入。不读环境变量——分发版数据封闭于自身 data 目录（安全设计）。
"""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from typing import Any


class LLMError(RuntimeError):
    """LLM 调用失败（缺 Key / 网络 / 响应异常）。"""


def _load_key_from_db(data_dir: Path) -> str | None:
    """从 data/xxt.db 解密 llm.key_enc；无则 None。"""
    try:
        from ..core.crypto import decrypt, master_key

        db = data_dir / "xxt.db"
        if not db.exists():
            return None
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key='llm.key_enc'").fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return decrypt(master_key(data_dir), base64.b64decode(row[0]))
    except Exception:
        return None


class LLMClient:
    def __init__(self, cfg: dict[str, Any], data_dir: Path | None = None) -> None:
        self.base_url: str = str(cfg["base_url"]).rstrip("/")
        self.model: str = str(cfg["model"])
        self.timeout: int = int(cfg.get("timeout", 60))
        self.data_dir = data_dir

    def _api_key(self) -> str:
        key = _load_key_from_db(self.data_dir) if self.data_dir else None
        if not key:
            raise LLMError(
                "未配置 LLM Key——请打开管理面板 → ⚙设置 → AI模型 → 粘贴 Key 保存（加密存本机 data/）"
            )
        return key

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        """对话补全，返回首条回复文本。任何失败统一抛 LLMError。"""
        import httpx

        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key()}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"LLM 调用失败：{exc}") from exc
