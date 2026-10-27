# Q37 文档脱敏 + push 门禁加真实姓名扫描（会话 63）

> 用户 2026-10-28 凌晨：*「那我现在改呢 不急不是吗，功能有些还没有完善 这个可以做」*
> → Q37 开工；Q12（重建 exe）按用户判断**降到"功能冻结/对外分发前"**。

## 一、目标与结果

- **目标**：把仓库里散落的真实姓名/校名换成代号，并让"以后再写回真名"在 push 前被拦下。
- **结果**：✅ 24 个文件 58 行（75 处）替换完；`scripts/push_check.py` 新增第 3 项
  「tracked 全文脱敏扫描」；**pytest 255 全绿**；复扫 0 命中。

## 二、先量再改（台账的 25 处不准）

台账 Q37 写的是"25 处（13 个文件）"——**偏低**。实际全仓扫描（md/py/toml/txt/json/yml，
跳过 `data/ .git .venv build dist`）：

| 类别 | 文件 | 处数 | 说明 |
| --- | --- | --- | --- |
| devlog 历史 | 18 篇 | 61 | 2026-10-24~27 的实测记录，样本账号名反复出现 |
| 主文档 | `docs/PLAN.md`、`STATUS.md` | 2 | 变更记录里的样本描述 |
| 代码注释 | `adapters/chaoxing/work.py:3` | 1 | "实况核对（某某 2.12 样本）" |
| 测试夹具 | `tests/test_profiles.py`、`test_stats.py`、`test_profile_read.py` | 10 | `platform_username` / 断言 / `_looks_like_school("…")` |
| **合计** | **24** | **75 处 / 58 行** | 台账少算的原因：当时只点数了主文档，devlog 与测试都漏掉 |

## 三、关键决策

1. **代号规则**：姓名 → `甲同学` / `乙同学`，校名 → `示范学院`。
   理由：句子读起来仍通顺（"甲同学 2.12 平台草稿区现有 3 题 AI 答案"），且一眼看得出是代号、不是另一个人。
2. **映射表与词表都放 `data/`**（整目录已 gitignore）：
   - `data/redact_map.local.md` —— 原名 ↔ 代号，日后需要还原语境时对照；
   - `data/redact_terms.local.txt` —— 门禁词表，每行一条，`#` 注释。
   **脚本里不含任何真值**：别人 clone 本仓库时没有词表，该检查自动跳过，既不误报也不卡住。
3. **门禁只扫 diff 不够**：历史文档里的姓名永远不会出现在 diff 里 → 新增
   `scan_tracked_text()` 扫**当前全部 tracked 文本文件**（二进制解码失败即跳过）。
   这是"存量清干净 + 增量别再写回来"的配套。
4. **不重写 git 历史**：已推送的 100+ 提交里仍有原名（`git log` 可查）。重写历史必须
   force push，违反项目红线；远端是私有仓库 → 记为**已知残留**，新增内容一律用代号。
5. **测试夹具一并改成代号**：不改也能跑，但留真名等于把数据留在仓库里——这是
   "测试夹具用合成数据"约定的延伸。

## 四、改动文件

| 文件 | 改动 |
| --- | --- |
| `docs/devlog/2026-10-24-*`（9 篇）、`2026-10-25-*`（4 篇）、`2026-10-26-*`（3 篇）、`2026-10-27-*`（2 篇） | 姓名/校名 → 代号（共 61 处） |
| `docs/PLAN.md` | v0.14 变更记录里的样本描述 |
| `STATUS.md` | 台账第 40 行 `school=…` |
| `src/xxt/adapters/chaoxing/work.py` | 文件头注释的样本账号名 |
| `tests/test_profiles.py`、`tests/test_stats.py` | fixture 的 `platform_username` 与断言 |
| `tests/test_profile_read.py` | `_looks_like_school("…")` 入参换成合成校名 |
| `scripts/push_check.py` | +`redact_terms()` / `local_needles()`（原 `extra_needles` 更名）/ `scan_tracked_text()`；命中提示不打印原值；输出改 `[1/5]~[5/5]` |
| `data/redact_map.local.md`、`data/redact_terms.local.txt` | 新建，**不入库** |

## 五、验证

| 项 | 结果 |
| --- | --- |
| 用替换前的扫描脚本复扫全仓 | `files=0 hits=0` |
| 直接调门禁函数（词表 3 条） | `tracked hits: []`；`git ls-files` 不含两份 data 文件 |
| `pytest -q` | **255 passed**（夹具换代号后断言不变） |
| 改后语义抽查 | "甲同学 2.12 平台草稿区现有 3 题 AI 答案"／"缺课：#2 乙同学（运行时会被跳过）"／"`school=示范学院`、`name=甲同学 / 乙同学`" 均通顺 |

## 六、踩坑

1. **`write_text(newline="\n")` 会把 CRLF 文件整篇重写** → diff 会从 2 行变成几百行。
   改用 bytes 读写（`read_bytes` → `replace` → `write_bytes`），行尾与 BOM 一个字节都不动
   （实测 `git diff --stat` 每个文件都只有 2 行变化）。
2. **PowerShell 里 `python -c "…中文…"` 的引号会被吃掉**（`like 'pace.%'` 报
   `unterminated string literal`）→ 临时脚本写进 `data/debug/` 再跑，别跟引号较劲。
3. `findstr /s /c:"…" *.md` 在 PowerShell 下没递归到 → 用 Grep 工具（ripgrep）或 python 扫。
4. **门禁命中提示不能打印原值**：终端输出常被粘进对话/日志，打出来等于又泄露一次 →
   改成"命中本机敏感词（N 字，原值不显示）"。

## 七、未做 / 残留

1. **历史提交里的原名未清**（需重写历史 + force push，不做）。
2. 词表只收了这次发现的 3 条；以后再看到新的真名，往 `data/redact_terms.local.txt` 追加一行即可。
3. 台账里提到的"与新站学校代号库合并做"暂缓——新站还没开工。
4. 运行时数据（`data/xxt.db` 里平台返回的真名）不涉仓库，不处理。

## 八、交接块

- **进行中**：无，本项已闭环。
- **下一步第一个动作**：无待推。用户 2026-10-28「推吧」→ `push_check` 全绿
  （含新增的 `[3/5] 脱敏扫描`）→ **已推送 `8dea462..fdf5f49`，待推 0**。
- **阻塞点**：无。
