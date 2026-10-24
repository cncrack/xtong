"""OpenAI 兼容 LLM 客户端（PLAN §4：GLM/DeepSeek/Qwen/本地随时切换，零改码）。

配置（config/settings.toml [llm]）：
    base_url    如 https://open.bigmodel.cn/api/paas/v4 或 https://api.deepseek.com/v1
    model       如 glm-4-flash / deepseek-chat
    api_key_env 环境变量名（Key 绝不落盘）
"""

from __future__ import annotations

import os
from typing import Any


class LLMError(RuntimeError):
    """LLM 调用失败（缺 Key / 网络 / 响应异常）。"""


class LLMClient:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_url: str = str(cfg["base_url"]).rstrip("/")
        self.model: str = str(cfg["model"])
        self.api_key_env: str = str(cfg.get("api_key_env", "XXT_LLM_API_KEY"))
        self.timeout: int = int(cfg.get("timeout", 60))

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise LLMError(
                f"未设置环境变量 {self.api_key_env}——请在系统环境变量中配置 LLM API Key 后重开终端"
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
