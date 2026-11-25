"""G-5 fix: gxks_work reads Key from gxks_questions.db (2026-11-25)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xxt.ai import llm
from xxt.store import gxks_ai_config
from xxt.store.schema_gxks import open_question_bank


class TestLlmKeySource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db = open_question_bank(self.data / "q.db")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def _cfg(self):
        return {"base_url": "https://api.agnies-ai.cn/v1", "model": "gpt-4o-mini"}

    def test_client_accepts_external_key(self):
        fake = "test-" + "key-for-unit-test"
        client = llm.LLMClient(self._cfg(), self.data, api_key=fake)
        self.assertEqual(client._api_key(), fake)

    def test_client_without_key_still_raises(self):
        client = llm.LLMClient(self._cfg(), self.data, api_key=None)
        with self.assertRaises(llm.LLMError):
            client._api_key()

    def test_gxks_key_roundtrip(self):
        fake = "test-" + "key-for-unit-test"
        db_path = self.data / "q.db"
        gxks_ai_config.save_key(
            db_path, data_dir=self.data, provider="agnies", api_key=fake)
        key = gxks_ai_config.load_key_for(
            db_path, data_dir=self.data, provider="agnies")
        self.assertEqual(key, fake)

    def test_make_llm_uses_gxks_db_not_xxt_db(self):
        src = Path(__file__).resolve().parents[1] / "scripts" / "gxks_work.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("gxks_ai_config.load_key_for", text)
        self.assertIn("api_key=key", text)
        self.assertIn("gxks_questions.db", text)


if __name__ == "__main__":
    unittest.main()
