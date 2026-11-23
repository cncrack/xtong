# 一次性为**所有账号**注册交互式计划任务（2026-11-23 方案 B）。
#
# 为什么需要手工跑一次（用户 2026-11-23 实测）：
# `Register-ScheduledTask` 创建「以 Administrator 运行」的任务时**需要提权**，
# 而面板是普通权限进程 → `FAIL: 拒绝访问`。
# 自动注册只在**权限足够**时生效（例如面板本身以管理员启动），
# 否则用本脚本手工补一次即可，之后一直有效。
#
# 用法（**在管理员 PowerShell 窗口**执行一次）：
#     cd E:\xxt-gxks
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_all_chrome_tasks.ps1
#
# 编码：必须带 BOM（Windows PowerShell 5.1 按 GBK 读无 BOM 的 .ps1）。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$single = Join-Path $root "scripts\register_chrome_task.ps1"

# 从账号表读所有 label（不读凭据，只读代号）
$acctFile = Join-Path $root "data\accounts.local.json"
$labels = @()
if (Test-Path $acctFile) {
    try {
        $doc = Get-Content $acctFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $list = if ($doc.accounts) { $doc.accounts } else { $doc }
        foreach ($a in $list) {
            $lb = [string]$a.label
            if (-not [string]::IsNullOrWhiteSpace($lb)) { $labels += $lb }
        }
    } catch {
        Write-Output "FAIL: 读账号表失败：$($_.Exception.Message)"
        exit 1
    }
} else {
    Write-Output "FAIL: 找不到 $acctFile"
    exit 1
}

if ($labels.Count -eq 0) {
    Write-Output "FAIL: 账号表里没有 label"
    exit 1
}

$ok = 0
$bad = 0
foreach ($lb in $labels) {
    & $single -Label $lb
    if ($LASTEXITCODE -eq 0) { $ok++ } else { $bad++ }
}
Write-Output ""
Write-Output "DONE: ok=$ok failed=$bad total=$($labels.Count)"
if ($bad -gt 0) { exit 1 } else { exit 0 }
