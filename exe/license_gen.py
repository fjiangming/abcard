"""
License Key 生成工具 — 管理员专用 (不分发给买家)

用法:
  首次使用: 生成 RSA 密钥对
     python license_gen.py --gen-keys

  让买家运行 "获取机器码" 功能, 获取 machine_id, 然后:
     python license_gen.py --tier pro --machine-id abc123def456...
     python license_gen.py --tier lite --machine-id abc123def456...

  将生成的 License Key 发送给买家

  验证 License Key:
     python license_gen.py --verify <key>

  获取本机机器码 (测试用):
     python license_gen.py --show-machine-id

安全说明:
  - private_key.pem 是 RSA 私钥，切勿泄露！
  - 此文件和私钥不随产品分发
  - 客户端 (license_manager.py) 只包含公钥，无法伪造 License
"""
import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime

import rsa

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from license_manager import get_machine_id, verify_license

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRIVATE_KEY_FILE = os.path.join(SCRIPT_DIR, "private_key.pem")
MANAGER_FILE = os.path.join(SCRIPT_DIR, "license_manager.py")


def gen_keys():
    """生成 RSA-2048 密钥对，并自动将公钥嵌入 license_manager.py"""
    if os.path.isfile(PRIVATE_KEY_FILE):
        print(f"\n⚠️  私钥文件已存在: {PRIVATE_KEY_FILE}")
        confirm = input("  重新生成将使所有已发放的 License 失效，确认? (y/N): ").strip()
        if confirm.lower() != "y":
            print("  已取消")
            return

    print("\n生成 RSA-2048 密钥对...")
    pub_key, priv_key = rsa.newkeys(2048)

    # 保存私钥
    priv_pem = priv_key.save_pkcs1()
    with open(PRIVATE_KEY_FILE, "wb") as f:
        f.write(priv_pem)
    print(f"  ✅ 私钥已保存: {PRIVATE_KEY_FILE}")
    print(f"  ⚠️  请妥善保管私钥，切勿泄露！")

    # 将公钥嵌入 license_manager.py
    pub_pem = pub_key.save_pkcs1()
    _embed_public_key(pub_pem)

    print(f"\n{'='*60}")
    print(f"  RSA 密钥对生成完成!")
    print(f"  私钥: {PRIVATE_KEY_FILE} (管理员保管)")
    print(f"  公钥: 已嵌入 license_manager.py (随产品分发)")
    print(f"{'='*60}\n")


def _embed_public_key(pub_pem: bytes):
    """将公钥 PEM 自动写入 license_manager.py 的 _RSA_PUBLIC_KEY_PEM 常量"""
    if not os.path.isfile(MANAGER_FILE):
        print(f"  ❌ 未找到 {MANAGER_FILE}")
        return

    with open(MANAGER_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配 _RSA_PUBLIC_KEY_PEM = b"""...""" 块
    pattern = r'_RSA_PUBLIC_KEY_PEM = b""".*?"""'
    replacement = f'_RSA_PUBLIC_KEY_PEM = b"""{pub_pem.decode()}"""'

    new_content, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
    if count == 0:
        print(f"  ❌ 未找到 _RSA_PUBLIC_KEY_PEM 常量，请手动替换")
        print(f"  公钥 PEM:\n{pub_pem.decode()}")
        return

    with open(MANAGER_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"  ✅ 公钥已嵌入 {MANAGER_FILE}")


def _load_private_key() -> rsa.PrivateKey:
    """加载 RSA 私钥"""
    if not os.path.isfile(PRIVATE_KEY_FILE):
        print(f"\n❌ 私钥文件不存在: {PRIVATE_KEY_FILE}")
        print(f"   请先运行: python license_gen.py --gen-keys")
        sys.exit(1)

    with open(PRIVATE_KEY_FILE, "rb") as f:
        priv_pem = f.read()
    return rsa.PrivateKey.load_pkcs1(priv_pem)


def generate_license(tier: str, machine_id: str) -> str:
    """
    生成 License Key (RSA 私钥签名)

    Args:
        tier: "lite" 或 "pro"
        machine_id: 目标机器 ID (由买家提供)

    Returns:
        License Key 字符串
    """
    if tier not in ("lite", "pro"):
        raise ValueError(f"无效的 tier: {tier}, 必须是 'lite' 或 'pro'")

    priv_key = _load_private_key()

    payload = {
        "tier": tier,
        "machine_id": machine_id,
        "created_at": datetime.now().isoformat(),
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # RSA 私钥签名
    sig = rsa.sign(payload_bytes, priv_key, "SHA-256")

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode()
    sig_b64 = base64.urlsafe_b64encode(sig).decode()

    return f"{payload_b64}.{sig_b64}"


def main():
    parser = argparse.ArgumentParser(description="AGBC License Key 生成工具 (RSA)")
    parser.add_argument("--tier", choices=["lite", "pro"], help="授权等级")
    parser.add_argument("--machine-id", "-m", help="目标机器 ID")
    parser.add_argument("--show-machine-id", action="store_true", help="显示本机机器 ID")
    parser.add_argument("--verify", help="验证一个 License Key")
    parser.add_argument("--gen-keys", action="store_true", help="生成 RSA 密钥对")
    args = parser.parse_args()

    if args.gen_keys:
        gen_keys()
        return

    if args.show_machine_id:
        mid = get_machine_id()
        print(f"\n{'='*60}")
        print(f"  本机机器 ID: {mid}")
        print(f"{'='*60}\n")
        return

    if args.verify:
        payload = verify_license(args.verify)
        if payload:
            print(f"\n✅ License 有效")
            print(f"   等级: {payload['tier'].upper()}")
            print(f"   机器 ID: {payload['machine_id']}")
            print(f"   创建时间: {payload['created_at']}")
        else:
            print(f"\n❌ License 无效")
        return

    if not args.tier or not args.machine_id:
        parser.print_help()
        print("\n示例:")
        print("  python license_gen.py --gen-keys              # 首次: 生成密钥对")
        print("  python license_gen.py --show-machine-id       # 查看本机机器码")
        print("  python license_gen.py --tier pro --machine-id <机器ID>")
        return

    key = generate_license(tier=args.tier, machine_id=args.machine_id)

    print(f"\n{'='*60}")
    print(f"  License Key 已生成 ({args.tier.upper()} 版)")
    print(f"{'='*60}")
    print(f"\n{key}\n")
    print(f"  目标机器: {args.machine_id}")
    print(f"  授权等级: {args.tier.upper()}")
    print(f"  签名算法: RSA-2048 + SHA-256")
    print(f"  有效期:   永久")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
