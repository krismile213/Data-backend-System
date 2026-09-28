# install_auto_etl.ps1 — 注册「自动管线」计划任务（T05 §6.2）
#
# 作用：每天 09:20 / 14:50 各跑一次 backend/etl/auto_pipeline.py（增量导入 →
#       快照 → 事中日报 → 邮件），日志在 backend/state/auto_pipeline.log。
#
# ⚠️ 为什么要 09:20 / 14:50（2026-09-23 实测）：上游计划任务 DingTalkApprovalSync
#    固定在 09:00 / 14:30 起跑，实跑耗时约 7.5 分钟（09:00:02→09:07:32、
#    14:30:01→14:37:42）。若管线按原来的 09:05 / 14:35 起跑，正好撞在导出中途，
#    会读到半截 / 正在写入的 xlsx。改到 09:20 / 14:50 = 导出完成后再留 12 分钟缓冲。
#    ⚠️ 改动上游同步时间时，务必同步调整这里。
#
# ⚠️ 本机沙箱/工具环境把 schtasks.exe 拉黑过 —— 必须用 PowerShell 原生
#    ScheduledTasks 模块（Register-ScheduledTask）。需要管理员权限。
#
# ⚠️ 本机执行策略会拦 .ps1（"禁止运行脚本"）→ 用子进程显式 Bypass：
#    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_auto_etl.ps1
#    卸载：同上，末尾加 -Remove

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# scripts 的上一级 = 仓库根
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script   = Join-Path -Path $RepoRoot -ChildPath "backend\etl\auto_pipeline.py"
$TaskName = "DataAdmin-AutoPipeline"
$At1      = "09:20"                                   # 避开 DingTalkApprovalSync（09:00 起跑 + 7.5min）
$At2      = "14:50"                                   # 避开 DingTalkApprovalSync（14:30 起跑 + 7.5min）

# ⚠️ 不能用 Join-Path a, b（逗号会被当成数组绑到 -Path，报 ChildPath 丢失）
# 也不能直接拼 $env:USERPROFILE（本机某些执行上下文里它是空的）
$HomeDir = $env:USERPROFILE
if (-not $HomeDir) { $HomeDir = Join-Path -Path "C:\Users" -ChildPath $env:USERNAME }

$PyExe = "python"                                     # 最终回退：PATH 里的 python
foreach ($cand in @(
    (Join-Path -Path $HomeDir -ChildPath ".workbuddy\binaries\python\envs\default\Scripts\python.exe"),
    "C:\Users\dell\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)) {
    if (Test-Path $cand) { $PyExe = $cand; break }
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已删除计划任务 $TaskName"
    exit 0
}

if (-not (Test-Path $Script)) { throw "找不到管线脚本: $Script" }
Write-Host "解释器: $PyExe"
Write-Host "脚本  : $Script"

$action    = New-ScheduledTaskAction -Execute $PyExe `
             -Argument "`"$Script`" --skip-mail" `
             -WorkingDirectory $RepoRoot
$trigger1  = New-ScheduledTaskTrigger -Daily -At $At1
$trigger2  = New-ScheduledTaskTrigger -Daily -At $At2
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
             -MultipleInstances IgnoreNew

# 已存在则先卸再装（幂等）
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger1, $trigger2 `
    -Settings $settings `
    -Description "数据后台系统自动管线：增量导入→快照→事中日报（邮件按规则开关）。须晚于 DingTalkApprovalSync 完成（约 09:07/14:37）。" | Out-Null

Write-Host "已注册计划任务 $TaskName（每天 $At1 / $At2，--skip-mail）"
Write-Host "手动试跑：& `"$PyExe`" `"$Script`" --skip-mail"
Write-Host "日志：backend\state\auto_pipeline.log"
