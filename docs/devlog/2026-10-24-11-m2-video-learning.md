# 会话记录 2026-10-24-10 — M2 视频自动学习：实现与验收通过

## 本次目标

用户指定"知行合一，做最好的自己"为首发课程，启动 M2：视频自动学习（外层编排 + 页内 watcher）。

## 结果

✅ **M2 核心验收通过**：`xxt learn 知行合一 --limit 1 --ignore-window` 实况运行——

```
01:18:21 进入知识点：第一章 2.1 尽快适应大学生活（上）
01:18:25 开始播放：倍速 1.23（区间随机）
01:18:28~01:24:32 进度 3→449s 稳步推进（449s 视频 @1.23x，实际 367s，数据吻合真实播放）
01:24:32 播放结束（ended=True）
01:24:32 拟人间隔 13 秒（三档加权抽样）
01:24:46 学习会话结束：视频 1 个；原因：达到 limit
```

落库：章节 2/70 done（导言平台已完成 + 2.1 本次完成），run_logs 记录 video-done。

## 关键发现（实况侦察）

1. **课程页已是新版 fanyav3**：stucoursemiddle 跳转 `mooc2-ans-vue/fanyav3/stu`，§5.8 的旧版
   `#coursetree` 不在首页——需点主 frame 导航"**章节**"进入 `mycourse/studentcourse` frame。
2. **新版章节树**：容器 `#fanyaChapter`；叶子 `div.chapter_item[id^=cur<知识点id>]`，
   `onclick=toOld(courseId, knowledgeId, clazzId)`；已完成标记 `.icon_yiwanc`；任务数
   `input.knowledgeJobCount`。共 70 叶子。
3. **点叶子 → 旧版知识点页**：toOld 导航后 §5.8 旧版选择器全部适用（视频 iframe、
   `#prevNextFocusNext`、`.prev_title`）——新旧混合架构确认。
4. **每日学习时段保护真实验效**：凌晨 01:07 首跑被正确拒绝（"超出每日学习时段"）——
   防封设计起作用了。为验收加了显式 `--ignore-window` 调试开关（打 WARNING 日志）。

## 修复的缺陷（验收中发现）

1. **expose_binding 回调签名**：首参是 source 对象，原 `_on_event(payload)` 把 source 当载荷
   解析失败被静默吞掉 → 事件管道全断、外层盲等。修复为 `(self, source, payload)`。
2. **盲切保护**：新增"watcher 事件 90 秒未达即中止会话"——管道失效时绝不盲点
   "下一节"造成快进（首次盲跑约 10 分钟后人工停止，平台侧 2.1 已记录部分进度，
   重跑完整观看覆盖，无副作用）。

## 新增能力清单

- `core/pace.py`：拟人节奏引擎（三档切节间隔加权抽样 / 倍速区间 / 每日窗口含跨午夜 / 专注会话）
- `[pace]` 配置段（全部区间化，代码零固定 sleep）
- `chapters.py`：新版章节树解析（70 叶子含平台完成状态、任务数）
- `watcher.js`：页内 watcher（静音、倍速、暂停/停滞恢复、事件上报、绝不改 currentTime）
- `study.py`：StudyRunner（长会话、滚动续期、事件泵、拟人间隔、异常安全中止）
- CLI：`xxt chapters <课程>`、`xxt learn <课程> [--limit N] [--ignore-window]`
- 测试 10/10（新增章节解析、节奏引擎用例，夹具均合成数据）

## 账号安全台账（本会话）

课程页访问 6 次（侦察 4 + chapters 1 + learn 2，含一次盲跑中止）；视频 2.1 完整真实播放；
全程真人浏览器指纹；凌晨时段保护生效 1 次。

## 改动文件

- 新增：`src/xxt/core/{__init__,pace}.py`、`adapters/chaoxing/{chapters.py,watcher.js,study.py}`、
  `tests/fixtures/chapter_tree_sample.html`、`tests/test_chapters_pace.py`、`scripts/recon_course.py`、`scripts/recon_course_enter.py`
- 修改：`cli.py`（chapters/learn 命令）、`adapters/base.py`（+fetch_chapters 抽象）、
  `adapters/chaoxing/__init__.py`（fetch_chapters）、`config.py`+`settings.toml`（[pace]）、`pyproject.toml`（package-data）

## 交接块

- **进行中**：M2 核心已验收；整课程连续推进（多知识点经"下一节"串联）已实现但仅单视频验证
- **下一步第一个动作**：M3——① 白天窗口内整课程实跑验证连续推进与断点续跑 ② 经"下一节"推进的知识点状态精确同步（当前只标起始叶子）③ 异常恢复硬化
- **阻塞点**：无（用户白天说"继续"即可整课推进；或自行 `xxt learn 知行合一`）
