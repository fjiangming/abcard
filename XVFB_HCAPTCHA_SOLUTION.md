# ChatGPT Business 自动开通方案

## 方案概述

通过 **API 注册 + Xvfb 虚拟显示 + Playwright CDP + Stripe 自动填表 + hCaptcha 自动点击** 实现 ChatGPT Business 套餐全自动开通。

**首月免费**: 使用 `team-1-month-free` 促销码，5 席位首月 US$0.00  
**成功率**: hCaptcha 自动点击 100% 通过（SwiftShader 环境）  
**全流程耗时**: ~60-90 秒

---

## 完整流程

```
+===========================================================================+
|                        ChatGPT Business 自动开通流程                        |
+===========================================================================+

  ┌─────────────────────────────────────────────────────────────────────┐
  │  Phase 1: 账号注册 (API)                                           │
  │                                                                     │
  │  MailProvider ──> 创建临时邮箱 (mkai.de5.net)                       │
  │       │                                                             │
  │       v                                                             │
  │  AuthFlow.run_register()                                            │
  │    1. CSRF Token                                                    │
  │    2. OAuth 授权地址                                                │
  │    3. OAuth 初始化 ──> device_id                                    │
  │    4. Sentinel Token                                                │
  │    5. 提交注册邮箱                                                  │
  │    6. 发送 OTP ──> 邮箱接收                                         │
  │    7. 验证 OTP                                                      │
  │    8. 创建账户                                                      │
  │    9. 重定向链                                                      │
  │   10. 获取 session_token + access_token                             │
  │                                                                     │
  │  输出: email, session_token, access_token, device_id                │
  └─────────────────────────┬───────────────────────────────────────────┘
                            │
                            v
  ┌─────────────────────────────────────────────────────────────────────┐
  │  Phase 2: 创建 Checkout Session (API)                               │
  │                                                                     │
  │  POST /backend-api/payments/checkout                                │
  │  ┌─────────────────────────────────────────┐                        │
  │  │ {                                       │                        │
  │  │   "plan_name": "chatgptteamplan",       │                        │
  │  │   "team_plan_data": {                   │                        │
  │  │     "workspace_name": "...",            │                        │
  │  │     "price_interval": "month",          │                        │
  │  │     "seat_quantity": 5                  │                        │
  │  │   },                                    │                        │
  │  │   "billing_details": {                  │                        │
  │  │     "country": "US",                    │                        │
  │  │     "currency": "USD"                   │                        │
  │  │   },                                    │                        │
  │  │   "promo_campaign": {                   │                        │
  │  │     "promo_campaign_id":                │  <── 关键！免费试用     │
  │  │       "team-1-month-free",              │                        │
  │  │     "is_coupon_from_query_param": false  │                        │
  │  │   },                                    │                        │
  │  │   "checkout_ui_mode": "custom"          │                        │
  │  │ }                                       │                        │
  │  └─────────────────────────────────────────┘                        │
  │                                                                     │
  │  返回: checkout_session_id, publishable_key, client_secret          │
  │                                                                     │
  │  Checkout 页面定价:                                                 │
  │    小计:    US$150.00  (5 x $30/席位/月)                            │
  │    折扣:  -US$150.00  (US$150.00 优惠，持续 1 个月)                 │
  │    应付:    US$0.00    ✅                                           │
  └─────────────────────────┬───────────────────────────────────────────┘
                            │
                            v
  ┌─────────────────────────────────────────────────────────────────────┐
  │  Phase 3: 浏览器支付 (Playwright CDP + Xvfb + SwiftShader)         │
  │                                                                     │
  │  3a. 启动 Chrome (有头模式 on Xvfb)                                │
  │      Chrome 145 + SwiftShader + Proxy                               │
  │      CDP connect_over_cdp (navigator.webdriver = false)             │
  │                                                                     │
  │  3b. Cloudflare 通关                                                │
  │      GET chatgpt.com/ ──> 等待 title != "请稍候"                    │
  │                                                                     │
  │  3c. 加载 Checkout 页面                                             │
  │      GET /checkout/openai_llc/{cs_id}                               │
  │      等待 Stripe Payment Element + Address Element 加载             │
  │                                                                     │
  │  3d. 填写卡片信息 (Stripe Payment iframe)                          │
  │      ┌──────────────────────────────────────┐                       │
  │      │  iframe: elements-inner-payment      │                       │
  │      │  ┌────────────────────────────────┐  │                       │
  │      │  │ Card number  [4242...4242]     │  │ ← y+25 点击 + 键盘   │
  │      │  ├────────────────┬───────────────┤  │                       │
  │      │  │ MM/YY [03/32]  │ CVC [667]     │  │ ← Stripe 自动跳转    │
  │      │  └────────────────┴───────────────┘  │                       │
  │      └──────────────────────────────────────┘                       │
  │      方法: page.mouse.click(绝对坐标) + page.keyboard.type()       │
  │      Stripe 自动在卡号完成后跳到过期日，过期日后跳到 CVC             │
  │                                                                     │
  │  3e. 填写账单地址 (Stripe Address iframe)                          │
  │      ┌──────────────────────────────────────┐                       │
  │      │  iframe: elements-inner-address      │                       │
  │      │  可见: name, addressLine1            │ ← 绝对坐标点击 + 键盘 │
  │      │  隐藏: city, state, zip              │ ← frame.evaluate()    │
  │      │        (超出 iframe 可视区域)        │   nativeInputValueSetter│
  │      └──────────────────────────────────────┘                       │
  │                                                                     │
  │  3f. 提交支付                                                       │
  │      button[type="submit"].click()                                  │
  │                                                                     │
  │  3g. hCaptcha 自动处理                                              │
  │      Stripe handleNextAction() 触发 hCaptcha                       │
  │      ┌─────────────────────────────────────┐                        │
  │      │ checkout.stripe.com                 │                        │
  │      │  └─ js.stripe.com/hcaptcha-inner    │                        │
  │      │     └─ stripecdn.com/HCaptcha       │                        │
  │      │        └─ newassets.hcaptcha.com     │                        │
  │      │           └─ #checkbox ← 自动点击 ✅│                        │
  │      └─────────────────────────────────────┘                        │
  │      SwiftShader 让 checkbox 点击通过 (无 challenge 图片验证)       │
  │                                                                     │
  │  3h. 等待结果                                                       │
  │      成功: "订阅已激活" / "Thank you" / URL 跳转                    │
  │      失败: "Card declined" / "支付被拒"                             │
  └─────────────────────────────────────────────────────────────────────┘
```

