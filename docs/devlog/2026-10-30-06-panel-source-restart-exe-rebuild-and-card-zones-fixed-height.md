# 2026-10-30-06 面板源码重启 + exe 重建核验 + 卡片分区定高（消除跨卡等高拉伸）

## 一、本次目标与最终结果

用户命令两件事：**走"源码重启" + 重构建 exe**，并继续追"账号卡片底部折叠区跨卡联动"。全部完成：

1. **源码重启 8300**：停旧进程（系统 python PID 22828），改用 `.venv` 源码后台重起，实测下发含 `a.id+':r'` 的新码，`panel_restart.err` 无异常。全程未碰 8301《第二平台控制台》(PID 22312)。
2. **重构建 exe**：`.venv\Scripts\python.exe scripts\build_exe.py --onefile` 跑完，`dist\xxt.exe` 更新（约 63MB）。用 **PyInstaller `Analysis-00.toc` + 构建副本** 实证：`xxt.panel.app` 收集自 `E:\xxt\build\app\xxt\panel\app.py`（即从 `E:\xxt\src` 新树拷入的副本），含 `:r`/`:d`/`auto-corrected`/`auto_grind`/`setAutoGrind` 全部新标记，**无一条模块来自旧树** → 本次 exe 打进的是新码，正确。
3. **跨卡联动根因（最终定性，历经三次修正）**：
   - 初判"旧 exe / 浏览器缓存" → 用户在新码 8300 实测仍反馈联动；
   - 控制台实测 `EXPANDED` 键：点 `1:r` 只写 `{"1:r":true}`，`1:d/2:r/2:d` 的 `display` 全 `none` → **状态联动证伪**；
   - 量化脚本测两卡 `offsetTop/offsetHeight`：点击前卡0/卡1 均 `top=224 高=386`，点击后卡0 `高→859`、**卡1 也 `高→859` 但展开区仍 `(无)`** → **真因 = CSS Grid 默认 `align-items:stretch` 把同行卡片拉到等高**（卡1 盒子被撑高、内容没展开，看着像联动）。

## 二、关键决策

- **卡片分区定高（方案 A，纯 CSS/结构，不动折叠逻辑）**：
  1. `.grid` 加 `align-items:start` → 解耦，展开一张不撑高另一张（消除"联动"）；
  2. `.more`（其他/已学完）加 `max-height:340px;overflow-y:auto` → 防无限变长；
  3. 新增 `.cap`（340 封顶 + 纵滚）并把**自动刷队列**包进 `<div class="cap">` → 队列同样防变长；
  4. `.chips` 预留 **2 行高**（`height:52px;overflow-y:auto;align-content:flex-start`）→ 徽章条恒定 2 行，多则上下滚（用户选 A）；
  5. `.head .names` 加 `min-height:66px` → 姓名区恒定 3 行，有无 🎓 学号行都齐平。
- **放弃"ops 并入头部右侧"**：`app.py:312` 注释记载 2026-10-27 曾专门把 ops 挪到独立行以修"■ 停止被卡片右边界裁掉"（`overflow:hidden` + 按钮过多）。并入会重触发裁切 bug，不划算；改用 names/chips 预留高度达到同样"等高"目标，零裁切风险。
- **环境隐患登记（未动）**：本机存在旧孪生树 `E:\code\xxt`（10/25、无 `:r`），`.venv\pyvenv.cfg` 显示 venv 当初建于 `E:\code\xxt`，PyInstaller 经 `.exe` 垫片会报 `E:\code\xxt\.venv` 前缀。本次靠 `--paths build/app` 让新树副本胜出、结果正确，但下次未必稳。

## 三、改动文件清单

- `src/xxt/panel/app.py`：5 处 CSS（`.grid`/`.chips`/`.head .names`/`.more`/新增 `.cap`）+ 队列渲染包 `<div class="cap">`（`renderAccounts` 内，约 856/294/336/305/365 行区）。**未改折叠逻辑与后端**。
- `tests/test_panel.py`：本会话无新增用例（纯样式），回归 **53 passed**；全量 **316 passed**。
- `docs/PLAN.md`：变更记录追加 v0.20.5。
- `STATUS.md`：顶部"最近更新"轻注本次卡片分区定高 + exe 重建核验。
- 一次性探针脚本 `data/debug/_panel_8399.py`/`_which_xxt.py`/`_probe_exe.py` 已删除（`data/` 不入库）。

## 四、交接块

- **进行中**：本会话改动全部**未提交**（等"提交"口令随会话 72 其余批次一并归档）。
- **下一步第一个动作**：用户硬刷新 8300 验收——两卡顶部应基本等高、展开只在固定框内滚；如仍觉得 ops 行左侧空档碍眼，再单独想办法（不动按钮归属）。`52px/66px/340px` 均为可调值。
- **阻塞点**：无。exe 内容静态字节搜索不可信（onefile 压缩），如需终极实证可临时把 `dist\xxt.exe` 跑在空闲端口 curl 其下发 JS（需批准临时占端口）。
- **用户下一步操作清单**：① Ctrl+F5 刷新 8300 看等高效果；② 满意则下"提交"归档；③ 若要根治环境隐患，批准重指 editable 安装 + 旧树改名 `.old`（可回退、不删除）。
