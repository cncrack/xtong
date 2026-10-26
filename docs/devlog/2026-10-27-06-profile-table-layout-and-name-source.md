# 2026-10-27-06 画像设置表：列宽收紧 + 姓名/昵称分离 + 未保存提示 + 来源不误改

## 一、目标

用户看截图提两问：① 画像表列宽能否按内容自动调整（有些太宽）② 这块是干什么的、有什么缺陷、改了有什么好处。
确认「连可选一起」后落地 5 项（3 必做 + 2 可选）。

## 二、改动清单

### 1. 列宽按内容收紧（用户点名）

原状：`.ptable{width:100%}` + `.pf{width:100%}` + `<input>` 默认 `size=20`（≈170px）→ 9 个输入框把 1440px 容器**强行铺满**，
`年级`/`学期` 这种 4 字内容也占 130px+，右侧大片空白。

现改（只在画像表作用域生效，`pick` 选择器表不受影响）：

```css
#profBody .ptable{width:max-content;max-width:100%;min-width:0}   /* 不再强行铺满 */
#profBody .ptable th,#profBody .ptable td{padding:.26rem .35rem}  /* 行高/内边距一起收 */
#profBody .ptable td:nth-child(3) .pf{width:8em}   /* 姓名 */  (5)学号 9em  (6)年级/(8)学期 6em
#profBody .ptable td:nth-child(7) .pf{width:9em}   /* 专业 */  (9)学校 11em (10)院系/(11)班级 8em
```

### 2. 修 P1：平台昵称不再冒充“手填姓名”

前端 `inp(id,'n', p.display_name, …)` 的 value 取 `display_name`（= 手填姓名 **or** 平台昵称）→
姓名框预填昵称，点一次「保存」就把昵称写进 `account_profiles.name`、来源变「手动」，
此后「平台读取」按“只补空”永远跳过姓名。现改为 value 取 `p.name`，昵称只做 placeholder：
`留空用昵称：林鑫阳`。

### 3. 修 P2：文案与实现对齐

顶部与底部说明原写“姓名/学号/年级/专业/学期参与统计分组”，而 `stats.DIMENSIONS` 只有
**年级/专业/学期/课程/账号**。改为：`年级/专业/学期参与统计分组；姓名/学号用于识别与搜索；…`
底部补“姓名留空时卡片显示平台昵称（不会把昵称写进姓名）”。

### 4. 可选 A：未保存提示

任一输入框 `oninput` → 操作列出现橙字「● 未保存」；保存成功后清除。

### 5. 可选 B：没有改动就不动来源

`saveProfile` 先与 `PROFILES` 原值逐字段比较；全等则 POST `source: null`。
后端 `stats.set_profile(source: str | None = "manual")`，upsert 改
`source=COALESCE(excluded.source, account_profiles.source)` —— 传 null 保留原来源。
（此前点一次“保存”即使一字未改也会把「平台」抹成「手动」，徽标失去意义。）

## 三、实测（真面板 headless，零平台访问，`data/debug/diag_prof_table.py`）

```
表格宽 1178px / 容器 1376px（横向滚动=False）      ← 原为铺满 1440px
列宽：账号:35、备注:52、姓名:106、昵称:47、学号:118、年级:82、专业:118、
      学期:82、学校:141、院系:106、班级:106、来源:35、操作:150
改动后提示 = '● 未保存'
无改动保存 → toast='✔ 无改动（来源保持不变）'｜来源 'platform'→'platform'｜提示已清=True
pageerror = 0
```

截图：`data/debug/prof_after.png`。`pytest` **140 passed**（+5，`tests/test_profiles.py`）。

## 四、实测暴露的新问题（用户选 **C**，已实施见 §七）

库里两号 `account_profiles.name` **都等于平台昵称**（`#1 林鑫阳 / #2 王泽轩`，`source='platform'`）——
根源不是前端，而是「⬇ 平台读取」把平台返回的 `name` 当真实姓名回填，而超星该字段给的就是**昵称**
（`accounts.platform_username` 已有同名值）。后果：姓名列永远显示昵称、用户以为已填真名。

建议修法（三选一，成本递增）：

| 方案 | 内容 | 成本 |
|---|---|---|
| A 只清数据 | 把 `name == platform_username` 的行清空 `name`（保留 school 等），UI 回归“未填” | 一次性 SQL，可逆 |
| B 适配层不再回填 name | `merge_platform_profile` / `profile_from_platform` 忽略 `name`，只补 school 等（宁漏不错） | 小，需测试 |
| **C = A + B** | 先堵住再清历史（推荐） | 小 |

## 五、文件清单

