# 一键打包 Windows 正式安装包（PowerShell 7）
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw '请使用 PowerShell 7 (pwsh) 运行。'
}

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root 'scripts\dev-env.ps1')

$engineDir = Join-Path $root 'engine'
$binDir    = Join-Path $root 'desktop\src-tauri\binaries'
New-Item -ItemType Directory -Path $binDir -Force | Out-Null

Write-Host '=== 1. 使用 PyInstaller 打包独立 Python AI 引擎 ===' -ForegroundColor Cyan
Set-Location $engineDir
uv run pyinstaller --noconfirm --onefile --clean `
    --name 'epub-refiner-engine-x86_64-pc-windows-msvc' `
    --distpath $binDir `
    --workpath (Join-Path $engineDir 'build\pyinstaller') `
    --specpath (Join-Path $engineDir 'build') `
    --collect-all litellm `
    --hidden-import keyring.backends.Windows `
    'src\epub_refiner_engine\server.py'

Write-Host '=== 2. 使用 Tauri v2 构建 Windows 安装向导 (.exe) ===' -ForegroundColor Cyan
Set-Location (Join-Path $root 'desktop')
npm.cmd run tauri build

Write-Host '=== 3. 归整成品到 E:\EPUB精排项目\ 与 release\ ===' -ForegroundColor Cyan
$releaseRoot = Join-Path $root 'release'
$portableDir = Join-Path $releaseRoot 'portable'
$topRoot     = 'E:\EPUB精排项目'
$topPortable = Join-Path $topRoot '便携版'
$tauriRel    = Join-Path $root 'desktop\src-tauri\target\release'
New-Item -ItemType Directory -Path $portableDir, $topPortable -Force | Out-Null

# 清理旧文件，避免新手混淆
Remove-Item (Join-Path $releaseRoot '*.msi') -ErrorAction SilentlyContinue
Remove-Item (Join-Path $topRoot '*.msi') -ErrorAction SilentlyContinue
Remove-Item (Join-Path $topRoot 'EPUB-Refiner_*_x64-setup.exe') -ErrorAction SilentlyContinue
Remove-Item (Join-Path $topRoot '绿色免安装版') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $topPortable 'epub-refiner-engine.exe') -ErrorAction SilentlyContinue
Remove-Item (Join-Path $portableDir 'epub-refiner-engine.exe') -ErrorAction SilentlyContinue

$setupSrc = Get-ChildItem (Join-Path $tauriRel 'bundle\nsis\*-setup.exe') | Select-Object -First 1
Copy-Item $setupSrc.FullName $releaseRoot -Force
Copy-Item $setupSrc.FullName (Join-Path $topRoot 'EPUB精排工厂_电脑安装版.exe') -Force

if (Test-Path (Join-Path $root 'THIRD_PARTY_NOTICES.txt')) {
    Copy-Item (Join-Path $root 'THIRD_PARTY_NOTICES.txt') $releaseRoot -Force
    Copy-Item (Join-Path $root 'THIRD_PARTY_NOTICES.txt') $topRoot -Force
}

foreach ($pDest in @($portableDir, $topPortable)) {
    $innerDir = Join-Path $pDest '_内部组件'
    New-Item -ItemType Directory -Path $innerDir -Force | Out-Null
    Copy-Item (Join-Path $tauriRel 'desktop.exe')             (Join-Path $pDest 'EPUB精排工厂.exe') -Force
    Copy-Item (Join-Path $tauriRel 'epub-refiner-engine.exe') (Join-Path $innerDir 'epub-refiner-engine.exe') -Force
}

$guideText = @"
【EPUB 精排工厂】两个版本有什么区别？（新手必看）

外面的安装程序和「便携版」功能完全一样，您只需要任选其一使用即可：

1. 想把软件正式装进电脑、在桌面生成图标：
   → 请直接双击「EPUB精排工厂_电脑安装版.exe」，按中文提示点“下一步”完成安装。

2. 不想安装软件、或放在 U 盘里随身带着用：
   → 请打开「便携版」文件夹，直接双击里面唯一的「EPUB精排工厂.exe」即可打开。
   （注意：不要删除「便携版」里面的「_内部组件」文件夹，否则软件无法启动。）
"@
Set-Content -Path (Join-Path $topRoot '请先看我_使用说明.txt') -Value $guideText -Encoding UTF8
Set-Content -Path (Join-Path $topPortable '点我启动说明.txt') -Value "请直接双击当前文件夹里的「EPUB精排工厂.exe」即可打开软件。`r`n请勿删除「_内部组件」文件夹。" -Encoding UTF8

# 压缩便携版与安装版以便发布到 GitHub Releases
$portableZip = Join-Path $topRoot 'EPUB精排工厂_便携版.zip'
$setupZip    = Join-Path $topRoot 'EPUB精排工厂_电脑安装版.zip'
Compress-Archive -Path $topPortable -DestinationPath $portableZip -CompressionLevel Optimal -Force
Compress-Archive -Path (Join-Path $topRoot 'EPUB精排工厂_电脑安装版.exe') -DestinationPath $setupZip -CompressionLevel Optimal -Force

# 生成 SHA-256 校验文件
$hashLines = @(
    (Get-FileHash (Join-Path $topRoot 'EPUB精排工厂_电脑安装版.exe') -Algorithm SHA256),
    (Get-FileHash $setupZip -Algorithm SHA256),
    (Get-FileHash $portableZip -Algorithm SHA256),
    (Get-FileHash (Join-Path $topPortable 'EPUB精排工厂.exe') -Algorithm SHA256),
    (Get-FileHash (Join-Path $topPortable '_内部组件\epub-refiner-engine.exe') -Algorithm SHA256)
) | ForEach-Object { "$($_.Hash)  $(Split-Path -Leaf $_.Path)" }
Set-Content -Path (Join-Path $topRoot 'SHA256SUMS.txt') -Value $hashLines -Encoding UTF8
Set-Content -Path (Join-Path $releaseRoot 'SHA256SUMS.txt') -Value $hashLines -Encoding UTF8

Write-Host "打包完成！便携版内现在只有一个 EPUB精排工厂.exe，并已生成压缩包。" -ForegroundColor Green
