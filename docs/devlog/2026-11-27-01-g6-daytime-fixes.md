# 2026-11-27-01 G-6 白天攻坚（补记）

> ⚠ **本篇是事后补记**（2026-11-28 发现 11-27 白天的 6 个提交没有对应 devlog，
> 违反 AGENTS「会话结束前必须追加 devlog」的约定，现补上）。
> 晚上的部分见 `2026-11-28-01`。

---

## 一、11-27 白天修掉的 6 个断点

按提交顺序（16:10 ~ 17:44）：

| 提交 | 问题 | 根因 |
|---|---|---|
| `f676a9c` | 做完练习状态不变；点交卷没反应 | ① `gxks_pending.add(course_id=str(args.course))` —— argparse **从未定义** `--course`，填完题写 pending 时崩；② `wait_graded` 默认 60 秒，你从点"确认交卷"到浏览器交卷切窗口不够 |
| `a54c68a` | 点交卷没弹窗 | 收到确认后**立刻 `connect_over_cdp`**，Playwright 连着 CDP 会 dismiss confirm（Q31 老坑）→ 改纯 HTTP 只读轮询 `page_urls()` |
| `3f049a3` | 交卷后等不到判分页 | 平台先跳 **`ExamMessage.aspx`**（交卷成功页），不是直接 `viewExam` → 从中间页 query 取 `Tk_Cl_Id`/`Clerk_Kscj_Id` 自己拼判分页 URL |
| `7079e80` | 整条链断在入库前 | **`exam_start_id` 从未提取**（在答题页 URL 的 `&ExamStartId=`），而 `import_bundle._validate` 强制要它非空；`start_time` 同理；`_annotate_attempts` 的 `except` 静默吞异常导致无迹可查 |
| `c22cc72` | 「已核验」看不到 | ① `verify_by_site_answer` 返回值被丢弃，终端不打印；② `list_pending` 默认只查 `pending`，全标成 `verified` 后整块不渲染 |
| `0d9e77d` | Chrome 被关后提示难懂 | `ECONNREFUSED` + Playwright call log + 让人手敲 chrome.exe 命令行 → 改成「浏览器没开（端口 N 没在监听）→ 到面板点启动并登录」 |

## 二、方法论上的转折

`7079e80` 那次我做了**端到端演练**（用真实 `raw_rows` 形状造判分页 HTML，一路走到
`_annotate_attempts`），**一次挖出 3 个断点**，而不是修一个等用户测一个。

这是对用户批评「你能不能一次性把链条全部搞定，一次修一个问题还要来回折腾」
的直接回应。之后新增 `tests/test_gxks_e2e_review_import.py` 专门守住"分段全绿、
真实链路却断"这种局面。

## 三、测试

`pytest` 1443 → **1471**（+28）。

## 交接块

- **进行中**：无
- **下一步第一个动作**：继续 1.6 实跑验证（晚上做的，见 `2026-11-28-01`）
- **阻塞点**：判分页真实结构、交卷入口都还在靠实机试错
