# 为单个账号注册交互式计划任务 `xxt-gxks-chrome-<label>`（2026-11-23 方案 B）。
#
# 为什么单独一个 .ps1 而不是面板里拼 `powershell -Command "..."`：
# 2026-11-23 踩到的真 bug —— `-Argument` 的值自带引号，经过
# `subprocess` → `powershell -Command` 两层转义后引号失效，
# PowerShell 把 `-ExecutionPolicy` 当成 New-ScheduledTaskAction 自己的参数：
#   New-ScheduledTaskAction : A parameter cannot be found that matches
#   parameter name 'ExecutionPolicy'
# 于是注册静默失败，点「启动并登录」才报「任务不存在」。
# 用 -File 调用本脚本 = 零转义。
#
# 输出约定（面板按这个判断成败，不再靠字符串碰运气）：
#   OK: <taskname>      注册成功
#   FAIL: <原因>        注册失败（exit 1）
#
# 编码注意：本文件必须存成**带 BOM 的 UTF-8**，否则 Windows PowerShell 5.1
# 按 GBK 读中文注释会解析失败（2026-11-03 踩过）。

param(
    [Parameter(Mandatory = $true)]
    [string]$Label
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Label)) {
    Write-Output "FAIL: 缺少 -Label"
    exit 1
}

# 与 `app._chrome_task_name()` 保持一致的命名规则
$taskName = "xxt-gxks-chrome-$Label"
$root = Split-Path -Parent $PSScriptRoot
$chromeScript = Join-Path $root "scripts\start_chrome.ps1"

if (-not (Test-Path $chromeScript)) {
    Write-Output "FAIL: 找不到 $chromeScript"
    exit 1
}

try {
    # -Argument 用**单引号**：里面的双引号原样保留给被启动的 powershell 用
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$chromeScript`" -Label $Label"
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel Limited
    # ⚠ 不用 `| Out-Null`：那会把错误吞掉，面板侧误判成功
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Force | Out-Null
    Write-Output "OK: $taskName"
    exit 0
} catch {
    Write-Output "FAIL: $($_.Exception.Message)"
    exit 1
}
