# 会话记录 2026-10-24-09 — M1 完成：登录实机验证 + 课程抓取入库

## 本次目标

承接 devlog 07：用户在 AI 代启动的浏览器窗口完成登录（00:45），验证会话；随后按工作模式直接推进 M1 后半段（课程抓取）。用户中途强调**账号安全，不能被封**。

## 结果

✅ **M1 完成**：

1. 登录成功、state.json 保存；`xxt status` → `[OK] 会话有效`（登录+探测+滚动续期全链路实机验证）
2. 实况核对课程列表数据源：学习空间页面自身 XHR `mooc2-ans/visit/courselistdata`（返回 HTML 片段，非 JSON）
3. `fetch_courses` + `xxt courses` 落地：**7 门课程全部入库**，解析走 `div.course[id^=c_]` 卡片（courseId/clazzId/curPersonId 隐藏域 + `.course-name` + 入口链接）
4. 测试 4/4 通过（新增课程解析测试，夹具为合成数据）

## 关键决策及理由

1. **课程列表复用页面自身 XHR（截获响应体）而非解析 DOM/额外请求**：零额外流量、与真人浏览完全一致——直接回应"不能账号被封"。
2. **最小访问原则入 AGENTS 硬性约定**：用户明确强调账号安全；侦察合并、不重复跑 recon、测试用合成夹具、批量抓取带随机间隔。
3. **章节树不预抓（7 门课只入库列表），并入 M2 学哪门抓哪门**：避免无目的批量访问课程页；M2 本来就要进课程页。
4. **滚动续期（v0.4 §5.1）落地到 fetch_courses 与 probe_session**：每次浏览器会话结束回写 storage_state。
5. schema v2：courses 表补 `clazz_id`（后续章节/任务点接口需要），带轻量 ALTER 迁移。
6. 依赖 +beautifulsoup4（解析接口 HTML 片段）。
7. 第一次登录失败教训（登错窗口/5 分钟超时）→ 已被诊断化日志解决；本轮 80 秒完成登录。

## 账号安全台账（本会话）

- 实况访问学习空间共 6 次（status 探测 1 + recon 3 + fetch 1 + 滚动续期回写 0 额外）；登录 1 次成功（预算制：24h ≤1~2 次，余量充足）
- 全部流量为真人浏览器指纹（Playwright Chromium + storage_state），无裸 HTTP 请求

## 改动文件

- `src/xxt/adapters/chaoxing/courses.py`（新增）— courselistdata 片段解析（纯函数可测）
- `src/xxt/adapters/chaoxing/__init__.py` — fetch_courses（XHR 截获+解析+滚动续期+失效检测）
- `src/xxt/adapters/chaoxing/selectors.py` — 更新为"已核对/未核对"清单
- `src/xxt/session/login.py` — probe_session 滚动续期
- `src/xxt/cli.py` — `xxt courses` 命令（抓取+upsert+列表展示）
- `src/xxt/store/db.py` — schema v2（courses.clazz_id + 迁移）
- `pyproject.toml` — +beautifulsoup4
- `scripts/recon_space.py`（新增）— 实况侦察脚本（改版后复用）
- `tests/fixtures/courselist_sample.html`、`tests/test_courses.py`（新增）
- `docs/PLAN.md` v0.5、`AGENTS.md`（最小访问原则+状态速览）

## 交接块

- **进行中**：P0 的 M1 完成；M2（视频自动学习）未启动
- **下一步第一个动作**：用户说"继续"→ M2：选定一门课，实况进课程页核对章节树（§5.8 已有可复用选择器清单），实现页内 watcher + 拟人化节奏引擎
- **阻塞点**：无（登录会话有效、7 门课程已入库、选择器清单就绪）
