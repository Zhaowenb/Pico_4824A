param([switch]$FirewallOnly)

$ErrorActionPreference = "Stop"
$ScriptPath = $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptPath
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Port = 4824
$RuleName = "Pico4824A-Private-LAN-TCP-4824"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-PrivateFirewallRule {
    $rule = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
    if ($rule) {
        $portFilter = $rule | Get-NetFirewallPortFilter
        $addressFilter = $rule | Get-NetFirewallAddressFilter
        if ($rule.Enabled -ne "True" -or $rule.Direction -ne "Inbound" -or
            $rule.Action -ne "Allow" -or $rule.Profile -ne "Private" -or
            $portFilter.Protocol -ne "TCP" -or "$($portFilter.LocalPort)" -ne "$Port" -or
            "$($addressFilter.RemoteAddress)" -ne "LocalSubnet") {
            throw "A firewall rule named $RuleName already exists with different settings; inspect it manually."
        }
        return $true
    }
    return $false
}

function Ensure-PrivateFirewallRule {
    if (Test-PrivateFirewallRule) { return }
    if (-not (Test-Administrator)) {
        throw "Adding the Windows Firewall rule requires administrator approval."
    }
    New-NetFirewallRule -Name $RuleName -DisplayName "Pico 4824A Web (Private LAN only)" `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -Profile Private -RemoteAddress LocalSubnet | Out-Null
}

if ($FirewallOnly) {
    Ensure-PrivateFirewallRule
    exit 0
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python environment is missing. Run .\setup.ps1 in this directory first."
}

$connections = @(Get-NetIPConfiguration | Where-Object {
    $_.NetAdapter -and $_.NetAdapter.Status -eq "Up" -and
    $_.IPv4Address -and $_.IPv4DefaultGateway
})
$networks = @()
foreach ($connection in $connections) {
    $profile = Get-NetConnectionProfile -InterfaceIndex $connection.InterfaceIndex -ErrorAction SilentlyContinue
    foreach ($address in @($connection.IPv4Address)) {
        if (-not $address.IPAddress -or $address.IPAddress -like "169.254.*") { continue }
        $networks += [pscustomobject]@{
            Address = $address.IPAddress
            Name = if ($profile -and $profile.Name) { $profile.Name } else { $connection.InterfaceAlias }
            Interface = $connection.InterfaceAlias
            Category = if ($profile) { "$($profile.NetworkCategory)" } else { "Unknown" }
        }
    }
}

$ssid = $null
try {
    foreach ($line in @(netsh wlan show interfaces 2>$null)) {
        if ($line -match '^\s*SSID\s*:\s*(.*?)\s*$' -and $Matches[1]) {
            $ssid = $Matches[1]
            break
        }
    }
} catch {
    # Wired-only computers may not have a WLAN service.
}

function Get-BackendInfo {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { return $null }
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $response = $null
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/status" `
            -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    } catch { }
    $isPico = $response -and "$($response.Headers['Server'])" -like "Pico4824AWeb/*"
    $ownerId = if ($owners.Count -eq 1) { [int]$owners[0] } else { 0 }
    $process = if ($ownerId) {
        Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
    } else { $null }
    $verified = $isPico -and $process -and
        $process.CommandLine -match '(?i)(^|\s)-m\s+pico4824a\s+web(\s|$)'
    $lanBound = [bool]@($listeners | Where-Object {
        $_.LocalAddress -in @("0.0.0.0", "::") -or
        $_.LocalAddress -in @($networks | ForEach-Object { $_.Address })
    }).Count
    return [pscustomobject]@{
        Owners = $owners
        Pid = $ownerId
        IsPico = [bool]$isPico
        Verified = [bool]$verified
        LanBound = $lanBound
        State = if ($isPico) { ($response.Content | ConvertFrom-Json).state } else { "unknown" }
    }
}

