# ═══════════════════════════════════════════════════
# OpenAi-AGBC 环境初始化脚本 (Windows PowerShell)
# 用法: powershell -ExecutionPolicy Bypass -File setup_env.ps1
# ═══════════════════════════════════════════════════

Write-Host "══════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  OpenAi-AGBC 环境初始化 (Windows)" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════" -ForegroundColor Cyan

# ── 1. 创建 / 激活 Python 虚拟环境 ──
Write-Host "`n[1/4] Python 虚拟环境..." -ForegroundColor Yellow
$VenvDir = ".venv"
if (Test-Path $VenvDir) {
    Write-Host "  虚拟环境已存在: $VenvDir ✓" -ForegroundColor Green
} else {
    Write-Host "  创建虚拟环境: $VenvDir ..."
    python -m venv $VenvDir
    Write-Host "  虚拟环境创建成功 ✓" -ForegroundColor Green
}
# 激活虚拟环境
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    & $ActivateScript
    Write-Host "  已激活: $(python --version)" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ 激活脚本不存在，请手动运行: .venv\Scripts\activate" -ForegroundColor Red
}

# ── 2. 安装 Python 依赖 ──
Write-Host "[2/4] 安装 Python 依赖..." -ForegroundColor Yellow
pip install --upgrade pip -q 2>$null
if (Test-Path "requirements.txt") {
    pip install -r requirements.txt -q 2>$null
}
pip install playwright -q 2>$null

# ── 3. 安装 Playwright 浏览器 + 验证 Chrome ──
Write-Host "[3/4] 安装 Playwright Chromium 并验证 Chrome..." -ForegroundColor Yellow
python -m playwright install chromium 2>$null

$chromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe"
)
$pwChrome = Get-ChildItem "$env:LOCALAPPDATA\ms-playwright\chromium-*\chrome-win\chrome.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
if ($pwChrome) { $chromePaths = @($pwChrome.FullName) + $chromePaths }

$foundChrome = ""
foreach ($p in $chromePaths) {
    if (Test-Path $p) { $foundChrome = $p; break }
}
if ($foundChrome) {
    Write-Host "  Chrome: $foundChrome" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ 未找到 Chrome, 请安装 Google Chrome 或运行: python -m playwright install chromium" -ForegroundColor Red
}

# ── 4. 测试网络 ──
Write-Host "[4/4] 测试网络..." -ForegroundColor Yellow
try {
    $resp = Invoke-WebRequest -Uri "https://js.stripe.com/v3/" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        Write-Host "  可直连 Stripe ✓" -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠️ 无法直连 Stripe, 请配置代理" -ForegroundColor Red
}

# Windows 上不需要 Xvfb (有原生 GUI)
Write-Host ""
Write-Host "══════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  ✅ 环境初始化完成" -ForegroundColor Green
Write-Host ""
Write-Host "  启动项目:" -ForegroundColor White
Write-Host "    .venv\Scripts\activate" -ForegroundColor White
Write-Host "    streamlit run ui.py" -ForegroundColor White
Write-Host "══════════════════════════════════════" -ForegroundColor Cyan
