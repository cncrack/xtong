"""2026-11-23 方案 B：多账号并行启动浏览器 + label 自动生成。

## 用户要求

> 「7 个账号要多开时只能排队…后面可能会更多账号，这个我们在先前设计与开发的
>   时候已经说过了，账号数量比较多」

> 「可以按方案 B，可以自动注册，任务命名 xxt-gxks-chrome-<label>…
>   旧的全局任务可以（保留）」

> 「不加拼音吧，用这个（user01 递增）」

## 改了什么

1. **每账号一个计划任务** `xxt-gxks-chrome-<label>`，`-Label` 在**注册时**写进
   任务动作（`schtasks` 运行期传不进参数）。
2. **spec/result 文件按 label 分开**：`chrome_start_<label>.json` /
   `chrome_start_<label>.result.json` → 互不覆盖。
3. **删掉 `_CHROME_START_LOCK`**（原来强制串行）→ 多账号真并行，无账号数上限。
4. **自动注册**（用户选方案 C：启动时预注册 + 懒注册）。
5. **报错如实**：旧版 `returncode != 0` 一律说「任务不存在」，把用户引向重复注册；
   现在按 stdout/stderr 原文分四支（在跑 / 不存在 / 无权限 / 其他），并回传原始输出。
6. **`label` 自动生成**：留空时用 `user01` 递增（不加拼音库，用户 2026-11-23 决定）。
7. **任务状态可见**：`/api/state` 带 `chrome_task`，面板显示「任务 已注册/未注册」。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from xxt.adapters.gxks import accounts as gaccounts
from xxt.panel_gxks import app
from xxt.panel_gxks.page import PAGE

APP_SRC = Path(app.__file__).read_text(encoding="utf-8")
PS1 = (Path(__file__).resolve().parents[1] / "scripts" / "start_chrome.ps1").read_text(
    encoding="utf-8-sig")


class TestTaskNaming(unittest.TestCase):
    def test_task_name_uses_label_not_port_or_index(self):
        """用户问过「label 是什么，是序号还是端口号」——必须是账号代号。"""
        self.assertEqual(app._chrome_task_name("jia"), "xxt-gxks-chrome-jia")
        self.assertEqual(app._chrome_task_name("default"), "xxt-gxks-chrome-default")
        self.assertEqual(app._chrome_task_name("user01"), "xxt-gxks-chrome-user01")

    def test_old_global_task_kept(self):
        """用户明确「旧的全局任务可以（保留）」。"""
        self.assertEqual(app._CHROME_TASK, "xxt-gxks-chrome")
        self.assertTrue(APP_SRC.count("_CHROME_TASK =") >= 1)

    def test_label_must_be_valid_identifier(self):
        """任务名里的 label 只能是小写字母数字下划线短横线（否则任务名非法）。"""
        for good in ("jia", "user01", "a1", "test-01", "1"):
            self.assertTrue(gaccounts.is_valid_label(good), good)
        # ⚠ "1" 是合法的（正则允许数字开头）；中文/大写/空/超长不合法
        for bad in ("甲同学", "Jia", "", "a" * 25, "-abc", "a b"):
            self.assertFalse(gaccounts.is_valid_label(bad), bad)


class TestParallelNotSerial(unittest.TestCase):
    def test_start_lock_removed(self):
        """多账号必须能同时起 —— 原来的串行锁必须删掉。"""
        self.assertNotIn("_CHROME_START_LOCK", APP_SRC)
        self.assertNotIn("_start_chrome_interactive_locked", APP_SRC)

    def test_spec_per_label(self):
        """spec/result 按 label 分文件，否则后写的会覆盖前一个。"""
        self.assertIn('f"chrome_start_{label}.json"', APP_SRC)
        self.assertIn('f"chrome_start_{label}.result.json"', APP_SRC)

    def test_register_lock_only_guards_registration(self):
        """注册要串行（防并发建同一个任务），但启动本身不持锁。"""
        self.assertIn("_TASK_REGISTER_LOCK", APP_SRC)
        seg = APP_SRC.split("def _start_chrome_interactive_impl", 1)[1][:600]
        self.assertNotIn("_TASK_REGISTER_LOCK", seg)


class TestRegisterScript(unittest.TestCase):
    """**2026-11-23 踩到的真 bug**（注册静默失败）的回归守护。

    旧实现用 `powershell -Command "... -Argument \\"-NoProfile ...\\" ..."`，
    经 subprocess → powershell 两层转义后引号失效，PowerShell 把
    `-ExecutionPolicy` 当成 `New-ScheduledTaskAction` 自己的参数：
    `A parameter cannot be found that matches parameter name 'ExecutionPolicy'`。
    更糟的是后面还有 `Write-Output "REGISTERED"`，报错后照样执行 →
    面板 `"REGISTERED" in stdout` 判成成功 → **静默没注册**，
    点「启动并登录」才报「任务不存在」。

    现在：注册逻辑放进独立 `.ps1`，`-File` 调用零转义；
    成功判据 = `exitcode == 0` **且** stdout 含 `OK:`。
    """

    def setUp(self):
        self.reg = (Path(__file__).resolve().parents[1] / "scripts"
                    / "register_chrome_task.ps1")

    def test_script_exists_and_has_bom(self):
        raw = self.reg.read_bytes()
        self.assertEqual(raw[:3], b"\xef\xbb\xbf",
                         "Windows PowerShell 5.1 按 GBK 读无 BOM 的 .ps1，中文注释会解析失败")

    def test_uses_file_not_inline_command(self):
        seg = APP_SRC.split("def _register_chrome_task(", 1)[1].split(
            "def _manual_register_hint", 1)[0]
        self.assertIn("register_chrome_task.ps1", seg)
        self.assertIn('"-File"', seg)
        self.assertNotIn('"-Command"', seg)

    def test_success_needs_exitcode_and_marker(self):
        seg = APP_SRC.split("def _register_chrome_task(", 1)[1].split(
            "def _manual_register_hint", 1)[0]
        # 代码体（去掉 docstring 里的反例描述）应含两个判据
        code = seg.split('"""', 2)[-1]
        self.assertIn("returncode == 0", code)
        self.assertIn('"OK:" in out', code)
        # 「REGISTERED」只允许出现在 docstring 的反例说明里，代码里不许有
        self.assertNotIn('"REGISTERED"', code)

    def test_script_reports_failure_explicitly(self):
        text = self.reg.read_text(encoding="utf-8-sig")
        self.assertIn('Write-Output "OK: $taskName"', text)
        self.assertIn('Write-Output "FAIL:', text)
        self.assertIn("exit 1", text)
        self.assertNotIn("REGISTERED", text)

    def test_script_passes_label_into_action(self):
        text = self.reg.read_text(encoding="utf-8-sig")
        self.assertIn("-Label $Label", text)
        self.assertIn("xxt-gxks-chrome-$Label", text)

    def test_permission_denied_points_to_bulk_script(self):
        """**2026-11-23 实测**：`Register-ScheduledTask` 建「以 Administrator 运行」的任务
        需要提权，面板是普通权限进程 → `FAIL: 拒绝访问`。

        这种情况必须明确告诉用户「需要管理员权限」并指向**批量脚本**
        （一次注册全部账号，不用逐个来），而不是让他反复重试。
        """
        seg = APP_SRC.split("def _register_chrome_task(", 1)[1].split(
            "def _manual_register_hint", 1)[0]
        code = seg.split('"""', 2)[-1]
        self.assertIn("denied", code)
        self.assertIn("需要管理员权限", code)
        self.assertIn("all_accounts=True", code)
        self.assertIn("register_all_chrome_tasks.ps1", APP_SRC)

    def test_bulk_script_exists_with_bom(self):
        p = Path(__file__).resolve().parents[1] / "scripts" / "register_all_chrome_tasks.ps1"
        self.assertTrue(p.exists())
        self.assertEqual(p.read_bytes()[:3], b"\xef\xbb\xbf")