function Show-Status {
    Update-NetworkProfiles
    $backend = Get-BackendInfo
    if ($backend) {
        Write-Host "端口 $Port：已有进程 PID $($backend.Owners -join ', ')；状态 $($backend.State)"
        if (-not $backend.IsPico) { Write-Warning "端口不是可识别的 Pico Web 服务，不能由本脚本管理。" }
        elseif (-not $backend.Verified) { Write-Warning "无法验证进程命令行，关闭操作已禁用。" }
        elseif (-not $backend.LanBound) {
            if (@($networks | Where-Object { $_.Category -eq "Private" }).Count) {
                Write-Warning "当前服务仅供本机访问。按 r 可以重启为局域网模式。"
            } else {
                Write-Warning "当前服务仅供本机访问。若需局域网访问，先将可信网络设为 Private，再按 r。"
            }
        }
    } else {
        Write-Host "端口 $Port：没有监听进程。"
    }
    if ($backend -and $backend.IsPico) {
        Write-Host "本机可访问：http://127.0.0.1:$Port/"
    } else {
        Write-Host "本机：服务尚未就绪，当前不能访问 127.0.0.1:$Port"
    }
    if ($ssid) { Write-Host "当前 Wi-Fi SSID：$ssid" }
    foreach ($network in $networks) {
        Write-Host "网络：$($network.Name) [$($network.Interface), $($network.Category)]"
        if ($backend -and $backend.IsPico -and $backend.LanBound -and
            $network.Category -eq "Private") {
            Write-Host "局域网候选地址：http://$($network.Address):$Port/"
        }
    }
    Write-Host "公网：未开放。不要在路由器上转发 $Port 端口。"
}

function Update-NetworkProfiles {
    foreach ($network in $networks) {
        $profile = Get-NetConnectionProfile -InterfaceAlias $network.Interface -ErrorAction SilentlyContinue
        if ($profile) { $network.Category = "$($profile.NetworkCategory)" }
    }
}

function Stop-ActiveTask {
    $backend = Get-BackendInfo
    if (-not $backend -or -not $backend.Verified) {
        Write-Warning "未发现可验证的 Pico 后端。"
        return
    }
    if ($backend.State -notin @("running", "paused")) {
        Write-Host "当前没有运行中的测量任务。"
        return
    }
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/stop" -Method Post `
            -ContentType "application/json" -Body "{}" -TimeoutSec 10 | Out-Null
        Write-Host "已请求停止测量；等待状态结束后再按 q 关闭后端。"
    } catch {
        Write-Warning "停止测量请求失败：$($_.Exception.Message)"
    }
}

