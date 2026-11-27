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



class TestBrowserNotOpenHint(unittest.TestCase):
    """回归：Chrome 被关掉后的提示要可操作（2026-11-27 实拍）。

    用户做 1.4 时报 `ECONNREFUSED 127.0.0.1:9222` + 一串 Playwright call log，
    hint 还让人去手敲 chrome.exe 命令行 —— 又长又不解决他的实际问题。
    真正该做的是「回面板点启动并登录」。
    """

    def test_connect_error_tells_user_to_click_start(self):
        src = (Path(__file__).resolve().parents[1] / "src" / "xxt" / "adapters"
               / "gxks" / "browser.py").read_text(encoding="utf-8")
        self.assertIn("浏览器没开", src)
        self.assertIn("启动并登录", src)
        # 不该再让人手敲命令行
        self.assertNotIn("--user-data-dir=\"<该账号专用目录>\"", src)

    def test_distinguishes_port_not_listening(self):
        src = (Path(__file__).resolve().parents[1] / "src" / "xxt" / "adapters"
               / "gxks" / "browser.py").read_text(encoding="utf-8")
        self.assertIn("if not port_alive(cdp_port):", src)


if __name__ == "__main__":
    unittest.main()



class TestNoMissingArgparseAttr(unittest.TestCase):
    """回归：gxks_work.py 引用了 argparse 里不存在的参数（2026-11-27 实拍）。

    用户点交卷后「关闭」没反应。日志：填完 3 题后立刻
    [FAIL] AttributeError: Namespace object has no attribute course

    真因：gxks_pending.add(..., course_id=str(args.course or ""))，
    但该脚本 argparse 从未定义 --course。
    后果更阴：题已填进浏览器，「等确认」信号还没写出就抛异常，
    work_review 流程根本没启动 → 面板没在等确认，「关闭」自然没反应。
    """

    def test_all_args_attrs_are_defined(self):
        import re as _re
        import ast
        src = Path(__file__).resolve().parents[1] / "scripts" / "gxks_work.py"
        text = src.read_text(encoding="utf-8")
        tree = ast.parse(text)
        # ① argparse 里定义的：从 add_argument 的第一个字面量参数取
        definedvex = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "add_argument"):
                continue
            for arg0 in node.args[:1]:
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    name = arg0.value.lstrip("-").replace("-", "_")
                    if name and name != "h":
                        definedvex.add(name)
        # ② 代码里用的 args.xxx（去注释；注释里的引用不算 bug）
        used = set()
        for line in text.split(chr(10)):
            code = line.split(chr(35), 1)[0]          # 去掉 # 后的注释
            for m in _re.finditer(r"\bargs\.([A-Za-z_][A-Za-z0-9_]*)", code):
                used.add(m.group(1))
        missing = sorted(used - definedvex)
        self.assertEqual(missing, [],
                         f"引用了 argparse 里没有的参数：{missing}")


if __name__ == "__main__":
    unittest.main()

