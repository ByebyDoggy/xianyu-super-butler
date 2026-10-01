# 以管理员权限停止本服务（计划任务以提升权限启动，普通 shell 杀不掉）。
$ErrorActionPreference = 'Continue'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*xianyu-super-butler*' -and $_.CommandLine -notlike '*3.2*' -and $_.CommandLine -like '*Start.py*' } |
    ForEach-Object { Write-Output "killing pid=$($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$c = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
foreach ($pid in @($c.OwningProcess | Sort-Object -Unique)) {
    if ($pid) { Write-Output "killing port owner pid=$pid"; Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 3
if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output "PORT 8080 STILL BUSY"
} else { Write-Output "PORT 8080 RELEASED" }
