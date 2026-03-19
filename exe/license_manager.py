"""
License 验证模块 — HMAC-SHA256 签名 + 机器绑定

License Key 格式:
    BASE64(JSON{tier, machine_id, created_at}) . BASE64(HMAC_SIGNATURE)

tier: "lite" | "pro"
machine_id: 机器唯一标识 (CPU + 主板 + 磁盘序列号的 SHA256 前 32 位)

此模块编译为 .pyd 后分发，HMAC 密钥隐藏在二进制中。
"""
import base64
import hashlib
import hmac
import json
import os
import platform
import subprocess
import sys

# ═══════════════════════════════════════════════
# HMAC 签名密钥 (编译为 .pyd 后不可见)
# 请在正式发布前修改此密钥!
# ═══════════════════════════════════════════════
_HMAC_SECRET = b"AGBC-2026-CHANGE-THIS-SECRET-KEY-BEFORE-RELEASE!!"

# License 文件默认路径
_LICENSE_FILENAME = "license.key"


def get_machine_id() -> str:
    """获取当前机器唯一标识 (Windows: WMIC 硬件序列号)"""
    if platform.system() != "Windows":
        # Linux/Mac fallback: hostname + MAC
        import uuid
        raw = f"{platform.node()}:{uuid.getnode()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    parts = []
    # CPU ProcessorId
    try:
        out = subprocess.check_output(
            ["wmic", "cpu", "get", "ProcessorId"],
            stderr=subprocess.DEVNULL, timeout=10
        ).decode(errors="ignore")
        lines = [l.strip() for l in out.strip().splitlines() if l.strip() and l.strip() != "ProcessorId"]
        if lines:
            parts.append(lines[0])
    except Exception:
        pass

    # Baseboard SerialNumber
    try:
        out = subprocess.check_output(
            ["wmic", "baseboard", "get", "SerialNumber"],
            stderr=subprocess.DEVNULL, timeout=10
        ).decode(errors="ignore")
        lines = [l.strip() for l in out.strip().splitlines() if l.strip() and l.strip() != "SerialNumber"]
        if lines and lines[0] not in ("To be filled by O.E.M.", "Default string", ""):
            parts.append(lines[0])
    except Exception:
        pass

    # DiskDrive SerialNumber (第一块)
    try:
        out = subprocess.check_output(
            ["wmic", "diskdrive", "get", "SerialNumber"],
            stderr=subprocess.DEVNULL, timeout=10
        ).decode(errors="ignore")
        lines = [l.strip() for l in out.strip().splitlines() if l.strip() and l.strip() != "SerialNumber"]
        if lines:
            parts.append(lines[0])
    except Exception:
        pass

    if not parts:
        # 终极 fallback
        import uuid
        parts.append(str(uuid.getnode()))
        parts.append(platform.node())

    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _sign(data_bytes: bytes) -> bytes:
    """HMAC-SHA256 签名"""
    return hmac.new(_HMAC_SECRET, data_bytes, hashlib.sha256).digest()


def _get_license_path() -> str:
    """License 文件默认存放路径 (与程序同目录)"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    # 向上一级查找 data/ 目录
    data_dir = os.path.join(base, "..", "data")
    if os.path.isdir(data_dir):
        return os.path.join(data_dir, _LICENSE_FILENAME)
    return os.path.join(base, _LICENSE_FILENAME)


def generate_license(tier: str, machine_id: str) -> str:
    """
    生成 License Key (管理员调用)

    Args:
        tier: "lite" 或 "pro"
        machine_id: 目标机器 ID (由买家提供)

    Returns:
        License Key 字符串
    """
    if tier not in ("lite", "pro"):
        raise ValueError(f"无效的 tier: {tier}, 必须是 'lite' 或 'pro'")

    from datetime import datetime
    payload = {
        "tier": tier,
        "machine_id": machine_id,
        "created_at": datetime.now().isoformat(),
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = _sign(payload_bytes)

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode()
    sig_b64 = base64.urlsafe_b64encode(sig).decode()

    return f"{payload_b64}.{sig_b64}"


def verify_license(license_key: str) -> dict | None:
    """
    验证 License Key

    Returns:
        成功: {"tier": "lite"|"pro", "machine_id": "...", "created_at": "..."}
        失败: None
    """
    try:
        parts = license_key.strip().split(".")
        if len(parts) != 2:
            return None

        payload_bytes = base64.urlsafe_b64decode(parts[0])
        sig_bytes = base64.urlsafe_b64decode(parts[1])

        # 验证签名
        expected_sig = _sign(payload_bytes)
        if not hmac.compare_digest(sig_bytes, expected_sig):
            return None

        payload = json.loads(payload_bytes.decode("utf-8"))

        # 验证字段存在
        if payload.get("tier") not in ("lite", "pro"):
            return None
        if not payload.get("machine_id"):
            return None

        return payload

    except Exception:
        return None


def check_license(license_key: str = None) -> tuple[bool, str, str]:
    """
    检查 License 有效性 (含机器绑定验证)

    Args:
        license_key: License Key 字符串，None 则从文件读取

    Returns:
        (is_valid, tier, message)
        - is_valid: bool
        - tier: "lite" | "pro" | ""
        - message: 描述信息
    """
    # 从文件读取
    if license_key is None:
        path = _get_license_path()
        if not os.path.isfile(path):
            return False, "", "未找到 License 文件"
        try:
            with open(path, "r", encoding="utf-8") as f:
                license_key = f.read().strip()
        except Exception as e:
            return False, "", f"读取 License 文件失败: {e}"

    if not license_key:
        return False, "", "License Key 为空"

    # 验证签名
    payload = verify_license(license_key)
    if payload is None:
        return False, "", "License Key 无效"

    # 验证机器绑定
    current_machine = get_machine_id()
    if payload["machine_id"] != current_machine:
        return False, "", "License 与当前机器不匹配"

    return True, payload["tier"], f"License 有效 ({payload['tier'].upper()} 版)"


def save_license(license_key: str, path: str = None) -> str:
    """保存 License Key 到文件"""
    if path is None:
        path = _get_license_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(license_key.strip())
    return path


def get_tier() -> str:
    """快速获取当前 License 的 tier，无效返回空字符串"""
    valid, tier, _ = check_license()
    return tier if valid else ""


# ── 环境变量方式传递 tier (供 ui_entry.py 与 ui.py 之间通信) ──
TIER_ENV_KEY = "AGBC_LICENSE_TIER"


def set_tier_env(tier: str):
    os.environ[TIER_ENV_KEY] = tier


def get_tier_env() -> str:
    return os.environ.get(TIER_ENV_KEY, "")