| 文件 | 变化 |
|---|---|
| `src/xxt/panel/app.py` | 画像表 CSS 作用域（宽度/内边距/字段 em/`.dirty`）；姓名 value→`p.name` + 昵称 placeholder；`pfDirty()`；`saveProfile` 逐字段比较 + `source:null` + 清提示；顶部/底部文案；`/api/profiles` 透传 `source` |
| `src/xxt/core/stats.py` | `set_profile(source: str | None)`；upsert 用 `COALESCE` 保留原来源 |
| `tests/test_profiles.py` | 新增 5 条（name 不回退昵称 / source=None 保留 / 新行空来源 / 真改才 manual / 平台读取仍只补空） |
| `data/debug/diag_prof_table.py`、`shot_prof.py`、`q_prof_check.py` | 实测与截图脚本（不入库） |

**部署文件**：无变动（纯前端样式 + 核心层参数，无新依赖/环境变量）。

## 六、交接块（初稿；后续实施见 §七）

- **进行中**：无。5 项已落地并实测，面板已重启（新代码）。
- **下一步第一个动作**：用户 `Ctrl+F5` →「📈 统计 → 画像设置」看列宽与「● 未保存」提示是否合意。
- **阻塞点**：第四节“平台昵称被当姓名回填（历史数据已污染）”需用户选 A/B/C 后才动手。
- **备注**：`#profBody` 作用域外的表格（选择账号弹窗 `.ptable.pick`）本次未动，列宽规则保持原样。

## 七、C 方案实施（同日追加：A 清数据 + B 堵源头）

用户选「按 C 处理」。

### B：适配层不再把昵称当姓名

`core/stats.merge_platform_profile` 先读 `accounts.platform_username`，若平台返回的 `name`
与昵称**完全相同** → 不写 `name`，记入新键 `nickname_only`（宁漏不错：这一路本来分不清真名与昵称）；
与昵称不同则仍当真名回填（不把有效值丢掉）。
返回契约：`{ok, filled, skipped, nickname_only, error?, profile?}`。
面板 toast 增「；平台只给了昵称，姓名未填（请手填真名）」，`profile-read` 事件日志增「，姓名=昵称已忽略」。

### A：清历史污染（可逆）

`data/debug/q_clean_nickname.py`：只动 `name == platform_username` 的行，先备份到
`data/debug/profiles_backup_20261027-025421.json` 再置 `name=NULL`（`school`/`source` 不动）：

```
画像行 2 条，其中“姓名==平台昵称” 2 条：
  #1 name='林鑫阳' source='platform'
  #2 name='王泽轩' source='platform'
→ 已置空；#1/#2 昵称分别为 林鑫阳/王泽轩，学校泉州信息工程学院，source 仍 platform
```

### 复测（面板重启后）

```
pf_n_1: 宽 95px value='' ph='留空用昵称'      ← 姓名回归“未填”，昵称并列不复写
pf_g_1 value='2026' / pf_sc_1 value='泉州信息工程学院'（手填与平台值均未动）
无改动保存 → 来源 'platform'→'platform'；pageerror=0；pytest 142 passed（+2）
```

新增测试：`test_platform_name_equal_to_nickname_is_ignored`、`test_platform_name_differs_from_nickname_is_filled`。

### 当前状态

- 姓名列为空、placeholder「留空用昵称」，邻近「昵称」列已显示林鑫阳/王泽轩 → 需用户**手填真名**。
- 下次点「⬇ 平台读取」：昵称会被 `nickname_only` 拦下（不再污染），学校仍会回填。
- 部署文件仍无变动。

## 八、卡片标题「昵称」标签（同日追加）

用户追问“小卡片区标题显示的是名字还是昵称”。答：`display_name = profile.name or platform_username`，
手填姓名为空时回退到昵称，但**无任何标注**，易误以为已填姓名。

改法 A（用户选定）：标题走昵称回退时后缀一个灰边胶囊字「昵称」，手填姓名后自动消失、副行改为「· 平台昵称 XXX」。

```js
<div class="names"><div class="who">${hl(...)}${(a.platform_username&&!(a.profile&&a.profile.name))
  ?'<span class="nicktag" title="当前显示的是平台昵称；在「📈 统计 → 画像设置」手填姓名后自动换成姓名">昵称</span>':''}</div>
```

CSS：`.who .nicktag{font-size:.6rem;border:1px solid var(--line);border-radius:6px;padding:0 .25rem;…}`。

实测（`data/debug/shot_card.py`，截图 `card_nicktag.png`）：

```
卡片1: 标题='林鑫阳昵称' 有昵称标签=True 副行='#1 default · 优先级 1'
卡片2: 标题='王泽轩昵称' 有昵称标签=True 副行='#2 小号1 · 优先级 2'
pageerror = 0
```
