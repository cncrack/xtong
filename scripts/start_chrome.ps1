# 在**交互式用户会话**里拉起 Chrome（面板跑在后台任务里起不了 GUI）。
#
# ## 为什么需要这个脚本（2026-11-03 实测）
#
# 面板跑在后台计划任务（非交互会话）里，由它 `subprocess.Popen` 起的 Chrome
# **拿不到桌面、直接退出** → 调试端口永远起不来。表现是点「启动浏览器并登录」
# 报「Chrome 起来了但调试端口 9222 没响应」。
# 而同一个二进制、同一组参数，从**交互式 shell** 起就完全正常。
#
# 所以分工：
#   - 面板（后台、耐久）：只负责**触发**本任务，以及之后的 CDP 注入登录态
#     （`connect_over_cdp` 只是 HTTP，不需要桌面）；
#   - 本脚本（`-LogonType Interactive` 任务，有桌面）：真正把 Chrome 拉起来。
#
# 用法（由面板触发，不要手动跑）：
#   schtasks /run /tn xxt-gxks-chrome
# 读 `data/tmp/chrome_start.json` 拿 label/port，结果（含 pid）写 `chrome_start.result.json`。
#
# ## 编码注意
#
# 本文件必须存成**带 BOM 的 UTF-8**：Windows PowerShell 5.1 把无 BOM 的 .ps1
# 按系统 ANSI（中文系统是 GBK）读，中文注释会变成乱码并**导致解析失败**
# （2026-11-03 踩过：任务结果码 1、无任何输出）。改动后请确认 BOM 还在。

$ErrorActionPreference = "Stop"
# 仓库根 = 本脚本的上一级。不写死盘符：同一仓库有多个 worktree（E:\xxt / E:\xxt-gxks）。
$root = Split-Path -Parent $PSScriptRoot
$specPath = Join-Path $root "data\tmp\chrome_start.json"
$resultPath = Join-Path $root "data\tmp\chrome_start.result.json"

function Write-Result($obj) {
    ($obj | ConvertTo-Json -Depth 5) | Set-Content -Path $resultPath -Encoding UTF8
}

try {
    $spec = Get-Content $specPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $label = [string]$spec.label
    $port = [int]$spec.port
    $chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
    if ($env:XXT_GXKS_CHROME) { $chrome = $env:XXT_GXKS_CHROME }
    $profile = Join-Path $root "data\profiles\$label"

    if (-not (Test-Path $chrome)) { throw "找不到 Chrome：$chrome（设 XXT_GXKS_CHROME 指向 chrome.exe）" }
    if ([string]::IsNullOrWhiteSpace($label)) { throw "缺少 label" }

    $argv = @(
        "--remote-debugging-port=$port",
        "--user-data-dir=$profile",
        "--no-first-run",
        "--no-default-browser-check"
    )
    # -PassThru 拿 pid：面板要显示「停止浏览器」并只停自己起的那个
    $proc = Start-Process -FilePath $chrome -ArgumentList $argv -PassThru

    # 等调试端口（Chrome 冷启动几秒~十几秒）
    $up = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect("127.0.0.1", $port)
            $c.Close()
            $up = $true
            break
        } catch { }
    }
    if (-not $up) {
        throw "Chrome 起来了但调试端口 $port 没响应。" +
              "常见原因：该 profile 已被另一个**不带调试端口**的 Chrome 占用 —— " +
              "先关掉那个 Chrome 再重试。"
    }
    Write-Result @{ ok = $true; port = $port; label = $label; pid = $proc.Id }
}
catch {
    Write-Result @{ ok = $false; error = $_.Exception.Message }
}
