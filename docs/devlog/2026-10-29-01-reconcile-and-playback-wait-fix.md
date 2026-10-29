# 2026-10-29-01 刷前对账 + 播放等待与记账去重

## 本次目标与最终结果

两条主线，都已落地并全量测试通过（**300 passed**，较会话开始时 +20）。

### 一、记账对账：本地章节状态以平台标识为准（✅ 完成）

起因是核实账号#2《知行合一》：平台 70/70 全部已完成，本地仍记 29 节待学。
根因是结构性的——`cmd_chapters` 的 UPSERT 只回写 `name/idx`，**平台"已完成"这一
唯一权威事实没有任何入库通道**，缺口永不自愈，调度因而反复白跑真实页面。

用户拍板口径：「这个要以平台的标识为主」「那以后刷之前是不是要和平台先对账一下」。

落地：

- 新建 `src/xxt/core/reconcile.py`（163 行）：`guardrails()` / `pre_session_enabled()` /
  `reconcile_local()`。规则集中一处，三处共用（`chapters` 命令、同步学习器、异步学习器），
  避免"只改一条路径、另一条文本报错"。
- **升级无条件**（平台已完成 → 本地置 done），**降级须显式授权 + 两道护栏**
  （覆盖度 ≥90%、单次降级比例 ≤20%），且学习路径**绝不降级**（误判会立刻引发整门课重刷）。
  `leaves` 为空时绝不做任何对齐（防"页面没加载出来 → 整门课被打回"）。
- 刷前对账接进两条学习路径：章节树那一页本来就要打开，顺手读 `#fanyaChapter` 的
  outerHTML 解析，**零额外页面访问**；对账后重挑首节，全部归位完就直接收工不白跑。
  对账失败绝不影响学习（catch 后回到"按本地记账刷"）。
- `cli.py`：`CHAPTERS_UPSERT` 升为模块级常量且**只管建行与元数据**，状态一律交
  `reconcile_local`；新增 `--no-sync`、`--demote`。
- 配置化（用户：「这个可以做成配置项，但要明确只对谁起作用」「默认，后期自己调节」）：
  新增 `[reconcile]` 段四键，**每项注释都写明作用对象**（pre_session → 全部账号的每次
  学习会话；demote 与两道护栏 → 只有命令行 `chapters`）；`quota.float_ratio` 默认
  `[0.7, 1.0]` → `[0.85, 1.0]`（用户选 B），注释写明"只作用于每日额度上限的抽样"。
- 面板：① 今日目标补「额度浮动上/下限」两框；新增 **⑦ 记账对账**（刷前对账、允许降级、
  两道护栏），GET/PUT/校验/留痕中文映射全链路 + 越界拒绝（下限>上限、非布尔当场拒）。

### 二、播放中断（用户问"为何出现 2 次"）：查清两类原因，修掉其中真 bug（✅ 完成）

实测日志倒推，今天账号#1 共 6 次中断，分两类：

1. **长视频撞硬顶（不是 bug，但要修）**：`_play_video` 把 `max_wait` 写死 600 秒，
   凡「时长 ÷ 倍速 > 600 秒」的一节必然在 79%~96% 被砍（644/816s 恰好 600 秒墙钟、
   710/741s 585 秒……）。本课视频普遍 620~820s，几乎节节都撞，白花一次"中断→重载"。
2. **watcher 注入失败谎报 100%（真 bug）**：`21:33:19,471 注入失败` → 3 毫秒后
   `中断于 100%（346/346s）`。注入失败提前 return 时 `_last_progress/_last_video_dur`
   还是**上一节**的值，于是根本没播的一节显示 100% → 过续播门槛 → 整节重播 →
   同一节写出两条 `video-done`（21:33:15 与 21:39:16）。额度的两个口径都直接数
   video-done 行，**重复一条就是白扣一份额度**，还挤掉本该学的下一节。

用户批准「做 A+B+C」：

- **A**：进度快照在任何提前 return 之前归零；注入失败重试（`WATCHER_INJECT_TRIES=2`，
  间隔 3 秒再试，因为最常见原因是 iframe 刚重建）。日志改为「试了 2 次，本节保持待学」。
- **B**：`max_wait` 改成随剩余时长弹性延长——新增纯函数 `core/playback.next_wait_limit()`
  （= 已过时间 + 剩余内容÷倍速 + 余量 120s，封顶 1800s），两个引擎都调它。
- **C**：写 `video-done` 前判重 `core/quota.video_done_counted()`；已记过则只标章节状态，
  写一条新事件 `video-dup-skip`（面板中文「重播不重复计」），不重复计额度。
  用 `instr(detail, ?)` 精确子串匹配而不是 LIKE（节名可能含 `%`/`_`）；查库异常按"没记过"
  处理，宁可多记一次也不把真播完的一节丢掉。

### 顺带清掉的一处纪律漏洞

上一段会话为了让同步学习器做刷前对账，写了 `from .astudy import pick_pending_after`——
同步引擎反向依赖异步引擎。它没被静态门禁拦住（那条断言只查单向），但异步侧的函数随时可能
变 async，混用正是 2026-10-25 账号#2 事故的成因。本次把它下沉到 `pagejs.py`（只查库、
不碰 page/frame，本就是"两个引擎唯一共用通道"），并在 `test_module_boundaries.py`
补了**反向禁止**断言 + 把 B/C 两个新符号写进"两引擎必须同源"的断言里。

