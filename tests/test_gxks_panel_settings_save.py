"""面板设置的**白名单**与「本次跑几节」配置项（2026-11-07 修的真 bug）。

## 背景（这是用户实际踩到的）

用户在「账号设置 → 面板设置」把「同时学习上限」改成 5 → 界面提示「已保存」→
但 `/api/state` 回来的 `limit_concurrency` 仍然 **2**，实际并发还是 2 个。

根因：`app.py::panel_settings_save` 里有一个**手写白名单**

```python
items = {k: body.get(k) for k in ("reload", "poll_seconds") if k in body}
```

而 P2a/P2b 加的 `max_concurrency` / `start_jitter` / `fleet_daily_minutes`
**没加进这个白名单** → 前端提交了、`gxks_panel_settings.save()` 也支持，
但在这里就被悄悄过滤掉，且**不报错**（过滤后剩下的 items 仍然"保存成功"）。

这与 2026-11-06 那个 `except: pass` 是同一类**骗人 bug**：接口说成功、实际没生效，
用户只能得出"改了没效果"，且没有任何线索。

诊断实证：`POST {'max_concurrency': 5}` → 回 `ok:true`，回包 settings 里仍是 **2**。

## 守什么

① 白名单必须覆盖 `save()` 支持的全部键（否则又漏）；
② 改的新值必须真的落盘并能读回来；
③ 「本次跑几节」独立成配置项，且表格/批量/后端三处默认值统一；
④ 保存接口要把**真实落盘值**回给前端（不然界面会显示用户输入、后端是别的值）。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xxt.config import Settings
from xxt.panel_gxks import app as panel_app
from xxt.panel_gxks.app import create_app
from xxt.store import gxks_panel_settings as gp

FAKE_ACCOUNTS = {"accounts": [{"label": "a1", "port": 9222},
                              {"label": "a2", "port": 9223}]}


class PanelSettingsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "accounts.local.json").write_text(
            json.dumps(FAKE_ACCOUNTS, ensure_ascii=False), encoding="utf-8")
        self._old_root = os.environ.get("XXT_ROOT")
        os.environ["XXT_ROOT"] = str(self.root)
        self.settings = Settings(root=self.root)
        self.client = TestClient(create_app(self.settings))
        panel_app._JOBS.clear()

    def tearDown(self):
        panel_app._JOBS.clear()
        if self._old_root is None:
            os.environ.pop("XXT_ROOT", None)
        else:
            os.environ["XXT_ROOT"] = self._old_root
        self.tmp.cleanup()


class TestSaveWhitelist(PanelSettingsBase):
    """**P0 回归**：白名单漏键 = 设置改了没效果，且不报错。"""

    def test_all_documented_keys_are_persisted(self):
        """五个并发/节数相关键逐个提交，每个都必须真的落盘。

        这条测试就是**白名单守卫**：以后 `DEFAULTS` 加新键，这里必须同步加，
        否则又会静默失败。
        """
        cases = {
            "max_concurrency": 5,
            "start_jitter": 11,
            "fleet_daily_minutes": 240,
            "sections_per_run": 7,
            "poll_seconds": 9,
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                body = self.client.post("/api/panel/settings",
                                        json={key: value}).json()
                self.assertTrue(body["ok"])
                saved = gp.load(panel_app._db_path(self.settings))
                self.assertEqual(
                    saved[key], value,
                    f"{key} 提交了 {value}、接口说已保存，但读回来是 {saved[key]}"
                    " —— 白名单又漏了这个键")

    def test_whitelist_covers_every_default_key(self):
        """白名单键集合必须 **⊇** `DEFAULTS` 里的可写键。

        `reload` 也是可写的；全部 `DEFAULTS` 都应当能被面板保存
        （例外：将来若有只读派生的键，需要在这里显式列出并写明理由）。
        """
        import inspect

        src = inspect.getsource(panel_app.create_app)
        allowed_block = src.split('allowed = (', 1)[1].split(')', 1)[0]
        allowed = {k.strip().strip('"').strip("'")
                   for k in allowed_block.replace('\n', ' ').split(',') if k.strip()}
        missing = set(gp.DEFAULTS) - allowed
        self.assertEqual(
            missing, set(),
            f"这些设置项在 DEFAULTS 里、但保存接口的白名单没有 → 用户改了不会生效：{missing}")

    def test_relogin_of_settings_is_not_silently_clamped(self):
        """越界值要被钳制**并且**回包里如实反映钳制后的值（不能让界面显示原值）。"""
        body = self.client.post("/api/panel/settings",
                                json={"max_concurrency": 999}).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["settings"]["max_concurrency"], 20)
        # 前端会用这个回包回填控件，所以它必须是**真实落盘值**
        self.assertEqual(gp.load(panel_app._db_path(self.settings))["max_concurrency"], 20)

    def test_save_returns_saved_keys(self):
        """回包要告诉前端"这次到底哪些键被接受了"（便于排查漏键）。"""
        body = self.client.post("/api/panel/settings",
                                json={"max_concurrency": 3, "bogus_key": 1}).json()
        self.assertIn("max_concurrency", body["saved_keys"])
        self.assertNotIn("bogus_key", body["saved_keys"])


class TestSectionsPerRun(PanelSettingsBase):
    """「本次跑几节」：(2026-11-07) 用户问「默认 3 和面板设置项是否同步」。"""

    def test_default_is_three(self):
        self.assertEqual(gp.load(panel_app._db_path(self.settings))["sections_per_run"], 3)

    def test_clamped_to_one_twelve(self):
        self.assertEqual(gp.load(panel_app._db_path(self.settings))["sections_per_run"], 3)
        gp.save(panel_app._db_path(self.settings), {"sections_per_run": 0})
        self.assertEqual(gp.load(panel_app._db_path(self.settings))["sections_per_run"], 1,
                         "0 节 = 点了不跑，是误操作 → 必须钳到 1")
        gp.save(panel_app._db_path(self.settings), {"sections_per_run": 40})
        self.assertEqual(gp.load(panel_app._db_path(self.settings))["sections_per_run"], 12)

    def test_state_exposes_it_to_frontend(self):
        """前端靠 `/api/state` 拿到这个默认值（表格输入框与批量弹框都用它）。"""
        gp.save(panel_app._db_path(self.settings), {"sections_per_run": 5})
        body = self.client.get("/api/state").json()
        self.assertEqual(body["sections_per_run"], 5)

    def test_study_uses_configured_default(self):
        """「学习」不传节数时，用的是面板设置里的值，不再是写死的 3。"""
        gp.save(panel_app._db_path(self.settings), {"sections_per_run": 8})
        with mock.patch.object(panel_app.gchrome, "port_alive", return_value=True), \
             mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            got = panel_app._study_one(self.settings, "a1", None)
        self.assertTrue(got["ok"], got)
        argv = list(spawn.call_args.args)
        self.assertEqual(argv[argv.index("--max-sections") + 1], "8")

    def test_explicit_max_sections_overrides_default(self):
        """行内填了就以行内为准（只影响这一次）。"""
        gp.save(panel_app._db_path(self.settings), {"sections_per_run": 8})
        with mock.patch.object(panel_app.gchrome, "port_alive", return_value=True), \
             mock.patch.object(panel_app, "_spawn_script") as spawn:
            spawn.return_value = mock.Mock(pid=1)
            panel_app._study_one(self.settings, "a1", 2)
        argv = list(spawn.call_args.args)
        self.assertEqual(argv[argv.index("--max-sections") + 1], "2")


if __name__ == "__main__":
    unittest.main()
