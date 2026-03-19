<#
.SYNOPSIS
    OpenAi-AGBC 一键部署脚本 (Windows PowerShell)

.DESCRIPTION
    在 Windows 本地部署 OpenAi-AGBC，使用 Python venv 隔离。
    所有文件均在项目目录内，不污染系统环境。

.PARAMETER Action
    deploy   - 部署/启动 (默认)
    stop     - 停止服务
    uninstall - 卸载 (删除 venv)
    status   - 查看运行状态

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy.ps1
    powershell -ExecutionPolicy Bypass -File deploy.ps1 -Action stop
    powershell -ExecutionPolicy Bypass -File deploy.ps1 -Action uninstall
#>

param(
    [ValidateSet("deploy", "stop", "uninstall", "status")]
    [string]$Action = "deploy"
)

$ErrorActionPreference = "Stop"
$AppDir = $PSScriptRoot
$VenvDir = Join-Path $AppDir ".venv"
$DataDir = Join-Path $AppDir "data"
$PidFile = Join-Path $DataDir "streamlit.pid"

function Write-Info  { Write-Host "[INFO] $args" -ForegroundColor Cyan }
function Write-Ok    { Write-Host "[OK] $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN] $args" -ForegroundColor Yellow }
function Write-Err   { Write-Host "[ERROR] $args" -ForegroundColor Red }

# ---- 停止 ----
function Stop-App {
    Write-Info "停止 OpenAi-AGBC..."
    if (Test-Path $PidFile) {
        $pid = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($pid) {
            try {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                Write-Ok "进程 $pid 已停止"
            } catch {
                Write-Warn "进程 $pid 可能已退出"
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    # 兜底：按端口查找残留进程
    $procs = Get-NetTCPConnection -LocalPort 8501 -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            Write-Warn "已清理残留进程: $p"
        } catch {}
    }
    Write-Ok "服务已停止"
}

# ---- 卸载 ----
function Uninstall-App {
    Stop-App
    Write-Host ""
    Write-Host "============================="
    Write-Host "  🗑️  卸载 OpenAi-AGBC"
    Write-Host "============================="
    Write-Host ""

    if (Test-Path $VenvDir) {
        Remove-Item $VenvDir -Recurse -Force
        Write-Ok "虚拟环境已删除: $VenvDir"
    }

    $answer = Read-Host "是否删除数据目录 ${DataDir}? (y/N)"
    if ($answer -eq 'y' -or $answer -eq 'Y') {
        Remove-Item $DataDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Ok "数据目录已删除"
    } else {
        Write-Info "数据目录已保留: $DataDir"
    }

    Write-Ok "卸载完成"
    exit 0
}

# ---- 状态 ----
function Get-AppStatus {
    Write-Host ""
    Write-Host "============================="
    Write-Host "  📊 OpenAi-AGBC 状态"
    Write-Host "============================="
    Write-Host ""

    if (Test-Path $PidFile) {
        $pid = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($pid) {
            $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Ok "运行中 (PID: $pid)"
                Write-Info "访问地址: http://localhost:8501"
                exit 0
            }
        }
    }
    Write-Warn "未运行"
    exit 0
}

# ---- 分派 ----
switch ($Action) {
    "stop"      { Stop-App; exit 0 }
    "uninstall" { Uninstall-App }
    "status"    { Get-AppStatus }
}

# ============================================
# 部署主流程
# ============================================
Write-Host ""
Write-Host "============================="  -ForegroundColor Cyan
Write-Host "  🚀 OpenAi-AGBC Windows 部署" -ForegroundColor Cyan
Write-Host "============================="  -ForegroundColor Cyan
Write-Host ""

# ---- 1. 检测 Python ----
Write-Info "[1/5] 检测 Python..."
$pythonCmd = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 9) {
                $pythonCmd = $cmd
                Write-Ok "Python 已就绪: $ver"
                break
            }
        }
    } catch {}
}
if (-not $pythonCmd) {
    Write-Err "未找到 Python 3.9+，请先安装 Python"
    Write-Host "  下载地址: https://www.python.org/downloads/"
    exit 1
}

# ---- 2. 创建虚拟环境 ----
Write-Info "[2/5] 创建 Python 虚拟环境..."
if (-not (Test-Path $VenvDir)) {
    & $pythonCmd -m venv $VenvDir
    Write-Ok "虚拟环境已创建: $VenvDir"
} else {
    Write-Ok "虚拟环境已存在"
}

$pipCmd  = Join-Path $VenvDir "Scripts\pip.exe"
$pyCmd   = Join-Path $VenvDir "Scripts\python.exe"
$stCmd   = Join-Path $VenvDir "Scripts\streamlit.exe"

# ---- 3. 安装依赖 ----
Write-Info "[3/5] 安装 Python 依赖..."
& $pipCmd install --upgrade pip -q 2>&1 | Out-Null
& $pipCmd install -r (Join-Path $AppDir "requirements.txt") -q
& $pipCmd install playwright -q
Write-Ok "Python 依赖已安装"

# ---- 4. 安装浏览器 ----
Write-Info "[4/5] 安装 Playwright Chromium..."
$pwCmd = Join-Path $VenvDir "Scripts\playwright.exe"
& $pwCmd install chromium 2>&1 | Out-Null
Write-Ok "Chromium 已安装"

# ---- 5. 初始化数据目录及配置 ----
Write-Info "[5/5] 初始化数据和配置..."
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

$configDest = Join-Path $AppDir "config.json"
$configData = Join-Path $DataDir "config.json"

if (-not (Test-Path $configData)) {
    Copy-Item (Join-Path $AppDir "config.example.json") $configData
    Write-Warn "已创建 config.json，请编辑: $configData"
}

# 同步 data 目录的 config 到工作目录
Copy-Item $configData $configDest -Force

# 初始化数据库
& $pyCmd -c "from database import init_db; init_db(); print('[deploy] 数据库已初始化')"

# ---- 启动 ----
Stop-App 2>$null

Write-Info "启动 Streamlit..."
$stProcess = Start-Process -FilePath $stCmd -ArgumentList @(
    "run", "ui.py",
    "--server.port=8501",
    "--server.address=0.0.0.0",
    "--server.headless=true",
    "--server.maxUploadSize=5",
    "--browser.gatherUsageStats=false"
) -WorkingDirectory $AppDir -PassThru -WindowStyle Hidden

$stProcess.Id | Out-File $PidFile -Force

# 等待启动
Start-Sleep -Seconds 3
$proc = Get-Process -Id $stProcess.Id -ErrorAction SilentlyContinue
if ($proc) {
    Write-Ok "服务启动成功 (PID: $($stProcess.Id))"
} else {
    Write-Err "启动失败，请检查日志"
    exit 1
}

# ---- 完成 ----
Write-Host ""
Write-Host "============================="  -ForegroundColor Green
Write-Host "  ✅ 部署完成!"                 -ForegroundColor Green
Write-Host "============================="  -ForegroundColor Green
Write-Host ""
Write-Host "  📦 应用目录:   $AppDir"
Write-Host "  📂 数据目录:   $DataDir"
Write-Host "  📝 配置文件:   $configData"
Write-Host "  🌐 访问地址:   http://localhost:8501"
Write-Host ""
Write-Host "  常用命令:"
Write-Host "    查看状态:    powershell -File deploy.ps1 -Action status"
Write-Host "    停止服务:    powershell -File deploy.ps1 -Action stop"
Write-Host "    卸载清理:    powershell -File deploy.ps1 -Action uninstall"
Write-Host ""
