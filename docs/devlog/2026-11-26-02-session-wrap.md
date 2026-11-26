# 2026-11-26-02 上午收工整理（AI Key 解耦）

> 用户 2026-11-26：「整理会话，收工，下午继续」。
> 本篇是**状态收口**。解耦细节见 `2026-11-26-01`。

---

## 一、上午做了什么

**核心：AI Key 真正解耦**（用户连续追问后定案）。

### 起因

用户问：
- 「两个项目不同，为何用到超星侧的代码/数据？」
- 「可以共用，但不要有互相影响」
- 「调用 AI 模型这个模块不能共用吗？」
- **「你改代码前没有查先前的项目日记和约定吗？」**

### 我犯的错

| # | 错误 | 后果 |
|---|---|---|
| 1 | 没读 AGENTS.md/devlog 就动手 | 被用户当场指出 |
| 2 | 凭想象判断架构（先说独立仓库、又说共用一个库），**没跑 `git common-dir` 查证** | 结论反复横跳 |
| 3 | 昨天给共享 `LLMClient` 加 `api_key` 参数**绕过**问题 | 改了共用文件，将来合并冲突 |

### 查证到的事实

```
E:\xxt          common-dir = .git            (主仓库, main)
E:\xxt-gxks     common-dir = E:/xxt/.git     (worktree, feat/gxks)
```

**是同一个仓库的两个工作目录**，改 `ai/` 共用路径会合并冲突、两平台互相影响。

### 正确解法（已实现）

**共用文件一个字不改，用继承换掉 Key 来源：**

| 文件 | 改动 |
|---|---|
| `src/xxt/ai/llm.py` | **恢复成 main 原样**，`git diff main -- ...` 为空 |
| `src/xxt/adapters/gxks/llm.py`（新） | `GxksLLMClient(LLMClient)` 继承共用 `chat()`，只覆写 `_api_key()` |
| `scripts/gxks_work.py` | 改用 `make_gxks_llm(settings)` |

**效果**：
- 调 AI 的逻辑**还是共用一份**（继承），没有代码重复
- Key 来源各自管，谁也不知道对方的库
- 共用文件零差异 → 合并零冲突
- 超星代码一行没动

### 守护测试

- `test_shared_llm_unchanged_from_main` —— 共用文件必须与 main 零差异
- `test_gxks_client_never_reads_xxt_db` —— gxks 客户端代码不读别的库
- `test_gxks_boundaries` —— gxks 适配器不许出现 `chaoxing` 字样

### 又踩的坑

1. PowerShell `Out-File -Encoding UTF8` 给新 `.py` 加了 BOM → AST 报 `U+FEFF`（已删）
2. devlog 里写假 Key 字面量样例触发敏感扫描（已改成描述）
3. `test_panel.py::test_auto_stop_never_kills_reused_pid` 是超星侧顺序敏感测试，全量偶发失败、重跑即过（未改）

---

## 二、验证与现场

| 项 | 结果 |
|---|---|
| 共用文件与 main 差异 | **空** |
| 全量 `pytest` | **1443 全绿** |
| 门禁 | **PASS** |
| 工作区 | **干净** |
| HEAD | `022cfdf` |
| 未推送 | **9 个** |
| 面板 / Chrome | 都停着 |
| 临时脚本 | 已清 |

---

## 三、下午第一件事：测 G-6 人工复核交卷

**唯一还没实机验证的功能。**

1. 在 `config/settings.local.toml` 把 `[gxks] work_review` 改成 `true`
2. **重启面板**（配置启动时读）
3. 挑一节 →「做练习」→ 脚本填题
4. **核对逐题表**（每题有来源、置信度）
5. 点面板「**确认交卷并入库**」
6. **到浏览器窗口手动点「交卷」**
7. 看终端是否出现 `[5/6]` / `[6/6]` + `入库 N 题`
8. 刷新节列表 → 该节显示「已做 1 次」+「可重做」

### 两个关键机制（回想）

- **脚本先断开 CDP**：连着会吞掉你手动交卷的 confirm（Q31）
- **`raw_rows`/`type_map` 存信号文件**：重连后拿不到答题帧

### 未验证的风险

「重连 CDP 后等判分页」这段没法离线验。判分页**解析**已用 10-30 真实快照跑通（3 题全解析出 `site` 标准答案），但「交卷后重连能否拿到判分页」要实机看。若报 `[WARN]`，按原文贴回。

---

## 四、待办（STATUS §4）

| 项 | 状态 |
|---|---|
| **G-5 / G-6** 练习回填 + 人工复核交卷 | **下午第一件事** |
| **G-27** AI Key 解耦 | ✅ 已完成 |
| **P-1** 甲同学 3 节是否真未解锁 | 等你看平台 |
| **G-9 第 2 级 guarded** | 等题库喂饱 |
| **T1/Q30** 平台切课 | 等第二门课样本 |

---

## 五、本次改动的文件

- `src/xxt/adapters/gxks/llm.py`（新）
- `src/xxt/ai/llm.py`（还原成 main 原样）
- `scripts/gxks_work.py`（用 gxks 客户端）
- `tests/test_gxks_llm_key_source.py`（重写 7 条）
- `docs/devlog/2026-11-26-01-key-decoupling.md`（新）
- `docs/devlog/2026-11-25-01-work-review-flow.md`（去掉敏感样例）
- `STATUS.md`

## 交接块

- **进行中**：无（1443 全绿、门禁 PASS、工作区干净）
- **下一步第一个动作**：开 `work_review=true` → 重启面板 → 做一次练习 → 核对 → 确认交卷 → 浏览器手动交卷 → 看是否入库
- **阻塞点**：无技术阻塞；G-6 待实机验证
