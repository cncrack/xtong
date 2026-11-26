"""Gxks-only LLM client.

Reuses the shared ``ai/llm.py`` for the HTTP call/response logic, but overrides
WHERE the API key comes from. The shared module stays byte-identical to its
baseline, so merging this branch back never conflicts there.

Why a subclass instead of changing the shared class:
  - ``LLMClient._api_key()`` reads a legacy database path that is not ours.
  - gxks stores its key in ``data/gxks_questions.db`` (``meta.ai.key.<provider>``).
  - Adding an ``api_key`` parameter to the shared class touches a file we do
    not own -> merge conflicts and cross-platform coupling.

Here we inherit ``chat()`` unchanged and only replace ``_api_key()``.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from ...ai.llm import LLMClient, LLMError
from ...store import gxks_ai_config


class GxksLLMClient(LLMClient):
    """LLM client that reads its Key from the gxks question bank only."""

    def __init__(self, cfg: dict[str, Any], *, db_path: Path,
                 data_dir: Path, provider: str) -> None:
        # Deliberately do NOT pass data_dir to the parent: that would let the
        # parent fall back to the legacy DB if our override ever changed.
        super().__init__(cfg)
        self.gxks_db_path = Path(db_path)
        self.gxks_data_dir = Path(data_dir)
        self.gxks_provider = str(provider or "").strip()

    def _api_key(self) -> str:
        if not self.gxks_provider:
            raise LLMError(
                "gxks 未配置 AI 厂商名——请打开面板 → AI 模型，选中一个厂商并保存 Key")
        key = gxks_ai_config.load_key_for(
            self.gxks_db_path, data_dir=self.gxks_data_dir,
            provider=self.gxks_provider)
        if not key:
            raise LLMError(
                "未配置 LLM Key——请在 gxks 面板 → AI 模型 粘贴 Key 保存"
                "（加密存本机 data/gxks_questions.db）")
        return key


def make_gxks_llm(settings: Any) -> GxksLLMClient:
    """Build the gxks client from settings; Key comes from the gxks database."""
    provider = str(getattr(settings, "llm", {}).get("name") or "") or "agnies"
    return GxksLLMClient(
        settings.llm,
        db_path=Path(settings.data_dir) / "gxks_questions.db",
        data_dir=Path(settings.data_dir),
        provider=provider,
    )
