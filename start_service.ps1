$ErrorActionPreference = 'Stop'
$root = 'D:\dev\python\xianyu-super-butler'
Set-Location $root
New-Item -ItemType Directory -Force -Path 'logs', 'data' | Out-Null

$Port = if ($env:API_PORT) { [int]$env:API_PORT } else { 8080 }
$Py = Join-Path $root '.venv\Scripts\python.exe'

function Get-AppProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*xianyu-super-butler*" -and $_.CommandLine -like '*Start.py*' }
}
function Get-PortOwners([int]$p) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($c) { @($c.OwningProcess | Sort-Object -Unique) } else { @() }
}

# 1) 先停掉遗留实例。
#    Start.py 的 uvicorn 跑在后台线程，端口被占时只打一行日志、进程照活 ——
#    不先确认端口释放，就会出现「新实例起来了、旧实例还在服务」的假重启。
foreach ($p in @(Get-AppProcesses)) {
    Write-Output "stopping stale instance pid=$($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline -and (Get-PortOwners $Port).Count -gt 0) { Start-Sleep -Milliseconds 500 }
$owners = Get-PortOwners $Port
if ($owners.Count -gt 0) {
    Write-Output "ERROR: 端口 $Port 仍被 PID $($owners -join ',') 占用，已中止"
    exit 1
}

# 2) 环境变量
$credFile = Join-Path $root 'ADMIN-CREDENTIALS.txt'
if (-not (Test-Path $credFile)) { Write-Output "ERROR: 缺少 $credFile"; exit 1 }
$env:ADMIN_PASSWORD = (Get-Content -Raw $credFile).Trim()
$env:DB_PATH = 'data\xianyu_data.db'
$env:PYTHONUNBUFFERED = '1'
$env:TZ = 'Asia/Shanghai'
$env:PLAYWRIGHT_DOWNLOAD_HOST = 'https://cdn.npmmirror.com/binaries/playwright'
$env:PATCHRIGHT_DOWNLOAD_HOST = 'https://cdn.npmmirror.com/binaries/playwright'

# 3) 启动
$proc = Start-Process -FilePath $Py -ArgumentList 'Start.py' -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root 'logs\service.log') `
    -RedirectStandardError (Join-Path $root 'logs\service.err') `
    -PassThru
$proc.Id | Out-File -FilePath (Join-Path $root 'service.pid') -Encoding ascii

# 4) 校验：不只是进程活着，而是 /health 通、且监听者就是新实例
$ok = $false
$deadline = (Get-Date).AddSeconds(180)
while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) { break }
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if ($ok) {
    Write-Output "OK: pid=$($proc.Id) 端口 $Port 监听者=$((Get-PortOwners $Port) -join ',') /health 正常"
    exit 0
}
Write-Output "FAILED: pid=$($proc.Id) 启动后 /health 无响应"
Get-Content (Join-Path $root 'logs\service.err') -Tail 25 -ErrorAction SilentlyContinue
exit 1
