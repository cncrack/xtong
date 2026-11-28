# 2026-11-28-09 自动检测漏 ExamMessage + 日志区真正变高 + 审计规则入约定

> 用户 2026-11-28 三问：
> ① 「① 浏览器点「交卷」→ 我自动发现并入库；没有发现自动并入库呀」
> ② 「运行日志这个高度没有变化，我说提这个黑色的高度呀」（附截图）
> ③ 「为什么你每次改完不再审查一下是否完整，每次都要测一下、改一下，
>    这个问题我们前面是不是已经碰到过」

---

## 一、自动检测漏了 ExamMessage（同一个坑，我第二次踩）

### 事实

`git log --grep=ExamMessage` 显示**2026-11-27 已修过一次**：

```
3f049a3 fix: 识别 ExamMessage 交卷成功页，自己拼出判分页 URL
```

而 2026-11-28 我写「自动检测」时**又只认 `viewexam`**：

```python
if "viewexam.aspx" not in _low:
    continue        # ← ExamMessage 被跳过
```

**后果**：用户交卷成功（停在 `ExamMessage.aspx`），脚本检测不到，
白等 10 分钟。1.9 那次就是这样。

### 修法

`_find_review()` 现在两种页面都认：

| 页面 | 处理 |
|---|---|
| `viewExam.aspx` | 直接用 |
| `ExamMessage.aspx` | 从 `Tk_Cl_Id` + `Clerk_Kscj_Id` **拼出**判分页 URL（与主流程第 ④ 步同一套，不写第二套） |

顺带修一个会崩的 bug：`MAIN_PAGE` 原在 L413 才 import，我在 L335 就用 →
`NameError`。已把 import 提前。

### 1.9 数据已补入库

用 ExamMessage 页参数拼判分页抓取：

```
#1 歼-35A是新一代隐身多用途歼击机。        标准=对 考生=对 ✅ 33
#2 歼-10战斗机的作战半径大约在1200公里。     标准=对 考生=对 ✅ 34
#3 歼-16被军迷称为"炸弹卡车"。               标准=对 考生=对 ✅ 33
paper_id=12，100 分
```

---

## 二、日志区「黑色区域」没变高——我改错了对象

用户截图指出：他要调高的是**黑色区域**（`<pre>` 里那些日志行），
而我上一轮只把外层 `#runlog` 从 52vh 调到 72vh。

**真正卡高度的是全局样式**：

```css
pre{background:#0f1115; ...; max-height:150px}   /* ← 约 6 行，黑色区域 */
```

### 修法

用 `#runlog pre` 精确覆盖，**只影响运行日志这一块，不动别处的 `<pre>`**：

```css
#runlog pre{...; max-height:none; overflow:visible}
```

外层 `#runlog`（72vh/900px）负责限高与滚动。

> 教训：改样式前要看**实际是哪个元素在限高**，不能只看容器。

---

## 三、把审计规则写进 AGENTS.md（用户要求「可写入到规则约定中」）

新增章节 **「改代码前的『同类点审计』」**：

### 改前三件事
1. `git log --oneline --all --grep="<关键词>"` 查这个坑修过没
   （关键词取**平台细节**：页面名/字段名/接口名）
2. grep 扫出**所有同类判断点**，列清单（文件+行号），不能只看要改的那一处
3. 判断口径必须与既有代码对齐；既有实现处理过的分支，新代码也要处理，
   或写清为什么不需要

### 改后三件事
1. 逐条对照清单确认（贴清单打勾，不凭记忆）
2. 自问「这个功能失效，现有测试能抓到吗」，抓不到就补
3. 跑全量 pytest

### 核心：修完一个易重犯的坑，要**加门禁测试把它钉死**
只在 commit message 写「修好了」而不留机制 = 下次还会犯。

---

## 四、门禁测试：判分页判断点必须同时认两种页面

新增 `tests/test_gxks_review_url_detection.py`（3 条）：

- 扫描 `gxks_work.py` 里**每一处**判断 `viewexam.aspx` 的代码行，
  要求同行或逻辑上下文里出现 `exammessage`；
- 例外必须显式登记并写理由，且**总数 ≤4**（防用白名单绕过审计）；
- 校验 ExamMessage 分支确实能拼出判分页。

**它立刻抓到了我自己漏的那处**：

```
AssertionError: 以下判分页判断点没有同时处理 ExamMessage.aspx
    L339: if "viewexam.aspx" not in _low:
```

（L339 经审后属合理例外——examessage 分支在它之前已 return，已登记。）

---

## 五、测试

`pytest` 1512 → **1515 全绿**（+3）。

## 六、改动的文件

- `scripts/gxks_work.py`（`_find_review` 支持 ExamMessage + MAIN_PAGE 导入位置）
- `src/xxt/panel_gxks/page.py`（`#runlog pre{max-height:none}`）
- `tests/test_gxks_review_url_detection.py`（新增门禁）
- `AGENTS.md`（新增「同类点审计」章节）

## 交接块

- **进行中**：无
- **下一步第一个动作**：重启面板 → 做一节练习，验证
  ① 交卷后（停在 ExamMessage）能自动入库 ② 日志区黑色部分变高
- **阻塞点**：无
