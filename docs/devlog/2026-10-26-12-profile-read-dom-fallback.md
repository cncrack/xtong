# 2026-10-26-12 补画像实跑：发现 stu-base-info 抓取点已失效 → 改读同页 DOM

## 本次目标

用户说"补画像"（= 按约定：每账号只登一次、一次抓全、两号串行不并发、带随机间隔模拟人）。目标是把 `school/dept/cls` 等画像字段补进 `account_profiles`，让批次选课能按学校区分同名课。

## 最终结果

- ✅ **学校/姓名 已补齐**（两号）：`school=泉州信息工程学院`、`name=林鑫阳 / 王泽轩`，`source=platform`
- ⚠ **院系 / 班级 / 学号 仍为空**：个人空间页**不提供**这三项（页面无个人资料入口链接），按"绝不猜"原则不填
- 🐞 **发现并修掉一个从未被实测过的功能缺陷**（详见下）

## 关键发现：`stu-base-info` 抓取点根本不存在了

第一次实跑两号均失败：`页面没有返回 stu-base-info（可能改版或未登录）`。

只读诊断（**一次**访问 #1，同时抓响应列表 + DOM，避免反复试探）：

| 观察到 | 结论 |
|---|---|
| `final_url=https://i.chaoxing.com/base?t=…`、`title=个人空间` | **会话有效**，不是登录失效 |
| 正文含 `泉州信息工程学院 / 林鑫阳 / 课程 / 收件箱…` | 已登录，学校名就在页面上 |
| 全部非静态响应 51 条，**无一条含 `stu-base-info`** | 该接口在新版个人空间（nervous/newSpace 主题）**已不发** |
| `.header` innerText = `泉州信息工程学院⏎输入邀请码⏎林鑫阳`；`.user` = `林鑫阳` | 可稳定取得 **学校 + 姓名** |
| 命中「专业/班级/学号」关键字 0 次；`<a>` 链接 0 个 | 该页**没有**院系/班级/学号，也没有个人资料入口 |

所以：会话 32 上线的"平台画像读取"**从上线起就只对 `stu-base-info` 生效，而这个接口现在不存在** → 功能实际不可用。这与"该功能标注为'尚未实况验证'"完全对得上。

## 修法（`src/xxt/adapters/chaoxing/profile_read.py`）

1. **接口优先、DOM 兜底**：仍先拦截 `stu-base-info`（老版本/其它入口可能还有）；没有就读**同一页 DOM** `.header` / `.user` —— **仍然是零额外请求、一次访问**。
2. 新增纯函数并加测试：
   - `_looks_like_school()`：4~30 字、无数字、院校后缀结尾（学院/大学/学校/分校/校区/职院/中学）——宁漏不错；
   - `_parse_space_dom()`：从 `.header` 多行里挑出唯一像学校名的一行 + `.user` 取姓名（长度/数字校验）。
3. 返回值新增 `source`（`stu-base-info` / `space-dom`）与 `fields`（DOM 兜底时为 `dom:.header` / `dom:.user`），面板 toast 会显示，便于日后核对来源。
4. 错误文案改为"页面既没有 stu-base-info，也没读到学校/姓名"，不再把 DOM 兜底成功的场景误报为失败。

## 新增：`scripts/fill_profiles.py`（可复用，非临时脚本）

串行补画像工具，纪律固化进代码：

- 每号**只访问一次**（走与面板按钮完全相同的后端路径 `POST /api/accounts/{id}/profile_from_platform`）
- **串行**：跑完一个才跑下一个；账号之间 `sleep` 随机 45~90s（`--min-gap/--max-gap` 可调）
- **不登录**：只复用已有 `state.json`；会话失效如实报错，绝不自动重登
- 只回显 `school/dept/cls`（学号等个人信息不打印）
- `--dry-run` 先看要跑哪些号；`--accounts 1 2` 指定；`--api` 换地址

## 实跑记录（串行，含间隔）

```
[1/2] #1 default → ✔ 回填 name/school｜school=泉州信息工程学院
... 间隔 88s 后跑下一个
[2/2] #2 小号1 → ✔ 回填 name/school｜school=泉州信息工程学院
```

DB 复核：`account_profiles` 两行 `name/school` 已填、`source=platform`；`/api/overview` 的 `profile.school` 已下发。

## 验证

- `pytest` **111 passed**（新增 `tests/test_profile_read.py` 7 条，合成数据）
- 面板重启无 traceback；headless `diag_ui9.py`：
  - 课程下拉每项 `《课名》 · 泉州信息工程学院 · 你选的 2/2 有`（不再显示"学校未填"）
  - 选择账号 → 学校筛选 = `全部学校 | 泉州信息工程学院 | （未填学校）`；表格学校列已显示真名
  - **pageerror=0**

## 遗留

- **院系/班级/学号**：该页不给；要么人工在「📈 统计 → 画像设置」填，要么后续另找页面（会多一次访问，需权衡）
- 账号间访问节奏已按 45~90s 随机；若之后账号变多，`--min-gap` 建议一并调大

## 改动的文件清单

- `src/xxt/adapters/chaoxing/profile_read.py`（DOM 兜底 + `_looks_like_school` / `_parse_space_dom` + `source`/`fields` 来源标注）
- `scripts/fill_profiles.py`（新增：串行补画像工具）
- `tests/test_profile_read.py`（新增 7 条）
- `STATUS.md`、`docs/PLAN.md`（v0.17）、本 devlog
- 临时诊断（`data/` 已 gitignore）：`data/debug/probe_profile.py`、`probe_profile2.py`、`diag_ui9.py`

## 交接块

- **进行中**：✅ 无。画像 `name/school` 两号已补；院系/班级/学号平台该页无，需人工或另找入口
- **下一步第一个动作**：用户 `Ctrl+F5` →「📚 批次 → ＋新建批次」→ 课程下拉应显示「泉州信息工程学院」；「＋选择账号」的学校筛选里也应有该学校
- **阻塞点**：无
