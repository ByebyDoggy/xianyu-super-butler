# 以管理员权限停止本服务（计划任务以提升权限启动，普通 shell 杀不掉）。
$ErrorActionPreference = 'Continue'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*xianyu-super-butler*' -and $_.CommandLine -notlike '*3.2*' -and $_.CommandLine -like '*Start.py*' } |
    ForEach-Object { Write-Output "killing pid=$($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$c = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
# Do NOT use $pid here: it is a read-only automatic variable in PowerShell,
# assigning it throws and skips the kill. Use $procId instead.
foreach ($procId in @($c.OwningProcess | Sort-Object -Unique)) {
    if ($procId) { Write-Output "killing port owner pid=$procId"; Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 3
if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output "PORT 8080 STILL BUSY"
} else { Write-Output "PORT 8080 RELEASED" }
