# =====================================================================
#  EPUB 精排工厂 —— 项目专用环境脚本（只对当前 PowerShell 7 窗口生效）
#  用法：在 PowerShell 7 中执行   . .\scripts\dev-env.ps1
#  作用：把 E:\EPUB精排项目\tools 里的工具临时加入 PATH，
#        并把所有缓存都放到 E 盘，不修改系统环境变量。
# =====================================================================
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw '请使用 PowerShell 7 (pwsh) 运行本脚本，不支持 Windows PowerShell 5.1。'
}

$ToolsRoot = 'E:\EPUB精排项目\tools'
$CacheRoot = 'E:\EPUB精排项目\cache'

# --- Rust ---
$env:RUSTUP_HOME = Join-Path $ToolsRoot 'rust\rustup'
$env:CARGO_HOME  = Join-Path $ToolsRoot 'rust\cargo'

# --- uv / Python（Python 解释器与依赖缓存全部放在 E 盘）---
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ToolsRoot 'python'
$env:UV_PYTHON_BIN_DIR     = Join-Path $ToolsRoot 'python\bin'
$env:UV_TOOL_DIR           = Join-Path $ToolsRoot 'uv-tools'
$env:UV_CACHE_DIR          = Join-Path $CacheRoot 'uv'
$env:PYTHONUTF8            = '1'
$env:PYTHONIOENCODING      = 'utf-8'

# --- npm 缓存 ---
$env:npm_config_cache = Join-Path $CacheRoot 'npm'

# --- EPUBCheck ---
$env:EPUBCHECK_JAR = Join-Path $ToolsRoot 'epubcheck\epubcheck-5.4.0\epubcheck.jar'

# --- PATH（只在当前窗口生效）---
$extra = @(
    (Join-Path $ToolsRoot 'rust\cargo\bin'),
    (Join-Path $ToolsRoot 'uv'),
    'E:\java\JDK\bin'
)
$env:PATH = (($extra + ($env:PATH -split ';')) | Where-Object { $_ } | Select-Object -Unique) -join ';'

foreach ($d in @($env:UV_PYTHON_INSTALL_DIR, $env:UV_CACHE_DIR, $env:npm_config_cache)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

Write-Host '[EPUB 精排工厂] 项目环境已加载（仅当前窗口有效）' -ForegroundColor Green
