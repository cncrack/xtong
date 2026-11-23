# 停掉正在运行的面板（含整棵进程树），2026-11-23。
#
# 为什么要这个脚本：`reload=True` 在 Windows 上是多层进程树
# （powershell → xxt.exe → python → reloader → worker），
# 按 Ctrl+C 只打断最近一层，reloader 会再 spawn 一个 worker，
# 于是要按多次、还刷一堆 KeyboardInterrupt/CancelledError 红字。
# `taskkill /T` 能一次连带整棵树停干净。
#
# 用法：
#     .\scripts\stop_panel.bat        （双击也行）
# 或：
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop_panel.ps1
#
# 编码：必须带 BOM（Windows PowerShell 5.1 按 GBK 读无 BOM 的 .ps1）。

$ErrorActionPreference = "Continue"

Write-Output "=== 停止 gxks 面板 ==="

# 1) 优先按"谁在监听 8301"找 —— 最准（不依赖进程名/命令行）
$targets = @()
$conn = Get-NetTCPConnection -LocalPort 8301 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
if ($conn -and $conn.OwningProcess -gt 0) {
    $targets += [int]$conn.OwningProcess
    Write-Output "  端口 8301 由 PID $($conn.OwningProcess) 监听"
}

# 2) 兜底：按命令行找（xxt.exe / python 跑 gxks-panel 的）
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -and $_.CommandLine -like '*gxks-panel*' }
foreach ($p in $procs) {
    if ($targets -notcontains [int]$p.ProcessId) {
        $targets += [int]$p.ProcessId
    }
}

if ($targets.Count -eq 0) {
    Write-Output "  没有找到正在运行的面板（8301 未监听）。可能已经停了。"
    exit 0
}

foreach ($pid in $targets) {
    Write-Output "  正在停止 PID $pid（含子进程）..."
    & taskkill /PID $pid /T /F 2>&1 | ForEach-Object { "    $_" }
}

Start-Sleep -Milliseconds 800
$still = Get-NetTCPConnection -LocalPort 8301 -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Output "  ⚠ 8301 仍在监听（PID $($still.OwningProcess)），请再跑一次本脚本"
    exit 1
} else {
    Write-Output "  OK：8301 已释放，面板已停止。"
    exit 0
}
