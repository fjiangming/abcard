"""
集成测试 - 完整流程: 注册 + Checkout + Confirm (用 Stripe 测试卡)
"""
import logging
import json
import sys
import traceback

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("integration_full")

from config import Config, CardInfo, BillingInfo
from mail_provider import MailProvider
from auth_flow import AuthFlow
from payment_flow import PaymentFlow
from logger import ResultStore

store = ResultStore(output_dir="test_outputs")

cfg = Config()
# 使用 Stripe 标准测试卡
cfg.card = CardInfo(
    number="4242424242424242",
    cvc="123",
    exp_month="12",
    exp_year="2030",
)
cfg.billing = BillingInfo(
    name="Test User",
    email="",  # 会用注册邮箱填充
    country="JP",
    currency="JPY",
    address_line1="1-1-1 Shibuya",
    address_state="Tokyo",
)

af = AuthFlow(cfg)
steps = {}

# ── 阶段 1: 注册 ──
logger.info("=" * 60)
logger.info("阶段 1: 注册")
logger.info("=" * 60)

mp = MailProvider(
    worker_domain=cfg.mail.worker_domain,
    admin_token=cfg.mail.admin_token,
    email_domain=cfg.mail.email_domain,
)

try:
    auth_result = af.run_register(mp)
    steps["register"] = "✅"
    logger.info(f"注册成功: {auth_result.email}")
    # 保存凭证
    store.save_credentials(auth_result.to_dict())
    store.append_credentials_csv(auth_result.to_dict())
except Exception as e:
    steps["register"] = f"❌ {e}"
    logger.error(f"注册失败: {e}")
    traceback.print_exc()
    store.save_debug_info({"error": str(e), "steps": steps})
    sys.exit(1)

# ── 阶段 2: 完整支付流程 ──
logger.info("")
logger.info("=" * 60)
logger.info("阶段 2: 完整支付 (checkout → fingerprint → confirm)")
logger.info("=" * 60)

cfg.billing.email = auth_result.email
pf = PaymentFlow(cfg, auth_result)
# 共享 cookie session
pf.session = af.session

try:
    payment_result = pf.run_payment()
    steps["checkout"] = f"✅ {pf.result.checkout_session_id[:30]}..." if pf.result.checkout_session_id else "❌"
    steps["confirm_status"] = pf.result.confirm_status
    steps["confirm_success"] = "✅" if payment_result.success else f"❌ {payment_result.error}"

    logger.info(f"支付结果:")
    logger.info(f"  状态码: {payment_result.confirm_status}")
    logger.info(f"  成功: {payment_result.success}")
    logger.info(f"  错误: {payment_result.error}")
    logger.info(f"  响应: {json.dumps(payment_result.confirm_response, indent=2, ensure_ascii=False)[:500]}")
except Exception as e:
    steps["payment"] = f"❌ {e}"
    logger.error(f"支付流程异常: {e}")
    traceback.print_exc()

# ── 测试报告 ──
logger.info("")
logger.info("=" * 60)
logger.info("完整集成测试报告")
logger.info("=" * 60)
for step, result in steps.items():
    logger.info(f"  {step}: {result}")

# 保存结果
test_result = {
    "auth": auth_result.to_dict(),
    "payment": pf.result.to_dict(),
    "steps": steps,
}
path = store.save_result(test_result, "full_integration")
store.append_history(
    email=auth_result.email,
    status="full_test",
    checkout_session_id=pf.result.checkout_session_id,
    payment_status=pf.result.confirm_status,
    error=pf.result.error,
    detail_file=path,
)
logger.info(f"结果已保存: {path}")
