# tool/ — 辅助工具脚本

本目录存放项目的辅助运维工具和一次性补丁脚本，**非项目启动必需**。

## 脚本说明

| 脚本 | 用途 |
|------|------|
| `card_format_converter.py` | 卡片信息格式转换：将原始卡片数据（空格分隔、含多余字段）转换为统一的 `key: value` 格式，支持批量转换、文件/stdin/剪贴板输入 |
| `backfill_result_json.py` | 回填脚本：将 `test_outputs/credentials_*.json` 中的凭证数据同步到 `data.db` 的 `executions` 表，补全缺失字段并关联兑换码 |
| `sync_status_checker.py` | NewAPI 同步状态校验：通过 NewAPI 搜索接口检查每个账号是否已同步，更新本地 JSON 和数据库中的 `synced_to_newapi` 状态，并生成校验报告 |
| `setup_env.sh` | Linux/WSL 环境一键初始化：安装 Python 依赖和 Chromium 系统库、启动 Xvfb 虚拟显示器、验证 Chrome 可运行、自动检测 WSL 代理并提示配置 |
| `setup_env.ps1` | Windows PowerShell 环境初始化：安装依赖、验证 Chrome、测试网络连通性 |

## 使用方式

```bash
# 卡片格式转换（三种输入方式）
python tool/card_format_converter.py cards.txt                 # 从文件读取
python tool/card_format_converter.py cards.txt -o output.txt   # 输出到文件
python tool/card_format_converter.py -c                        # 从剪贴板读取
echo "..." | python tool/card_format_converter.py              # 管道输入
python tool/card_format_converter.py                           # 交互式粘贴（Ctrl+Z 结束）

# 回填凭证到数据库（需指定兑换码）
python tool/backfill_result_json.py --code XXXX-XXXX-XXXX

# 校验 NewAPI 同步状态（支持 dry-run 预览）
python tool/sync_status_checker.py [--dry-run]

# 环境初始化（新机器部署时执行一次）
bash tool/setup_env.sh                                         # Linux/WSL
powershell -ExecutionPolicy Bypass -File tool/setup_env.ps1    # Windows
```

## WSL 代理配置

WSL 中运行绑卡功能需要通过 Windows 宿主机的代理访问外网（Stripe、Cloudflare 等）。

**步骤：**

1. **Windows 代理软件开启「允许局域网连接（Allow LAN）」**

2. **在 WSL 中获取 Windows 宿主 IP：**
   ```bash
   ip route show default | awk '{print $3}'
   # 输出示例: 172.21.16.1
   ```
   > ⚠️ 不要用 `/etc/resolv.conf` 中的 `nameserver`，那个不一定是宿主 IP

3. **验证代理连通性：**
   ```bash
   curl -sI --max-time 5 --proxy http://<IP>:<端口> https://js.stripe.com/v3/ | head -3
   # 返回 HTTP/2 200 即为成功
   ```

4. **在 UI「代理」栏填入：** `http://<IP>:<端口>`，例如 `http://172.21.16.1:7897`

> `setup_env.sh` 会自动执行步骤 2-3 并提示可用的代理地址。