class TestTwoPhaseParallel(unittest.TestCase):
    """**2026-11-23 用户实测「只启动一个」**：方案 B 只解决了文件隔离，
    没解决批量循环仍逐个等端口（实测 2 个账号间隔 1 分 44 秒）。

    修法（用户拍板方案 A）：拆成两阶段
      ① `_trigger_chrome()` 只写 spec + `schtasks /run`，**立即返回不等端口**；
      ② 全部触发完后，`_await_chrome_ready()` 统一等。
    这样整批耗时 ≈ 最慢的一个，而不是 N 倍。

    ⚠ 只对 `start-browser` 预触发：`start`/`start-and-study` 要按并发闸/额度
    逐个判定，无条件预触发会让「本该被拦下」的账号也把 Chrome 起了。
    """

    def test_trigger_does_not_wait(self):
        """`_trigger_chrome` 必须**不等端口**（否则又变串行）。"""
        seg = APP_SRC.split("def _trigger_chrome(", 1)[1].split(
            "def _await_chrome_ready", 1)[0]
        self.assertNotIn("port_alive", seg)
        self.assertNotIn("for _ in range(80)", seg)
        self.assertIn('"triggered": True', seg)

    def test_await_is_separate_function(self):
        seg = APP_SRC.split("def _await_chrome_ready(", 1)[1][:900]
        self.assertIn("port_alive", seg)
        self.assertIn("for _ in range(80)", seg)

    def test_single_account_still_triggers_then_awaits(self):
        """单独点一个账号：触发后立即等（行为不变，用户不会觉得变慢）。"""
        seg = APP_SRC.split("def _start_chrome_interactive_impl(", 1)[1][:800]
        self.assertIn("_trigger_chrome(label, port)", seg)
        self.assertIn("_await_chrome_ready(label, port, result)", seg)

    def test_batch_pretriggers_all_then_waits(self):
        """批量必须先全部触发，再进循环。"""
        seg = APP_SRC.split('if action == "start-browser" and len(labels) > 1:', 1)[1][:700]
        self.assertIn("pretriggered[label] = _trigger_chrome(", seg)

    def test_pretrigger_only_for_start_browser(self):
        """`start` 不能预触发（要过额度/并发闸）。"""
        seg = APP_SRC.split("pretriggered: dict[str, dict[str, Any]] = {}", 1)[0][-400:]
        self.assertIn('action == "start-browser"', APP_SRC)
        # 确认 start-and-study 不在预触发条件里
        cond = APP_SRC.split("pretriggered: dict[str, dict[str, Any]] = {}", 1)[1][:120]
        self.assertIn('action == "start-browser"', cond)
        self.assertNotIn("start-and-study", cond)

    def test_browser_start_one_skips_retrigger(self):
        """预触发过的账号只等端口，不能重复 /run（会多起实例）。"""
        seg = APP_SRC.split("def _browser_start_one(", 1)[1][:2200]
        self.assertIn("pretriggered", seg)
        self.assertIn("_await_chrome_ready", seg)


