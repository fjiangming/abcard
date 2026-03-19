"""
Windows 桌面启动器 — 启动 Streamlit 服务 + 自动打开浏览器

功能:
  1. 启动 Streamlit 服务 (使用嵌入式 Python)
  2. 等待服务就绪
  3. 自动打开默认浏览器访问 localhost
  4. 保持运行直到用户关闭控制台窗口

此文件在构建时会被 Nuitka 编译为 AGBC.exe
"""
import os
import subprocess
import sys
import time
import socket
import webbrowser
import signal

# 配置
PORT = 8501
HOST = "127.0.0.1"


def get_base_dir():
    """获取应用根目录"""
    if getattr(sys, "frozen", False):
        # 编译后的 exe
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def get_python_path():
    """获取嵌入式 Python 路径"""
    base = get_base_dir()
    python_dir = os.path.join(base, "python")

    # 嵌入式 Python
    python_exe = os.path.join(python_dir, "python.exe")
    if os.path.isfile(python_exe):
        return python_exe

    # fallback: 系统 Python
    return sys.executable


def is_port_open(host, port):
    """检查端口是否可用"""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def main():
    base_dir = get_base_dir()
    app_dir = os.path.join(base_dir, "app")
    python_exe = get_python_path()

    # 入口文件
    entry_file = os.path.join(app_dir, "ui_entry.py")
    if not os.path.isfile(entry_file):
        entry_file = os.path.join(app_dir, "ui.py")

    if not os.path.isfile(entry_file):
        print(f"[ERROR] 找不到入口文件: {entry_file}")
        input("按回车键退出...")
        return

    print("=" * 50)
    print("  AGBC — ChatGPT Auto Register & Bind Card")
    print("=" * 50)
    print(f"  Python: {python_exe}")
    print(f"  入口:   {entry_file}")
    print(f"  端口:   {PORT}")
    print("=" * 50)
    print()

    # 确保数据目录存在
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # 启动 Streamlit
    env = os.environ.copy()
    env["STREAMLIT_SERVER_PORT"] = str(PORT)
    env["STREAMLIT_SERVER_ADDRESS"] = HOST
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    cmd = [
        python_exe, "-m", "streamlit", "run", entry_file,
        "--server.port", str(PORT),
        "--server.address", HOST,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]

    print(f"[启动] 正在启动服务...")
    proc = subprocess.Popen(
        cmd,
        cwd=app_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # 等待服务就绪
    print(f"[等待] 等待服务启动 (端口 {PORT})...")
    for i in range(30):  # 最多等 30 秒
        if proc.poll() is not None:
            # 进程已退出
            output = proc.stdout.read().decode(errors="ignore") if proc.stdout else ""
            print(f"[ERROR] 服务启动失败:\n{output}")
            input("按回车键退出...")
            return
        if is_port_open(HOST, PORT):
            break
        time.sleep(1)
        if (i + 1) % 5 == 0:
            print(f"  ... 已等待 {i+1} 秒")
    else:
        print("[ERROR] 服务启动超时")
        proc.kill()
        input("按回车键退出...")
        return

    url = f"http://{HOST}:{PORT}"
    print(f"\n[就绪] 服务已启动! 浏览器访问: {url}")
    print("[提示] 关闭此窗口将停止服务\n")

    # 打开浏览器
    webbrowser.open(url)

    # 持续输出日志
    try:
        while proc.poll() is None:
            line = proc.stdout.readline()
            if line:
                print(line.decode(errors="ignore"), end="")
            else:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[停止] 正在关闭服务...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[已停止]")


if __name__ == "__main__":
    main()
