"""
AGBC 自动化构建脚本 — 一键打包为可分发的 Windows 安装包

用法:
    python build.py [--tier lite|pro|both] [--skip-compile] [--skip-installer]

步骤:
    1. 下载嵌入式 Python 3.12
    2. 安装 pip + 项目依赖
    3. 复制源码到构建目录
    4. 用 Nuitka 编译 .py → .pyd (源码保护)
    5. 注入 License 门禁入口
    6. 编译启动器为 AGBC.exe
    7. 生成 Inno Setup 安装包 (可选)

前置要求:
    - Python 3.10+ (系统 Python, 用于运行此脚本)
    - pip install nuitka (编译用)
    - Inno Setup 6 (可选, 生成安装包)
    - 网络连接 (下载嵌入式 Python)
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile


# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════
PYTHON_VERSION = "3.13.5"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_EMBED_ZIP = f"python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# 项目根目录 (build.py 在 exe/ 下, 项目在上一级)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# 构建输出目录
BUILD_DIR = os.path.join(SCRIPT_DIR, "build")
DIST_DIR = os.path.join(SCRIPT_DIR, "dist")

# 需要编译的源文件 (核心业务逻辑)
COMPILE_FILES = [
    "auth_flow.py",
    "browser_challenge.py",
    "payment_flow.py",
    "batch_register.py",
    "config.py",
    "database.py",
    "code_manager.py",
    "mail_provider.py",
    "http_client.py",
    "captcha_solver.py",
    "stripe_fingerprint.py",
    "logger.py",
    "main.py",
    "license_manager.py",  # License 模块也编译
]

# 关键: 不编译的文件 (Streamlit 需要 exec 加载的入口)
KEEP_AS_PY = [
    "ui_entry.py",  # Streamlit 入口 (License 门禁)
]

# 需要复制但不编译的文件
COPY_FILES = [
    "config.example.json",
    "test_outputs/",
]

# pip 依赖
REQUIREMENTS = [
    "curl_cffi>=0.7.0",
    "requests>=2.31.0",
    "streamlit>=1.30.0",
    "pandas>=2.0.0",
    "playwright>=1.40.0",
]


def log(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}\n")


def run(cmd, cwd=None, check=True):
    """运行命令并打印输出"""
    print(f"  $ {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    result = subprocess.run(
        cmd, cwd=cwd, shell=isinstance(cmd, str),
        capture_output=False, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败 (exit {result.returncode}): {cmd}")
    return result


def step1_download_python():
    """下载嵌入式 Python"""
    log("Step 1: 下载嵌入式 Python")
    python_dir = os.path.join(BUILD_DIR, "python")

    if os.path.isdir(python_dir) and os.path.isfile(os.path.join(python_dir, "python.exe")):
        print("  [跳过] 已存在")
        return

    os.makedirs(BUILD_DIR, exist_ok=True)
    zip_path = os.path.join(BUILD_DIR, PYTHON_EMBED_ZIP)

    if not os.path.isfile(zip_path):
        print(f"  下载 {PYTHON_EMBED_URL} ...")
        urllib.request.urlretrieve(PYTHON_EMBED_URL, zip_path)
        print(f"  下载完成: {zip_path}")

    print(f"  解压到 {python_dir} ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(python_dir)

    # 修改 python312._pth 以支持 pip 和 site-packages
    pth_files = glob.glob(os.path.join(python_dir, "python*._pth"))
    for pth in pth_files:
        with open(pth, "r") as f:
            content = f.read()
        # 取消 import site 注释
        content = content.replace("#import site", "import site")
        # 添加 Lib 目录
        if "Lib" not in content:
            content += "\nLib\nLib\\site-packages\n"
        with open(pth, "w") as f:
            f.write(content)
        print(f"  已修改 {os.path.basename(pth)}")

    print("  ✅ 嵌入式 Python 就绪")


def step2_install_pip():
    """安装 pip 到嵌入式 Python"""
    log("Step 2: 安装 pip")
    python_exe = os.path.join(BUILD_DIR, "python", "python.exe")
    pip_exe = os.path.join(BUILD_DIR, "python", "Scripts", "pip.exe")

    if os.path.isfile(pip_exe):
        print("  [跳过] pip 已安装")
        return

    get_pip = os.path.join(BUILD_DIR, "get-pip.py")
    if not os.path.isfile(get_pip):
        print(f"  下载 get-pip.py ...")
        urllib.request.urlretrieve(GET_PIP_URL, get_pip)

    run([python_exe, get_pip, "--no-warn-script-location"])
    print("  ✅ pip 已安装")


def step3_install_deps():
    """安装项目依赖"""
    log("Step 3: 安装项目依赖")
    python_exe = os.path.join(BUILD_DIR, "python", "python.exe")

    for pkg in REQUIREMENTS:
        run([python_exe, "-m", "pip", "install", pkg, "--no-warn-script-location", "-q"])

    # 安装 Playwright chromium
    print("  安装 Playwright Chromium ...")
    run([python_exe, "-m", "playwright", "install", "chromium"], check=False)

    print("  ✅ 依赖安装完成")


def step4_copy_source():
    """复制源码到构建目录"""
    log("Step 4: 复制源码")
    app_dir = os.path.join(BUILD_DIR, "app")

    if os.path.isdir(app_dir):
        shutil.rmtree(app_dir)
    os.makedirs(app_dir)

    # 复制所有需要编译的 .py 文件 (从项目根目录)
    for f in COMPILE_FILES:
        src = os.path.join(PROJECT_ROOT, f)
        if f == "license_manager.py":
            src = os.path.join(SCRIPT_DIR, f)  # License 模块在 exe/ 目录
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(app_dir, f))
            print(f"  复制 {f}")
        else:
            print(f"  [警告] 未找到 {src}")

    # 复制 ui.py 并重命名为 _original_ui.py (会被编译)
    ui_src = os.path.join(PROJECT_ROOT, "ui.py")
    if os.path.isfile(ui_src):
        shutil.copy2(ui_src, os.path.join(app_dir, "_original_ui.py"))
        COMPILE_FILES.append("_original_ui.py")
        print(f"  复制 ui.py → _original_ui.py")

    # 复制不编译的入口文件
    for f in KEEP_AS_PY:
        src = os.path.join(SCRIPT_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(app_dir, f))
            print(f"  复制 {f} (保持 .py)")

    # 复制不编译的源文件 (从项目根目录, Nuitka 编译有问题的文件)
    SKIP_COMPILE_FILES = ["browser_payment.py"]
    for f in SKIP_COMPILE_FILES:
        src = os.path.join(PROJECT_ROOT, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(app_dir, f))
            print(f"  复制 {f} (跳过编译)")

    # 复制其他文件
    for f in COPY_FILES:
        src = os.path.join(PROJECT_ROOT, f)
        dst = os.path.join(app_dir, f)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  复制 {f}")
        elif os.path.isdir(src):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            print(f"  复制目录 {f}")

    # 创建数据目录
    data_dir = os.path.join(BUILD_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)

    # 复制配置模板到 data 目录 (强制覆盖, 确保无旧数据)
    cfg_example = os.path.join(PROJECT_ROOT, "config.example.json")
    cfg_dst = os.path.join(data_dir, "config.json")
    if os.path.isfile(cfg_example):
        shutil.copy2(cfg_example, cfg_dst)
        print("  config.example.json -> data/config.json")
    # 清理 app 目录和 data 目录中的运行时数据
    for ext in ("*.db", "*.key"):
        for d in [app_dir, data_dir]:
            for f in glob.glob(os.path.join(d, ext)):
                os.remove(f)
    # 清理 test_outputs (运行时产生的 credentials 文件)
    test_outputs = os.path.join(app_dir, "test_outputs")
    if os.path.isdir(test_outputs):
        shutil.rmtree(test_outputs)
        print("  cleaned test_outputs/")

    print("  ✅ 源码复制完成")


def step4_5_patch_ui():
    """在复制的 _original_ui.py 中注入 Lite 版功能门控"""
    log("Step 4.5: 注入 License 功能门控")
    app_dir = os.path.join(BUILD_DIR, "app")
    ui_path = os.path.join(app_dir, "_original_ui.py")

    if not os.path.isfile(ui_path):
        print("  [跳过] _original_ui.py 不存在")
        return

    with open(ui_path, "r", encoding="utf-8") as f:
        content = f.read()

    patches_applied = 0

    # ── Patch 1: 在文件顶部 import 后注入 tier 读取 ──
    # 找到 "init_db()" 并在其后注入
    tier_inject = '''
# ═══ [EXE版] License 功能门控 ═══
import os as _os
_AGBC_TIER = _os.environ.get("AGBC_LICENSE_TIER", "pro")
'''
    if "init_db()" in content:
        content = content.replace(
            "init_db()",
            "init_db()" + tier_inject,
            1
        )
        patches_applied += 1
        print("  ✅ Patch 1: 注入 tier 读取")

    # ── Patch 2: 修改执行模式选择 — Lite 版只允许 "仅注册" ──
    # 找到流程模式选择的 radio，在其后注入 Lite 限制
    old_flow_mode = '''    # 根据模式派生控制标志
    do_register = flow_mode in ["仅注册", "注册 + 绑卡"]
    do_checkout = flow_mode in ["仅绑卡", "注册 + 绑卡"]
    do_payment = flow_mode in ["仅绑卡", "注册 + 绑卡"]'''

    new_flow_mode = '''    # ═══ [EXE版] Lite 版限制: 只允许 "仅注册" ═══
    if _AGBC_TIER == "lite" and flow_mode != "仅注册":
        flow_mode = "仅注册"
        st.warning("⚠️ 当前为 **Lite 版**，仅支持注册功能。升级到 Pro 版解锁支付功能。")

    # 根据模式派生控制标志
    do_register = flow_mode in ["仅注册", "注册 + 绑卡"]
    do_checkout = flow_mode in ["仅绑卡", "注册 + 绑卡"]
    do_payment = flow_mode in ["仅绑卡", "注册 + 绑卡"]'''

    if old_flow_mode in content:
        content = content.replace(old_flow_mode, new_flow_mode, 1)
        patches_applied += 1
        print("  ✅ Patch 2: 注入 Lite 执行模式限制")
    else:
        print("  ⚠️ Patch 2: 未找到执行模式代码块，请手动检查")

    # (Patch 3 已移除: 标题中的隐式字符串拼接不能替换为表达式)

    with open(ui_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n  共应用 {patches_applied} 个补丁")
    print("  ✅ UI 功能门控注入完成")


def step5_compile_nuitka():
    """用 Nuitka 编译 .py → .pyd"""
    log("Step 5: Nuitka 编译 (源码保护)")
    app_dir = os.path.join(BUILD_DIR, "app")

    # 检查 Nuitka 是否安装
    try:
        run([sys.executable, "-m", "nuitka", "--version"], check=True)
    except Exception:
        print("  [ERROR] Nuitka 未安装! 请先运行: pip install nuitka")
        print("  [提示] 也需要安装 C 编译器，推荐安装 Visual Studio Build Tools")
        raise

    compiled_count = 0
    failed = []

    for f in COMPILE_FILES:
        py_path = os.path.join(app_dir, f)
        if not os.path.isfile(py_path):
            continue

        module_name = f.replace(".py", "")
        print(f"\n  编译 {f} ...")

        try:
            run([
                sys.executable, "-m", "nuitka",
                "--module",                    # 编译为 .pyd 模块
                "--remove-output",             # 清理中间文件
                "--no-pyi-file",               # 不生成 .pyi
                f"--output-dir={app_dir}",     # 输出到 app 目录
                py_path,
            ], cwd=app_dir)

            # 检查 .pyd 是否生成
            pyd_pattern = os.path.join(app_dir, f"{module_name}*.pyd")
            pyd_files = glob.glob(pyd_pattern)
            if pyd_files:
                # 删除原 .py
                os.remove(py_path)
                compiled_count += 1
                print(f"    ✅ {f} → {os.path.basename(pyd_files[0])}")
            else:
                failed.append(f)
                print(f"    ❌ 未生成 .pyd")

        except Exception as e:
            failed.append(f)
            print(f"    ❌ 编译失败: {e}")

    # 清理 Nuitka 中间文件
    for d in glob.glob(os.path.join(app_dir, "*.build")):
        shutil.rmtree(d, ignore_errors=True)
    for d in glob.glob(os.path.join(app_dir, "*.dist")):
        shutil.rmtree(d, ignore_errors=True)

    print(f"\n  编译完成: {compiled_count} 成功, {len(failed)} 失败")
    if failed:
        print(f"  失败文件: {', '.join(failed)}")
        print(f"  [提示] 失败的文件将保留 .py 格式 (未加密)")

    print("  ✅ Nuitka 编译完成")


def step6_compile_launcher():
    """编译启动器为 AGBC.exe"""
    log("Step 6: 编译启动器")
    launcher_src = os.path.join(SCRIPT_DIR, "launcher.py")

    if not os.path.isfile(launcher_src):
        print("  [跳过] launcher.py 不存在")
        return

    try:
        run([
            sys.executable, "-m", "nuitka",
            "--standalone",
            "--onefile",
            "--windows-console-mode=attach",  # 显示控制台
            f"--output-dir={BUILD_DIR}",
            f"--output-filename=AGBC.exe",
            launcher_src,
        ])
        print("  ✅ AGBC.exe 已生成")
    except Exception as e:
        print(f"  ❌ 编译失败: {e}")
        print("  [fallback] 创建批处理启动器...")
        _create_bat_launcher()


def _create_bat_launcher():
    """创建 .bat 启动器"""
    bat_path = os.path.join(BUILD_DIR, "AGBC.bat")
    lines = [
        '@echo off',
        'chcp 65001 >nul 2>&1',
        'title AGBC',
        'echo ================================================',
        'echo   AGBC - ChatGPT Auto Register and Bind Card',
        'echo ================================================',
        'echo.',
        '',
        'cd /d "%~dp0"',
        '',
        'set PYTHON=python\\python.exe',
        'set ENTRY=app\\ui_entry.py',
        'set PORT=8501',
        'set PYTHONNOUSERSITE=1',
        'set PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers',
        '',
        'if not exist "%PYTHON%" (',
        '    echo [ERROR] Python not found: %PYTHON%',
        '    pause',
        '    exit /b 1',
        ')',
        '',
        'echo [Starting] Starting service...',
        'echo [Info] Browser: http://127.0.0.1:%PORT%',
        'echo [Info] Close this window to stop the service',
        'echo.',
        '',
        'REM Open browser after 3 second delay',
        'start /B cmd /c "ping -n 4 127.0.0.1 >nul & start http://127.0.0.1:%PORT%"',
        '',
        'REM Run Streamlit (foreground, no file watcher)',
        '"%PYTHON%" -m streamlit run "%ENTRY%" --server.port %PORT% --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false --server.fileWatcherType none',
        '',
        'echo.',
        'echo [Stopped] Service has stopped.',
        'pause',
    ]
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write('\n'.join(lines) + '\n')
    print("  [OK] AGBC.bat created")


def step7_package():
    """打包为最终分发目录"""
    log("Step 7: 打包")

    os.makedirs(DIST_DIR, exist_ok=True)

    # 确保 bat 启动器存在
    bat_path = os.path.join(BUILD_DIR, "AGBC.bat")
    if not os.path.isfile(bat_path):
        _create_bat_launcher()

    print(f"\n  构建目录: {BUILD_DIR}")
    print(f"  最终结构:")
    print(f"    {BUILD_DIR}/")
    print(f"    ├── python/          (嵌入式 Python + 依赖)")
    print(f"    ├── app/             (编译后的应用代码)")
    print(f"    ├── data/            (配置 + 数据库)")
    print(f"    ├── AGBC.exe/.bat    (启动器)")
    print(f"    └── (Inno Setup → 生成安装包)")

    print("\n  ✅ 打包完成!")
    print(f"\n  下一步: 运行 Inno Setup 编译 installer.iss 生成安装包")
    print(f"  或直接将 {BUILD_DIR} 目录压缩分发")


def main():
    parser = argparse.ArgumentParser(description="AGBC 构建脚本")
    parser.add_argument("--skip-download", action="store_true", help="跳过 Python 下载")
    parser.add_argument("--skip-compile", action="store_true", help="跳过 Nuitka 编译 (保留 .py)")
    parser.add_argument("--skip-installer", action="store_true", help="跳过 Inno Setup")
    parser.add_argument("--clean", action="store_true", help="清理构建目录后退出")
    args = parser.parse_args()

    if args.clean:
        log("清理构建目录")
        if os.path.isdir(BUILD_DIR):
            shutil.rmtree(BUILD_DIR)
            print("  已清理 build/")
        if os.path.isdir(DIST_DIR):
            shutil.rmtree(DIST_DIR)
            print("  已清理 dist/")
        return

    log("AGBC 自动化构建")
    print(f"  项目目录: {PROJECT_ROOT}")
    print(f"  构建目录: {BUILD_DIR}")
    print(f"  输出目录: {DIST_DIR}")

    if not args.skip_download:
        step1_download_python()
        step2_install_pip()
        step3_install_deps()

    step4_copy_source()
    step4_5_patch_ui()

    if not args.skip_compile:
        step5_compile_nuitka()
        step6_compile_launcher()
    else:
        print("\n  [跳过] Nuitka 编译")
        _create_bat_launcher()

    step7_package()

    # 安装 Playwright 浏览器到 build/browsers/ (自包含)
    browsers_dir = os.path.join(BUILD_DIR, "browsers")
    if not os.path.isdir(browsers_dir) or not glob.glob(os.path.join(browsers_dir, "chromium-*")):
        log("Step 8: 安装 Playwright Chromium")
        embed_python = os.path.join(BUILD_DIR, "python", "python.exe")
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_dir
        env["PYTHONNOUSERSITE"] = "1"
        subprocess.run([embed_python, "-m", "playwright", "install", "chromium"], env=env, check=True)
        print("  [OK] Chromium installed to build/browsers/")
    else:
        print("  [SKIP] Playwright Chromium already in build/browsers/")

    log("构建完成! 🎉")


if __name__ == "__main__":
    main()
