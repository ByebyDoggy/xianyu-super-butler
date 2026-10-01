# 注册开机/登录自启（24 小时常驻）。
# 用任务计划而不是"启动文件夹"：启动文件夹只在交互式登录时触发，
# 而任务计划可以设置「用户登录时」+「失败自动重启」，更接近 7x24 的预期。
#
# 用法（在 Rui 机器上、以当前用户身份执行）：
#   powershell -ExecutionPolicy Bypass -File register_autostart.ps1
# 取消：
#   Unregister-ScheduledTask -TaskName 'XianyuSuperButler' -Confirm:$false

$ErrorActionPreference = 'Stop'
$root = 'D:\dev\python\xianyu-super-butler'
$taskName = 'XianyuSuperButler'
$script = Join-Path $root 'start_service.ps1'

if (-not (Test-Path $script)) { Write-Output "ERROR: 找不到 $script"; exit 1 }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $root

# 登录时触发（Rui 机器是 24h 常开的桌面机）
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)   # 0 = 不限时，常驻

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Output "已注册计划任务: $taskName（登录时自动启动，失败每 5 分钟重启，最多 3 次）"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize
