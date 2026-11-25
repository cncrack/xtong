# 2026-11-25-02 早收工整理 + 下次首要任务：解耦超星数据库

> 用户 2026-11-25：「两个项目不同，为何会用到超星侧的代码或者数据呢，请排查，
> 这个列为下次开始处理的首要任务，以后不要有类似的情况发生」。

---

## 一、排查结果：分两类，性质完全不同

### ❌ 真正的越界（必修）

**`src/xxt/ai/llm.py` 的 `_load_key_from_db()` 硬编码读超星数据库。**

```python
def _load_key_from_db(data_dir: Path) -> str | None:
    """从 data/xxt.db 解密 llm.key_enc；无则 None。"""
    db = data_dir / "xxt.db"
    ...
    "SELECT value FROM app_config WHERE key='llm.key_enc'"
```

问题：
1. `xxt.db` 是**超星项目的业务库**，gxks 有自己的 `gxks_questions.db`
2. `app_config` 是超星的表，gxks 库里**根本没有**（只有 `meta`）
3. 昨天我加的 `api_key` 参数只是**绕过**它，**根子还在** —— `llm.py` 依然
   默认读超星库，任何新调用方不传 `api_key` 就会踩同一个坑

### ✅ 可接受的共享（不必改）

`src/xxt/core/` 是**两个平台共用的基础设施库**，不是超星私有资产：

| 引用点 | 模块 | 性质 |
|---|---|---|
| `gxks_ai_config.py` | `core.crypto`（AES-GCM 加解密） | 通用加密工具 |
| `locks.py` / `chrome.py` / `panel_gxks/app.py` | `core.runmark`（进程标记防重复） | 通用进程锁 |
| `browser.py` | `core.stealth` / `core.browser` | 通用反检测参数 |

`gxks_ai_config.py` 的注释本身就写明了边界：
> 「超星面板 `panel/app.py` 的 provider 实现连着 `data/xxt.db` 的 `app_config` 表，
> 而 **gxks 库里根本没有 `app_config` 表**（只有 `meta`）」

**所以：gxks 自己把 Key 存 `meta` 表是对的，错的是 `llm.py` 读 Key 时默认跑去读超星库。**

---

## 二、下次首要任务：把 Key 读取解耦

### 目标

`LLMClient` 不该知道任何项目的数据库路径。Key 的来源应由**调用方注入**。

### 建议做法

| 步骤 | 内容 |
|---|---|
| 1 | `LLMClient.__init__` 的 `data_dir` 改为**必传 `api_key`**（或保留 data_dir 但不再默认读 xxt.db） |
| 2 | 把 `_load_key_from_db` 从 `ai/llm.py` 移到**超星侧**（`panel/app.py` 或新建 `ai/legacy.py`） |
| 3 | 超星的调用方自己传 Key；gxks 继续用 `gxks_ai_config.load_key_for()` 传 Key |
| 4 | 加边界测试：`ai/llm.py` 里**不许出现** `xxt.db` / `app_config` 字面量 |

### 为什么这条重要

这次 bug 的表象是「配了 Key 说没配」，根因是**架构上让通用模块知道了某个项目的私有库**。
只要 `llm.py` 还留着那个默认路径，**任何新平台接入都会再踩一次**。

---

## 三、本次已完成（G-6 人工复核交卷 + Key 临时修复）

详见 [2026-11-25-01](2026-11-25-01-work-review-flow.md)。摘要：

| 项 | 结果 |
|---|---|
| `LLMClient` 加 `api_key` 参数（临时绕过） | ✅ |
| `_make_llm` 从 gxks 自己的库取 Key | ✅ |
| G-6 人工复核交卷（`work_review` 开关 + 确认按钮 + 回收入库） | ✅ |
| 全量 `pytest` | **1440 全绿** |
| 未推送 | **5 个** |

---

## 四、现场状态

| 项 | 值 |
|---|---|
| 工作区 | 干净 |
| HEAD | `7239ba9` |
| 未推送 | **5 个** |
| 面板 / Chrome | 都停着 |

### 下午第一件事

1. **先修 `llm.py` 解耦**（用户定的首要任务）
2. 再测 G-6（开 `work_review=true` → 跑练习 → 确认交卷 → 看入库）

---

## 改动的文件

本轮仅文档：
- 本 devlog
- `STATUS.md`（头部 + §3 + §4 新增 G-27 待办）

## 交接块

- **进行中**：无（1440 全绿、工作区干净）
- **下一步第一个动作**：**把 `ai/llm.py` 里读 `xxt.db` 的逻辑移走**，让 `LLMClient` 只接受注入的 Key；加边界测试禁止 `xxt.db`/`app_config` 字面量出现在该文件
- **阻塞点**：无
