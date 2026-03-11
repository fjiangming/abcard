# 自动化绑卡支付

ChatGPT Team 自动注册 + Stripe 绑卡支付的协议直连实现。

## 核心链路

```
注册/登录
  CSRF → auth_url → OAuth init → Sentinel → signup → OTP → verify → create_account → redirect → session
                                                                                                    ↓
支付                                                                                           access_token
  POST /backend-api/payments/checkout → checkout_session_id
  GET  m.stripe.com/6                 → guid / muid / sid
  POST /v1/payment_pages/{cs}/confirm → 支付确认
```

## 项目结构

```
auto_bindcard/
├── config.py              # 配置管理
├── config.example.json    # 配置模板
├── http_client.py         # HTTP 客户端 (curl_cffi TLS 指纹 + requests 降级)
├── mail_provider.py       # 临时邮箱创建 & OTP 获取
├── auth_flow.py           # 注册/登录协议流 (10 步)
├── stripe_fingerprint.py  # Stripe 设备指纹 (guid/muid/sid)
├── payment_flow.py        # 支付主链路 (checkout → tokenize → confirm)
├── logger.py              # 日志 & 结果持久化
├── main.py                # 主入口
├── test_all.py            # 单元测试 (23 用例)
├── requirements.txt       # 依赖
├── TEST_REPORT.md         # 测试报告
├── logs/                  # 运行日志 (自动生成)
└── outputs/               # 结果输出 (自动生成)
    ├── history.csv        # 运行历史
    ├── success_*.json     # 成功结果
    ├── failed_*.json      # 失败结果
    └── credentials_*.json # 认证凭证
```

## 安装

```bash
pip install -r requirements.txt
```

依赖:
- `curl_cffi` — TLS 指纹模拟（绕过 Cloudflare）
- `requests` — 降级 HTTP 客户端

## 使用

### 1. 配置

复制配置模板并填写:

```bash
cp config.example.json config.json
```

配置项:

| 字段 | 说明 |
|------|------|
| `mail.*` | 临时邮箱 API 配置 |
| `card.*` | 信用卡信息 (number/cvc/exp_month/exp_year) |
| `billing.*` | 账单地址 (name/country/currency 等) |
| `team_plan.*` | 团队计划 (plan_name/workspace_name/seat_quantity) |
| `proxy` | HTTP 代理 (可选) |
| `session_token` | 已有 session token (跳过注册时使用) |
| `access_token` | 已有 access token (跳过注册时使用) |
| `device_id` | 设备 ID (可选) |

### 2. 全流程 (注册 + 支付)

```bash
python main.py --config config.json
```

### 3. 仅支付 (已有凭证)

```bash
python main.py --config config.json --skip-register
```

需要在 config.json 中提供 `session_token` 和 `access_token`。

### 4. 交互式卡信息输入

```bash
python main.py --config config.json --interactive
```

### 5. 调试模式

```bash
python main.py --config config.json --debug
```

## 运行测试

```bash
python -m pytest test_all.py -v
```

## 输出说明

### 日志

- `logs/run_YYYYMMDD_HHMMSS.log` — 含完整 DEBUG 级别日志
- 终端显示 INFO 级别

### 结果文件

- `outputs/history.csv` — 每次运行追加一行记录
- `outputs/success_*.json` — 支付成功的完整结果
- `outputs/failed_*.json` — 支付失败的完整结果
- `outputs/credentials_*.json` — 注册获得的认证凭证

### history.csv 字段

| 字段 | 说明 |
|------|------|
| timestamp | 运行时间 |
| email | 注册邮箱 |
| status | success / failed / register_only |
| checkout_session_id | Stripe checkout session |
| payment_status | HTTP 状态码 |
| error | 错误信息 |
| detail_file | 详情 JSON 路径 |

## 已知限制

1. **Cloudflare 绕过**: 使用 curl_cffi TLS 指纹模拟，大部分场景可通过。如 CF 防护升级导致 403，需更新 impersonate 参数或改用其他方案。
2. **3DS 验证**: 如果卡触发 3D Secure 验证，当前版本无法自动完成，会返回 `requires_3ds_verification`。
3. **Stripe JS Build Hash**: `stripe_build_hash` 会随 Stripe 更新，需要定期从最新 stripe.js 中提取。
4. **guid 真实性**: `m.stripe.com/6` 获取的 guid 最可靠。模拟生成的 guid 容易触发风控。
