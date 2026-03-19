"""
License Key 生成工具 — 管理员专用 (不分发给买家)

用法:
  1. 先让买家运行 "获取机器码" 功能, 获取 machine_id
  2. 然后运行此工具生成 License Key:

     python license_gen.py --tier pro --machine-id abc123def456...
     python license_gen.py --tier lite --machine-id abc123def456...

  3. 将生成的 License Key 发送给买家

获取本机机器码 (测试用):
     python license_gen.py --show-machine-id
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from license_manager import generate_license, get_machine_id, verify_license


def main():
    parser = argparse.ArgumentParser(description="AGBC License Key 生成工具")
    parser.add_argument("--tier", choices=["lite", "pro"], help="授权等级")
    parser.add_argument("--machine-id", "-m", help="目标机器 ID")
    parser.add_argument("--show-machine-id", action="store_true", help="显示本机机器 ID")
    parser.add_argument("--verify", help="验证一个 License Key")
    args = parser.parse_args()

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
        print("  python license_gen.py --show-machine-id")
        print("  python license_gen.py --tier pro --machine-id <机器ID>")
        return

    key = generate_license(tier=args.tier, machine_id=args.machine_id)

    print(f"\n{'='*60}")
    print(f"  License Key 已生成 ({args.tier.upper()} 版)")
    print(f"{'='*60}")
    print(f"\n{key}\n")
    print(f"  目标机器: {args.machine_id}")
    print(f"  授权等级: {args.tier.upper()}")
    print(f"  有效期:   永久")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
