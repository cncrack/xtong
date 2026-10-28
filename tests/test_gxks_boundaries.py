"""第二平台（gxks）边界守护测试 —— T1，2026-10-29。

守四件事，每一条都对应一次真实的踩坑或一条硬性约定：

1. **平台细节不出适配层**：`src/xxt` 除 `adapters/` 外不得出现站点字符串
   （超星侧同类事故：平台 URL 散落到核心层后，换平台要全局搜改）；
2. **两个适配器互不引用**：`adapters/gxks` 不得 import `chaoxing`，
   反之亦然——否则一个平台的改版会牵连另一个平台；
3. **选择器文件是纯数据**：`gxks/selectors.py` 里不能有 `def`、不能 import playwright，
   否则“改版只改一个文件”的承诺就失效了；
4. **持久化工厂真的建 profile**：`persistent_launch_kwargs()` 必须产出 `user_data_dir`
   且目录存在——否则登录态与续播 cookie 无处安放（平台档案 §2.10 #7）。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "xxt"
GXKS = SRC / "adapters" / "gxks"

# 只允许出现在适配层里的站点特征串
PLATFORM_STRINGS = ("gaoxiaokaoshi", "LibraryStudy", "updateTime.ashx")


class PlatformIsolationTest(unittest.TestCase):
    def test_core_layer_has_no_site_strings(self):
        """核心层（含 store/panel/cli）不得出现任何站点特征串。"""
        bad: dict[str, list[str]] = {}
        for py in SRC.rglob("*.py"):
            rel = py.relative_to(SRC).as_posix()
            if rel.startswith("adapters/"):
                continue
            text = py.read_text(encoding="utf-8")
            hits = [k for k in PLATFORM_STRINGS if k in text]
            if hits:
                bad[rel] = hits
        self.assertEqual(bad, {}, f"平台特征串漏到核心层：{bad}")

    def test_gxks_does_not_reference_chaoxing(self):
        """两个适配器互不引用：gxks 里出现 chaoxing 就说明开始耦合了。"""
        offenders: list[str] = []
        for py in GXKS.rglob("*.py"):
            if "chaoxing" in py.read_text(encoding="utf-8").lower():
                offenders.append(py.relative_to(SRC).as_posix())
        self.assertEqual(offenders, [], f"gxks 适配器引用了超星：{offenders}")


class SelectorsArePureDataTest(unittest.TestCase):
    def test_selectors_has_no_logic(self):
        """选择器文件必须能被“只改数据、不改逻辑”地改。"""
        src = (GXKS / "selectors.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", src, flags=re.S)   # 文档字符串里的示例不算
        self.assertNotIn("import playwright", code)
        self.assertIsNone(re.search(r"^\s*def\s+", code, re.M),
                          "selectors.py 出现函数定义，逻辑应移到 __init__.py")


class PersistentFactoryTest(unittest.TestCase):
    def test_kwargs_carry_profile_dir_and_create_it(self):
        from xxt.core.browser import persistent_launch_kwargs

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "profiles" / "manual-01"
            kwargs = persistent_launch_kwargs(target, headless=False, match_screen=True)
            self.assertEqual(kwargs["user_data_dir"], str(target))
            self.assertTrue(target.is_dir(), "profile 目录应被自动创建")
            self.assertFalse(kwargs["headless"], "显式 headless=False 必须生效")
            self.assertTrue(kwargs["no_viewport"], "match_screen=True 应关掉固定视口")
            self.assertIn("args", kwargs)

    def test_match_screen_off_by_default(self):
        from xxt.core.browser import persistent_launch_kwargs

        with tempfile.TemporaryDirectory() as tmp:
            kwargs = persistent_launch_kwargs(Path(tmp) / "p", headless=True)
            self.assertNotIn("no_viewport", kwargs)

    def test_persistent_entries_exist(self):
        from xxt.core import browser

        for name in ("persistent_launch_kwargs", "persistent_launch", "async_persistent_launch"):
            self.assertTrue(callable(getattr(browser, name, None)), f"缺少 {name}")


class GxksAdapterSkeletonTest(unittest.TestCase):
    def test_adapter_registered_and_honest_about_todo(self):
        """注册了但不能假装实现：调未实现的方法必须抛 NotImplementedError。"""
        from xxt.adapters import get_adapter
        from xxt.adapters.gxks import GxksAdapter

        class _S:
            raw: dict = {}
            platform = {"name": "gxks"}

        adapter = get_adapter(_S())          # type: ignore[arg-type]
        self.assertIsInstance(adapter, GxksAdapter)
        self.assertTrue(adapter.login_url.startswith("http://"))
        with self.assertRaises(NotImplementedError):
            adapter.login(Path("ignored.json"))


if __name__ == "__main__":
    unittest.main()