function Stop-Backend {
    $backend = Get-BackendInfo
    if (-not $backend) { Write-Host "后端已经关闭。"; return $true }
    if (-not $backend.Verified) {
        Write-Warning "进程身份未验证，不发送关闭命令。"
        return $false
    }
    if ($backend.State -in @("running", "paused")) {
        Write-Warning "测量正在运行。先按 s 停止测量，再按 q 关闭后端。"
        return $false
    }
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/admin/shutdown" `
            -Method Post -ContentType "application/json" -Body "{}" `
            -Headers @{ "X-Pico-Local-Control" = "shutdown" } -TimeoutSec 15 | Out-Null
    } catch {
        Write-Warning "无法安全关闭：$($_.Exception.Message)"
        Write-Host "旧版后端可能没有安全退出接口。确认 ATA 已断电且任务已停止后，可选 f 进行人工确认的旧版退出。"
        return $false
    }
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (-not (Get-BackendInfo)) { Write-Host "后端已安全关闭，端口 $Port 已释放。"; return $true }
        Start-Sleep -Milliseconds 250
    }
    Write-Warning "关闭请求已发送，但端口仍被占用。请按 i 查看新进程状态。"
    return $false
}

function Stop-LegacyBackend {
    $backend = Get-BackendInfo
    if (-not $backend -or -not $backend.Verified) {
        Write-Warning "未发现可验证的 Pico 后端，不执行强制退出。"
        return
    }
    if ($backend.State -in @("running", "paused")) {
        Write-Warning "测量仍在运行，请先按 s 停止。"
        return
    }
    Write-Warning "此操作会直接结束 PID $($backend.Pid)，无法保证旧版后端执行 AWG 归零。"
    $confirmation = Read-Host "确认 ATA 功放已断电后，输入 KILL $($backend.Pid) 继续"
    if ($confirmation -cne "KILL $($backend.Pid)") { Write-Host "已取消。"; return }
    $current = Get-BackendInfo
    if (-not $current -or -not $current.Verified -or $current.Pid -ne $backend.Pid -or
        $current.State -in @("running", "paused")) {
        Write-Warning "进程或任务状态已变化，取消结束操作。"
        return
    }
    Stop-Process -Id $backend.Pid -ErrorAction Stop
    Write-Host "已结束旧版后端 PID $($backend.Pid)。"
}

function Start-Backend {
    if (Get-BackendInfo) { Write-Warning "端口 $Port 已被占用，不能再启动一份。"; return }
    Update-NetworkProfiles
    $bindHost = "127.0.0.1"
    if (@($networks | Where-Object { $_.Category -eq "Private" }).Count) {
        try {
            if (-not (Test-PrivateFirewallRule)) {
                if (Test-Administrator) {
                    Ensure-PrivateFirewallRule
                } else {
                    Write-Host "首次启用局域网需要管理员确认，以添加仅限专用网络本地子网的防火墙规则。"
                    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $ScriptPath + '"'), "-FirewallOnly")
                    $helper = Start-Process -FilePath "powershell.exe" -Verb RunAs -WindowStyle Hidden `
                        -ArgumentList $arguments -PassThru -Wait
                    if ($helper.ExitCode -ne 0) { throw "防火墙设置失败，退出码 $($helper.ExitCode)" }
                }
            }
            $bindHost = "0.0.0.0"
        } catch {
            Write-Warning "防火墙规则未就绪：$($_.Exception.Message)；改为仅本机启动。"
        }
    } else {
        Write-Warning "没有专用网络；后端仍将在 127.0.0.1:$Port 启动，但不能供局域网设备访问。"
    }
    if ($bindHost -eq "0.0.0.0") {
        Write-Warning "局域网用户可控制 Pico/AWG；本界面没有登录鉴权。"
    }
    Set-Location -LiteralPath $ProjectDir
    $logDir = Join-Path $ProjectDir "data\launcher_logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $logStem = (Get-Date -Format "yyyyMMdd_HHmmss_fff") + "_" + [Guid]::NewGuid().ToString("N").Substring(0, 8)
    $stdoutLog = Join-Path $logDir ($logStem + "_stdout.log")
    $stderrLog = Join-Path $logDir ($logStem + "_stderr.log")
    $child = Start-Process -FilePath $PythonExe -ArgumentList @(
        "-X", "utf8", "-u", "-m", "pico4824a", "web", "--host", $bindHost, "--port", "$Port"
    ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    Write-Host "正在启动后端，等待端口 $Port 就绪。"
    Write-Host "日志目录：$logDir；输入 l 查看最近输出。"
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $child.Refresh()
        if ($child.HasExited) {
            Write-Warning "后端进程提前退出，退出码 $($child.ExitCode)。"
            Show-BackendLogs
            return
        }
        $ready = Get-BackendInfo
        if ($ready -and $ready.IsPico) {
            Write-Host "后端已就绪，监听 PID $($ready.Pid)。输入命令后按 Enter 执行。" -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    Write-Warning "服务仍在启动；稍后按 i 刷新状态。"
}

function Show-BackendLogs {
    $logDir = Join-Path $ProjectDir "data\launcher_logs"
    $logs = @(Get-ChildItem -LiteralPath $logDir -Filter *.log -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 2)
    if (-not $logs.Count) { Write-Host "没有本启动器保存的日志。"; return }
    foreach ($log in $logs) {
        Write-Host "日志：$($log.FullName)"
        Get-Content -LiteralPath $log.FullName -Encoding UTF8 -Tail 30
    }
}

$backend = Get-BackendInfo
if ($backend -and -not $backend.IsPico) {
    throw "端口 $Port 已由其他程序占用；本脚本不会结束或覆盖它。"
}
if (-not $backend) { Start-Backend }
Show-Status
Write-Host "输入命令后按 Enter：i 状态/地址；s 停止测量；q 安全关闭后端；r 重启；o 打开网页；l 查看日志；f 旧版强制退出；h 帮助"
$leave = $false
while (-not $leave) {
    $choice = (Read-Host "Pico 管理").Trim().ToLowerInvariant()
    switch ($choice) {
        "i" { Show-Status }
        "s" { Stop-ActiveTask }
        "q" { if (Stop-Backend) { $leave = $true } }
        "r" { if (Stop-Backend) { Start-Backend; Show-Status } }
        "o" { Start-Process "http://127.0.0.1:$Port/" }
        "l" { Show-BackendLogs }
        "f" { Stop-LegacyBackend }
        "h" { Write-Host "q：仅在测量停止后请求后端安全归零并退出。s：停止当前采集/扫描。r：安全关闭后重新以局域网模式启动。f：仅旧版后端无法安全退出时，确认功放断电并输入 PID 后强制结束。" }
        default { Write-Host "未知命令，输入 h 查看帮助。" }
    }
}