---

## 技术细节

### 促销码对照

| 促销 ID | 效果 | 来源 |
|---------|------|------|
| `team-1-month-free` | **首月 $0** (官方免费试用) | 浏览器 "领取免费试用" 按钮 ✅ |
| `team0dollar` | ~~无效~~ (旧版，不再生效) | 旧代码/URL 参数 ❌ |
| `business_free_trial` | 定价配置中显示 enabled | 未被前端使用 ❌ |

### 定价配置 (US)

来自 `/backend-api/checkout_pricing_config/configs/US`:
```json
{
  "business": {
    "month": {"amount": 30.0, "tax": "exclusive"},
    "year":  {"amount": 25.0, "tax": "exclusive"}
  },
  "promos": {
    "business_free_trial": {"enabled": true, "amount": 0.0},
    "business_one_dollar": {"enabled": true, "amount": 1.0}
  }
}
```

### Stripe iframe 填写技术

**卡片填写** (Payment Element):
- iframe URL: `js.stripe.com/v3/elements-inner-payment-*.html`
- 布局: 卡号在上 (y=4-74), 过期日/CVC 在下 (y=86)
- 方法: 点击 `(iframe_x+80, iframe_y+25)` → 键盘输入 → Stripe 自动跳转
- **不要用 Tab** — Stripe 在卡号完成后自动跳到下一个字段

**地址填写** (Address Element):
- iframe URL: `js.stripe.com/v3/elements-inner-address-*.html`
- 可见字段: name, addressLine1 → 绝对坐标点击 + `page.keyboard.type()`
- 隐藏字段: city/state/zip → `frame.evaluate()` + `nativeInputValueSetter`
- iframe 高度仅 243px，但 city/state/zip 在 y=334-416 处 (超出可视区)

### hCaptcha 绕过原理