## 关键决策与理由

- **降级只在 `chapters` 提供、学习路径永不降级**：升级错顶多多刷一次；降级错会让整门课
  重刷，代价不对称。护栏拦的是"完成图标未渲染/页面改版"这类批量误判。
- **对账逻辑放 `core/` 而不是 `adapters/chaoxing/chapters.py`**：它不含 URL/选择器/接口，
  第二平台可直接复用（先写进 chapters.py 后判断分层不当，已撤销）。
- **等待上限与重试次数用代码常量而非配置项**：它们不是用户会调的业务参数（用户关心
  "长视频能不能播完"，不关心余量是 120 还是 180），做成配置反而要配面板输入框。
  真需要调时再补 `[learn]` 项。
- **C 不改 `video-done` 的 detail 格式**：`_DUR_RE = ｜(\d+)s$` 只认末尾，加字段会动额度
  解析口径；判重改用既有结构 + `instr`，零 schema 变更。

## 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `src/xxt/core/reconcile.py` | **新建**：`EVENT` / `guardrails()` / `pre_session_enabled()` / `reconcile_local()` |
| `src/xxt/core/playback.py` | 新增 `WAIT_INIT/SLACK/CEIL_SECONDS`、`WATCHER_INJECT_TRIES`、`next_wait_limit()` |
| `src/xxt/core/quota.py` | 新增 `video_done_counted()`（instr 判重，异常按未记处理） |
| `src/xxt/cli.py` | `CHAPTERS_UPSERT` 常量与职责注释；调 `reconcile_local`；`--no-sync` / `--demote`；输出区分"本地记已完成" |
| `src/xxt/config.py` | `DEFAULTS["reconcile"]` 新段（每项注明作用对象）；`float_ratio` 默认 `[0.85, 1.0]`；`Settings.reconcile` |
| `config/settings.toml` | 同上落盘（`[reconcile]` 四键 + float_ratio 注释） |
| `src/xxt/adapters/chaoxing/astudy.py` | 刷前对账块；A 快照归零+注入重试；B 弹性上限；C 记账判重；`pick_pending_after` 移出改从 pagejs 引入 |
| `src/xxt/adapters/chaoxing/study.py` | 同步版接同一套（对账 + A + B + C），不再 import astudy |
| `src/xxt/adapters/chaoxing/pagejs.py` | 收下 `pick_pending_after`（两引擎共用） |
| `src/xxt/adapters/chaoxing/chapters.py` | 只留一行注释指向 `core.reconcile`（撤销误放的实现） |
| `src/xxt/panel/app.py` | ① 浮动上下限两框；新增 ⑦ 记账对账区块；GET/PUT/校验/`CFG_CN`；`EVNAME` 加 `reconcile`、`video-dup-skip`；日志"💻 学习"类纳入 `reconcile` |
| `tests/test_chapters_status_sync.py` | **新建** 14 用例：入库不碰状态、升级、降级需授权、两道护栏各拦一次、空树不动库、护栏参数只从配置读一处 |
| `tests/test_video_done_dedup_and_wait.py` | **新建** 9 用例：长视频不再 600 秒砍断、封顶、余量、倍速缺失兜底；判重按节/账号/日区分、非 video-done 不挡、LIKE 元字符、查库异常放行 |
| `tests/test_panel.py` | 2 用例：浮动+对账参数往返（百分数→比例列表落库）、越界/类型错拒绝 |
| `tests/test_module_boundaries.py` | 新增"study 不得 import astudy"；两引擎同源断言补 3 个符号 |

## 用户下一步操作清单

**进行中**：账号#1 一直在刷（用户明确要求不停），未受本次改动影响。

1. **重启面板才能看到新配置项**（⚙ 全局设置 → ⑦ 记账对账 + ① 里的浮动上下限两框）。
   代价：会带走正在刷的会话与守护。**不重启也能用**——面板拉起的子进程走
   `python -m xxt.cli` 加载磁盘新代码，所以**下一个学习会话起 A/B/C 与刷前对账自动生效**。
2. **验证方式**（无需操作，看一眼即可）：下一次会话日志里
   - 出现 `记账对账`（`reconcile`）= 归位了 N 节；**没有出现也正常**（本地本就一致）；
   - 长视频不再在 10 分钟整报 `播放中断`；
   - 若出现 `重播不重复计`（`video-dup-skip`），说明 C 挡住了一次重复记账（会伴随 WARNING）。
3. **想启用降级对齐**：面板打开「允许降级」，或命令行 `xxt chapters 课程名 --account N --demote`
   （护栏不过仍只报警不改库）。默认关。
4. 账号#2 其余 6 门课仍无章节树，等点名再做（最小访问原则）。

**阻塞点**：无。

**待办（等用户口令）**：本会话改动共 15 个代码/配置文件 + 本 devlog。
main 分支此前已有 2 个待推送提交（说"推送"先跑 `scripts/push_check.py`）。
`dist\xxt.exe` 仍是旧代码，需要重新构建才能带上这些修复。

> 提交拆分说明：原本打算分两个 commit（对账一条、播放修复一条），但
> `astudy.py`/`study.py`/`panel/app.py` 三个文件同时含两组改动，git 按文件提交拆不开；
> 强行拆会留下"测试不绿的首个 commit"，违背不带病提交。最终**合为一个 commit**。
