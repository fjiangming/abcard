# ═══════════════════════════════════════════════════
# OpenAi-AGBC 环境初始化脚本 (Windows PowerShell)
# 用法: powershell -ExecutionPolicy Bypass -File setup_env.ps1
# ═══════════════════════════════════════════════════

Write-Host "══════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  OpenAi-AGBC 环境初始化 (Windows)" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════" -ForegroundColor Cyan

# ── 1. 安装 Python 依赖 ──
Write-Host "`n[1/4] 安装 Python 依赖..." -ForegroundColor Yellow
if (Test-Path "requirements.txt") {
    pip install -r requirements.txt -q 2>$null
}
pip install playwright -q 2>$null

# ── 2. 安装 Playwright 浏览器 ──
Write-Host "[2/4] 安装 Playwright Chromium..." -ForegroundColor Yellow
python -m playwright install chromium 2>$null

# ── 3. 验证 Chrome 可用 ──
Write-Host "[3/4] 验证 Chrome..." -ForegroundColor Yellow
$chromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe"
)
# Playwright Chrome
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
Write-Host "  启动项目: streamlit run ui.py" -ForegroundColor White
Write-Host "══════════════════════════════════════" -ForegroundColor Cyan
