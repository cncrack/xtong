"""G-6 Key 解耦测试（2026-11-26）。架构：共用文件不动，gxks 用自己的客户端。"""
from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_WORK = (ROOT / "scripts" / "gxks_work.py").read_text(encoding="utf-8")
SRC_GXKS_LLM = (ROOT / "src" / "xxt" / "adapters" / "gxks" / "llm.py").read_text(encoding="utf-8")


def _code_only(src):
    """去掉注释和 docstring，只留代码（用于查真调用而非说明文字）。"""
    out = []
    in_doc = False
    tq = chr(34) * 3
    for line in src.split(chr(10)):
        s = line.strip()
        # 单行 docstring ("""...""") 不能翻转状态
        if s.startswith(tq) and s.endswith(tq) and len(s) > 6:
            continue
        if s.startswith(tq):
            in_doc = not in_doc
            continue
        if in_doc or s.startswith("#"):
            continue
        out.append(line)
    return chr(10).join(out)


class TestDecoupledKeySource(unittest.TestCase):
    def test_shared_llm_unchanged_from_main(self):
        r = subprocess.run(
            ["git", "diff", "main", "--", "src/xxt/ai/llm.py"],
            cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual((r.stdout or "").strip(), "",
                         "ai/llm.py 不应有相对 main 的改动（共用文件不动）")

    def test_gxks_client_overrides_only_api_key(self):
        self.assertIn("class GxksLLMClient(LLMClient)", SRC_GXKS_LLM)
        self.assertIn("def _api_key(self)", SRC_GXKS_LLM)
        self.assertNotIn("def chat(self", SRC_GXKS_LLM)

    def test_gxks_client_reads_gxks_db(self):
        code = _code_only(SRC_GXKS_LLM)
        self.assertIn("gxks_ai_config.load_key_for", code)

    def test_gxks_client_never_reads_xxt_db(self):
        code = _code_only(SRC_GXKS_LLM)
        self.assertNotIn("= data_dir / " + chr(34) + "xxt.db" + chr(34), code)
        self.assertNotIn("SELECT value FROM app_config", code)

    def test_work_uses_make_gxks_llm(self):
        code = _code_only(SRC_WORK)
        self.assertIn("from xxt.adapters.gxks.llm import make_gxks_llm", code)
        self.assertIn("client = make_gxks_llm(settings)", code)
        self.assertNotIn("client = LLMClient(settings.llm", code)


class TestGxksClientRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        from xxt.store.schema_gxks import open_question_bank
        self.db = open_question_bank(self.data / "q.db")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_client_gets_key_from_gxks_db(self):
        from xxt.adapters.gxks.llm import GxksLLMClient
        from xxt.store import gxks_ai_config
        fake = "test-" + "key-for-unit-test"
        gxks_ai_config.save_key(
            self.data / "q.db", data_dir=self.data,
            provider="agnies", api_key=fake)
        client = GxksLLMClient(
            {"base_url": "https://api.example/v1", "model": "m"},
            db_path=self.data / "q.db", data_dir=self.data, provider="agnies")
        self.assertEqual(client._api_key(), fake)

    def test_client_raises_without_key(self):
        from xxt.adapters.gxks.llm import GxksLLMClient
        from xxt.ai.llm import LLMError
        client = GxksLLMClient(
            {"base_url": "https://api.example/v1", "model": "m"},
            db_path=self.data / "q.db", data_dir=self.data, provider="agnies")
        with self.assertRaises(LLMError):
            client._api_key()


if __name__ == "__main__":
    unittest.main()