class TestAutoRegister(unittest.TestCase):
    def test_register_function_exists(self):
        self.assertIn("def _register_chrome_task(", APP_SRC)
        self.assertIn("def _manual_register_hint(", APP_SRC)

    def test_register_delegates_label_to_script(self):
        """label 作为 `-Label` 参数传给注册脚本（脚本再写进任务动作）。"""
        seg = APP_SRC.split("def _register_chrome_task(", 1)[1].split(
            "def _manual_register_hint", 1)[0]
        self.assertIn('"-Label", label', seg)

    def test_preregister_on_startup(self):
        """用户选方案 C：启动时预注册已有账号。"""
        self.assertIn("_preregister_chrome_tasks", APP_SRC)
        self.assertIn("daemon=True", APP_SRC)

    def test_ps1_accepts_label_param(self):
        self.assertIn("param(", PS1)
        self.assertIn("$Label", PS1)
        self.assertIn("chrome_start_$Label.json", PS1)

    def test_ps1_keeps_legacy_fallback(self):
        """无 -Label 时回落旧全局文件，不破坏手工用法。"""
        self.assertIn("chrome_start.json", PS1)


class TestHonestErrors(unittest.TestCase):
    def test_error_has_four_branches(self):
        seg = APP_SRC.split("def _start_chrome_interactive_impl", 1)[1][:4200]
        self.assertIn("currently running", seg)     # 在跑：不报错
        self.assertIn("cannot find", seg)           # 不存在
        self.assertIn("access is denied", seg)      # 无权限
        self.assertIn("returncode=", seg)           # 其他：带原始码

    def test_raw_output_returned(self):
        """这次误导的根源是丢了 schtasks 输出 —— 现在必须带回去。"""
        seg = APP_SRC.split("def _start_chrome_interactive_impl", 1)[1][:4200]
        self.assertIn('"output":', seg)

    def test_no_longer_says_task_missing_unconditionally(self):
        seg = APP_SRC.split("def _start_chrome_interactive_impl", 1)[1][:4200]
        # 旧文案是「不存在或起不来」一律出现；现在只在 cannot find 分支里
        self.assertNotIn('"error": "交互式任务 xxt-gxks-chrome 不存在或起不来', seg)


class TestTaskStateVisible(unittest.TestCase):
    def test_state_exposes_chrome_task(self):
        self.assertIn('"chrome_task": _chrome_task_state(label)', APP_SRC)

    def test_state_function_queries_schtasks(self):
        seg = APP_SRC.split("def _chrome_task_state(", 1)[1][:700]
        self.assertIn("schtasks", seg)
        self.assertIn("registered", seg)

    def test_frontend_shows_task_tag(self):
        self.assertIn("state.chrome_task", PAGE)
        self.assertIn("任务", PAGE)
        self.assertIn("task.registered", PAGE)


class TestSuggestLabel(unittest.TestCase):
    def test_starts_at_user01(self):
        self.assertEqual(gaccounts.suggest_label([]), "user01")
        self.assertEqual(gaccounts.suggest_label(["default", "test"]), "user01")

    def test_skips_taken(self):
        self.assertEqual(gaccounts.suggest_label(["user01"]), "user02")
        self.assertEqual(gaccounts.suggest_label(["user01", "user02"]), "user03")
        self.assertEqual(gaccounts.suggest_label(["user02"]), "user01")

    def test_no_zero_pad_past_99(self):
        taken = ["user%02d" % i for i in range(1, 100)]
        self.assertEqual(gaccounts.suggest_label(taken), "user100")

    def test_generated_is_valid(self):
        for n in (0, 1, 9, 99, 100, 999):
            taken = ["user%02d" % i for i in range(1, n + 1)] if n < 100 else []
            self.assertTrue(gaccounts.is_valid_label(gaccounts.suggest_label(taken)))


if __name__ == "__main__":
    unittest.main()
