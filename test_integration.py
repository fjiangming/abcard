"""
集成测试 - 注册 + Checkout Session 创建
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
logger = logging.getLogger("integration")

from config import Config
from mail_provider import MailProvider
from auth_flow import AuthFlow
from payment_flow import PaymentFlow
from logger import ResultStore

store = ResultStore(output_dir="test_outputs")

cfg = Config()
af = AuthFlow(cfg)

# ── 阶段 1: 注册 ──
logger.info("=" * 60)
logger.info("阶段 1: 注册流程")
logger.info("=" * 60)

mp = MailProvider(
    worker_domain=cfg.mail.worker_domain,
    admin_token=cfg.mail.admin_token,
    email_domain=cfg.mail.email_domain,
)

steps = {}

try:
    auth_result = af.run_register(mp)
    steps["register"] = "✅ 成功"
    logger.info(f"注册完成: {auth_result.email}")
    logger.info(f"session_token 长度: {len(auth_result.session_token)}")
    logger.info(f"access_token 长度: {len(auth_result.access_token)}")
    logger.info(f"device_id: {auth_result.device_id}")

    # 保存凭证
    cred_path = store.save_credentials(auth_result.to_dict())
    logger.info(f"凭证已保存: {cred_path}")

except Exception as e:
    steps["register"] = f"❌ {e}"
    logger.error(f"注册失败: {e}")
    traceback.print_exc()
    # 保存失败信息
    store.save_debug_info({"error": str(e), "steps": steps})
    sys.exit(1)

# ── 阶段 2: Checkout Session ──
logger.info("")
logger.info("=" * 60)
logger.info("阶段 2: 创建 Checkout Session")
logger.info("=" * 60)

cfg.billing.email = auth_result.email
pf = PaymentFlow(cfg, auth_result)

# 使用注册流程的同一个 session (共享 cookie)
pf.session = af.session

try:
    cs_id = pf.create_checkout_session()
    steps["checkout"] = f"✅ {cs_id[:30]}..."
    logger.info(f"Checkout Session ID: {cs_id}")
except Exception as e:
    steps["checkout"] = f"❌ {e}"
    logger.error(f"Checkout 创建失败: {e}")
    traceback.print_exc()

# ── 阶段 3: Stripe 指纹 ──
logger.info("")
logger.info("=" * 60)
logger.info("阶段 3: Stripe 设备指纹")
logger.info("=" * 60)

try:
    pf.fetch_stripe_fingerprint()
    fp = pf.fingerprint.get_params()
    steps["stripe_fingerprint"] = f"✅ guid={fp['guid'][:20]}..."
    logger.info(f"指纹: {fp}")
except Exception as e:
    steps["stripe_fingerprint"] = f"❌ {e}"
    logger.error(f"指纹获取失败: {e}")

# ── 测试报告 ──
logger.info("")
logger.info("=" * 60)
logger.info("集成测试报告")
logger.info("=" * 60)

for step, result in steps.items():
    logger.info(f"  {step}: {result}")

# 保存完整测试结果
test_result = {
    "auth": auth_result.to_dict(),
    "steps": steps,
    "checkout_session_id": pf.result.checkout_session_id,
    "stripe_fingerprint": pf.fingerprint.get_params() if hasattr(pf, 'fingerprint') else {},
}
path = store.save_result(test_result, "integration_test")
logger.info(f"测试结果已保存: {path}")

# 写入历史
store.append_history(
    email=auth_result.email,
    status="integration_test",
    checkout_session_id=pf.result.checkout_session_id,
    detail_file=path,
)
