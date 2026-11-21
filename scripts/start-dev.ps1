# 一键启动桌面端开发环境（PowerShell 7）
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw '请使用 PowerShell 7 (pwsh) 运行。'
}
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root 'scripts\dev-env.ps1')
Set-Location (Join-Path $root 'desktop')
npm.cmd run tauri dev