```
                    ┌─────────────────┐
                    │  Stripe Payment │
                    │  handleNextAction│
                    └────────┬────────┘
                             │
                    ┌────────v────────┐
                    │  Invisible      │
                    │  hCaptcha       │
                    │  (自动评估)     │
                    └────────┬────────┘
                             │
                     检测浏览器指纹
                     GPU / Canvas / WebGL
                             │
              ┌──────────────┴──────────────┐
              │                             │
         [真实 GPU]                 [SwiftShader/Xvfb]
              │                             │
         自动通过                    Invisible 失败
              │                             │
              │                    ┌────────v────────┐
              │                    │  Visible        │
              │                    │  Checkbox       │
              │                    │  出现           │
              │                    └────────┬────────┘
              │                             │
              │                    自动点击 checkbox
              │                             │
              │                    ┌────────v────────┐
              │                    │  Challenge?     │
              │                    └────────┬────────┘
              │                             │
              │                    SwiftShader 环境
              │                    指纹够好 → 无 challenge
              │                             │
              └──────────────┬──────────────┘
                             │
                    ┌────────v────────┐
                    │  hCaptcha 通过  │
                    │  Stripe 继续    │
                    └─────────────────┘
```

---

## 环境要求

```bash
# Xvfb 虚拟显示
sudo apt-get install -y xvfb

# Chrome for Testing (Playwright 内置)
~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome  # Chrome 145

# Python 依赖
pip install playwright curl_cffi

# 代理 (US IP 必需)
http://proxy:port  # 必须是美国 IP
```

### Chrome 启动参数

```
--use-gl=angle                    # ANGLE GL
--use-angle=swiftshader-webgl     # SwiftShader 软件 WebGL
--enable-unsafe-swiftshader       # 允许 SwiftShader
--no-sandbox                      # WSL/容器环境
--remote-debugging-port=PORT      # CDP 连接
--proxy-server=http://proxy:port  # 代理
--window-size=1920,1080           # 窗口大小
```

**不要使用**:
- `--headless=new` — HeadlessChrome UA 被检测
- `--disable-gpu` — 禁用 WebGL 降低指纹质量

---

## 使用方法

### Quick Start

```python
import os, subprocess
from browser_payment import BrowserPayment
from auth_flow import AuthFlow
from mail_provider import MailProvider
from config import Config

# 1. 启动 Xvfb
subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac"])
os.environ["DISPLAY"] = ":99"

# 2. 注册账号
cfg = Config()
cfg.proxy = "http://proxy:port"
af = AuthFlow(config=cfg)
mp = MailProvider(cfg.mail.worker_domain, cfg.mail.admin_token, cfg.mail.email_domain)
auth = af.run_register(mp)

# 3. 运行支付
bp = BrowserPayment(proxy=cfg.proxy, headless=False, slow_mo=80)
result = bp.run_full_flow(
    session_token=auth.session_token,
    access_token=auth.access_token,
    device_id=auth.device_id,
    card_number="4242424242424242",
    card_exp_month="03",
    card_exp_year="32",
    card_cvc="123",
    billing_name="John Doe",
    billing_country="US",
    billing_zip="63640",
    billing_line1="863 Potosi Street",
    billing_city="Farmington",
    billing_state="MO",
    billing_currency="USD",
    workspace_name="MyWorkspace",
    chatgpt_proxy=cfg.proxy,
)

print(f"Success: {result['success']}")
```

### Streamlit UI

```bash
DISPLAY=:99 streamlit run ui.py --server.port 8503
```

---

## 关键文件

| 文件 | 功能 |
|------|------|
| `browser_payment.py` | 核心: Checkout 创建 + 浏览器支付 + hCaptcha |
| `auth_flow.py` | API 注册账号 (10 步) |
| `mail_provider.py` | 临时邮箱 + OTP 接收 |
| `config.py` | 配置: 邮箱/卡片/账单/代理 |
| `ui.py` | Streamlit Web UI |
| `http_client.py` | curl_cffi HTTP 客户端 |

---

## 故障排除

| 问题 | 原因 | 解决 |
|------|------|------|
| Stripe 字段 "incomplete" | 点击坐标错误 | 检查 iframe box + y+25 |
| hCaptcha 超时 | Chrome headless 模式 | 改用 Xvfb 有头模式 |
| Cloudflare 阻断 | 缺少 CF cookie | 先访问 chatgpt.com/ 通关 |
| 支付 $150 而非 $0 | 使用了旧 promo | 改为 `team-1-month-free` |
| 地址字段为空 | 字段超出 iframe 可视区 | 用 `frame.evaluate()` |
| Tab 不跳转 | 跨 iframe 键盘事件不传递 | 不用 Tab，Stripe 自动跳转 |
