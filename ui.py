"""
自动化绑卡支付 - Streamlit UI
运行: streamlit run ui.py --server.address 0.0.0.0 --server.port 8503
"""
import json
import logging
import os
import sys
import traceback
import threading
from collections import deque

import streamlit as st

st.set_page_config(page_title="Let's ABC", page_icon="💳", layout="wide", initial_sidebar_state="collapsed")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config, CardInfo, BillingInfo, CaptchaConfig
from mail_provider import MailProvider
from auth_flow import AuthFlow, AuthResult
from payment_flow import PaymentFlow
from logger import ResultStore
from database import init_db
from code_manager import validate_code, reserve_use, complete_use, update_execution, get_code_history, get_code_info, save_code_config, load_code_config

init_db()

# ── 兑换码系统开关: 在 config.json 中设置 "code_system_enabled": true 开启 ──
_ENABLE_CODE_SYSTEM = False
try:
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.isfile(_cfg_path):
        with open(_cfg_path, encoding="utf-8") as _f:
            _ENABLE_CODE_SYSTEM = bool(json.load(_f).get("code_system_enabled", False))
except Exception:
    pass

OUTPUT_DIR = "test_outputs"

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


# widget key → config.json 路径的映射 (复用)
_WIDGET_CONFIG_MAPPING = {
    # 邮箱
    "w_mail_worker_reg": ("mail", "worker_domain"),
    "w_mail_domain_reg": ("mail", "email_domain"),
    "w_mail_token_reg": ("mail", "admin_token"),
    # 代理
    "w_proxy": ("proxy",),
    # 注册
    "w_register_mode": ("register_mode",),
    "w_default_password": ("default_password",),
    # NewAPI
    "w_newapi_base": ("newapi", "base_url"),
    "w_newapi_token": ("newapi", "admin_token"),
    "w_newapi_type": ("newapi", "channel_type"),
    "w_newapi_models": ("newapi", "models"),
    "w_newapi_group": ("newapi", "group"),
    "w_newapi_priority": ("newapi", "priority"),
    "w_newapi_weight": ("newapi", "weight"),
    # 卡片
    "w_card_number": ("card", "number"),
    "w_card_cvc":    ("card", "cvc"),
    "w_exp_month":   ("card", "exp_month"),
    "w_exp_year":    ("card", "exp_year"),
    # 账单
    "w_billing_name":  ("billing", "name"),
    "w_currency":      ("billing", "currency"),
    "w_address_line1": ("billing", "address_line1"),
    "w_address_state": ("billing", "address_state"),
    "w_postal_code":   ("billing", "postal_code"),
    "w_address_city":  ("billing", "city"),
}


def _apply_config_data(data: dict, force: bool = False):
    """将配置 dict 应用到 session_state。
    force=False: 仅设置 default（不覆盖已有值），用于 config.json 兜底加载
    force=True: 强制覆盖已有值，用于数据库加载（优先级更高）"""
    for widget_key, path in _WIDGET_CONFIG_MAPPING.items():
        val = data
        for p in path:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                val = None
                break
        if val is not None and val != "":
            if force:
                st.session_state[widget_key] = str(val)
            else:
                st.session_state.setdefault(widget_key, str(val))
    # 国家映射: billing.country → w_country selectbox label
    bc = data.get("billing", {}).get("country", "")
    if bc:
        for label in COUNTRY_MAP:
            if label.startswith(bc):
                if force:
                    st.session_state["w_country"] = label
                else:
                    st.session_state.setdefault("w_country", label)
                break


def _load_config_defaults():
    """启动时加载配置到 session_state 默认值。
    优先级: 兑换码数据库配置 > config.json 全局配置"""
    if st.session_state.get("_config_loaded"):
        return

    # 1) 尝试从兑换码数据库加载配置
    code = st.session_state.get("verified_code", "")
    if _ENABLE_CODE_SYSTEM and code and code != "__disabled__":
        code_cfg = load_code_config(code)
        if code_cfg:
            _apply_config_data(code_cfg, force=True)
            st.session_state["_config_loaded"] = True
            return

    # 2) 回退到 config.json
    try:
        if not os.path.isfile(_CONFIG_PATH):
            st.session_state["_config_loaded"] = True
            return
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        st.session_state["_config_loaded"] = True
        return

    _apply_config_data(data)
    st.session_state["_config_loaded"] = True


def _save_config_to_file(**overrides):
    """将当前 UI 表单值写回 config.json (保留未涉及的字段)"""
    try:
        if os.path.isfile(_CONFIG_PATH):
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
    except Exception:
        data = {}

    data.setdefault("mail", {})
    data["mail"]["worker_domain"] = overrides.get("mail_worker", "") or st.session_state.get("w_mail_worker_reg", "")
    data["mail"]["admin_token"]   = overrides.get("mail_token", "") or st.session_state.get("w_mail_token_reg", "")
    data["mail"]["email_domain"]  = overrides.get("mail_domain", "") or st.session_state.get("w_mail_domain_reg", "")

    data["proxy"] = overrides.get("proxy") or st.session_state.get("w_proxy", "") or None
    data["register_mode"] = st.session_state.get("w_register_mode", "OTP 注册")
    data["register_mode"] = "password" if "密码" in data["register_mode"] else "otp"
    data["default_password"] = st.session_state.get("w_default_password", "") or None

    # NewAPI
    data.setdefault("newapi", {})
    data["newapi"]["base_url"]     = st.session_state.get("w_newapi_base", "")
    data["newapi"]["admin_token"]  = st.session_state.get("w_newapi_token", "")
    data["newapi"]["channel_type"] = st.session_state.get("w_newapi_type", "57")
    data["newapi"]["models"]       = st.session_state.get("w_newapi_models", "")
    data["newapi"]["group"]        = st.session_state.get("w_newapi_group", "default,vip,svip")
    data["newapi"]["priority"]     = st.session_state.get("w_newapi_priority", "0")
    data["newapi"]["weight"]       = st.session_state.get("w_newapi_weight", "0")

    data.setdefault("card", {})
    data["card"]["number"]    = st.session_state.get("w_card_number", "")
    data["card"]["cvc"]       = st.session_state.get("w_card_cvc", "")
    data["card"]["exp_month"] = st.session_state.get("w_exp_month", "")
    data["card"]["exp_year"]  = st.session_state.get("w_exp_year", "")

    data.setdefault("billing", {})
    data["billing"]["name"]          = st.session_state.get("w_billing_name", "")
    data["billing"]["currency"]      = st.session_state.get("w_currency", "")
    data["billing"]["address_line1"] = st.session_state.get("w_address_line1", "")
    data["billing"]["address_state"] = st.session_state.get("w_address_state", "")
    data["billing"]["postal_code"]   = st.session_state.get("w_postal_code", "")
    data["billing"]["city"]          = st.session_state.get("w_address_city", "")
    # 从 selectbox label 提取国家代码
    country_label = st.session_state.get("w_country", "")
    if country_label and country_label in COUNTRY_MAP:
        data["billing"]["country"] = COUNTRY_MAP[country_label][0]

    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("ui").warning(f"保存 config.json 失败: {e}")


def _collect_config_from_session() -> dict:
    """从 session_state 收集当前配置为 dict (可用于存入数据库)"""
    data = {}
    data["mail"] = {
        "worker_domain": st.session_state.get("w_mail_worker_reg", ""),
        "admin_token": st.session_state.get("w_mail_token_reg", ""),
        "email_domain": st.session_state.get("w_mail_domain_reg", ""),
    }
    data["proxy"] = st.session_state.get("w_proxy", "") or None
    rm = st.session_state.get("w_register_mode", "OTP 注册")
    data["register_mode"] = "password" if "密码" in rm else "otp"
    data["default_password"] = st.session_state.get("w_default_password", "") or None
    data["newapi"] = {
        "base_url": st.session_state.get("w_newapi_base", ""),
        "admin_token": st.session_state.get("w_newapi_token", ""),
        "channel_type": st.session_state.get("w_newapi_type", "57"),
        "models": st.session_state.get("w_newapi_models", ""),
        "group": st.session_state.get("w_newapi_group", "default,vip,svip"),
        "priority": st.session_state.get("w_newapi_priority", "0"),
        "weight": st.session_state.get("w_newapi_weight", "0"),
    }
    data["card"] = {
        "number": st.session_state.get("w_card_number", ""),
        "cvc": st.session_state.get("w_card_cvc", ""),
        "exp_month": st.session_state.get("w_exp_month", ""),
        "exp_year": st.session_state.get("w_exp_year", ""),
    }
    country_label = st.session_state.get("w_country", "")
    country_code = ""
    if country_label and country_label in COUNTRY_MAP:
        country_code = COUNTRY_MAP[country_label][0]
    data["billing"] = {
        "name": st.session_state.get("w_billing_name", ""),
        "currency": st.session_state.get("w_currency", ""),
        "address_line1": st.session_state.get("w_address_line1", ""),
        "address_state": st.session_state.get("w_address_state", ""),
        "postal_code": st.session_state.get("w_postal_code", ""),
        "city": st.session_state.get("w_address_city", ""),
        "country": country_code,
    }
    return data


def _on_config_change():
    """widget 值变化时自动保存配置 (config.json + 兑换码数据库)"""
    _save_config_to_file()
    # 兑换码系统开启时，同步保存到数据库
    code = st.session_state.get("verified_code", "")
    if _ENABLE_CODE_SYSTEM and code and code != "__disabled__":
        try:
            save_code_config(code, _collect_config_from_session())
        except Exception:
            pass


def _sanitize_error(raw_error: str) -> str:
    """将技术性错误信息转为用户友好的简要提示"""
    if not raw_error:
        return "执行失败"
    e = raw_error.lower()
    if "payment element" in e or "stripe" in e and "未加载" in raw_error:
        return "支付页面加载失败，请稍后重试"
    if "cloudflare" in e or "请稍候" in raw_error or "just a moment" in e:
        return "网络验证失败，请稍后重试"
    if "支付被拒" in raw_error or "card_declined" in e or "declined" in e:
        return "支付被拒，请检查卡片信息"
    if "用户手动终止" in raw_error:
        return "已取消"
    if "session_token" in e or "sentinel" in e or "403" in raw_error:
        return "登录凭证失效，请更换 Token"
    if "curl" in e or "url rejected" in e or "connection" in e or "timeout" in e:
        return "网络连接失败，请检查代理配置"
    if "captcha" in e or "hcaptcha" in e:
        return "人机验证失败，请重试"
    if "oom" in e or "memory" in e:
        return "服务器资源不足，请稍后重试"
    if "额度" in raw_error or "已用完" in raw_error:
        return raw_error  # 兑换码相关信息直接显示
    # 兜底: 只显示简要信息
    return "执行失败，请重试"


import re as _re

# 国家名/后缀 → (country_code, currency) 映射
_COUNTRY_ALIAS = {
    "UK": ("GB", "GBP"), "GB": ("GB", "GBP"), "England": ("GB", "GBP"), "United Kingdom": ("GB", "GBP"), "英国": ("GB", "GBP"),
    "US": ("US", "USD"), "USA": ("US", "USD"), "United States": ("US", "USD"), "美国": ("US", "USD"),
    "DE": ("DE", "EUR"), "Germany": ("DE", "EUR"), "德国": ("DE", "EUR"),
    "JP": ("JP", "JPY"), "Japan": ("JP", "JPY"), "日本": ("JP", "JPY"),
    "FR": ("FR", "EUR"), "France": ("FR", "EUR"), "法国": ("FR", "EUR"),
    "SG": ("SG", "SGD"), "Singapore": ("SG", "SGD"), "新加坡": ("SG", "SGD"),
    "HK": ("HK", "HKD"), "Hong Kong": ("HK", "HKD"), "香港": ("HK", "HKD"),
    "KR": ("KR", "KRW"), "Korea": ("KR", "KRW"), "韩国": ("KR", "KRW"),
    "AU": ("AU", "AUD"), "Australia": ("AU", "AUD"), "澳大利亚": ("AU", "AUD"),
    "CA": ("CA", "CAD"), "Canada": ("CA", "CAD"), "加拿大": ("CA", "CAD"),
    "NL": ("NL", "EUR"), "Netherlands": ("NL", "EUR"), "荷兰": ("NL", "EUR"),
    "IT": ("IT", "EUR"), "Italy": ("IT", "EUR"), "意大利": ("IT", "EUR"),
    "ES": ("ES", "EUR"), "Spain": ("ES", "EUR"), "西班牙": ("ES", "EUR"),
    "CH": ("CH", "CHF"), "Switzerland": ("CH", "CHF"), "瑞士": ("CH", "CHF"),
}


def _parse_card_text(text: str) -> dict:
    """从粘贴文本中解析卡号、有效期、CVV、账单地址。
    支持两种格式:
    1) 纯文本: 卡号一行、MM/YY一行、CVV一行、账单地址一行
    2) 键值对: 卡号: xxx / 有效期: MMYY / CVV: xxx / 地址: xxx / 城市: xxx / 邮编: xxx / 国家: xxx
    """
    result = {}
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    # 构建键值映射 (支持 "键: 值" 和 "键：值")
    kv = {}
    for line in lines:
        m = _re.match(r'^(.+?)\s*[:：]\s*(.+)$', line)
        if m:
            kv[m.group(1).strip().lower()] = m.group(2).strip()

    # ── 卡号 ──
    # 从键值对获取
    for k in ("卡号", "card number", "card", "card_number"):
        if k in kv:
            digits = kv[k].replace(" ", "").replace("-", "")
            if digits.isdigit() and 13 <= len(digits) <= 19:
                result["card_number"] = digits
                break

    # 检查 "cardnum MM YY CVC" 单行格式 (如 "5481087136282260 03 32 221")
    if "card_number" not in result:
        for line in lines:
            m = _re.match(r'^(\d{13,19})\s+(0[1-9]|1[0-2])\s+(\d{2,4})\s+(\d{3,4})$', line.replace("-", "").strip())
            if m:
                result["card_number"] = m.group(1)
                result["exp_month"] = m.group(2)
                yr = m.group(3)
                if len(yr) == 2:
                    yr = "20" + yr
                result["exp_year"] = yr
                result["cvv"] = m.group(4)
                break

    # 回退: 纯数字行
    if "card_number" not in result:
        for line in lines:
            digits_only = line.replace(" ", "").replace("-", "")
            if digits_only.isdigit() and 13 <= len(digits_only) <= 19:
                result["card_number"] = digits_only
                break

    # ── 有效期 ──
    # 从键值对获取 (支持 MMYY, MM/YY, MM/YYYY)
    for k in ("有效期", "exp", "expiry", "expiration", "exp_date"):
        if k in kv:
            val = kv[k]
            # MM/YY 或 MM/YYYY
            m = _re.search(r'(0[1-9]|1[0-2])\s*/\s*(\d{2,4})', val)
            if m:
                result["exp_month"] = m.group(1)
                yr = m.group(2)
                if len(yr) == 2:
                    yr = "20" + yr
                result["exp_year"] = yr
                break
            # MMYY 或 MMYYYY (无分隔符)
            m = _re.search(r'^(0[1-9]|1[0-2])(\d{2,4})$', val.strip())
            if m:
                result["exp_month"] = m.group(1)
                yr = m.group(2)
                if len(yr) == 2:
                    yr = "20" + yr
                result["exp_year"] = yr
                break
    # 回退: 逐行寻找 MM/YY
    if "exp_month" not in result:
        for line in lines:
            m = _re.search(r'\b(0[1-9]|1[0-2])\s*/\s*(\d{2,4})\b', line)
            if m:
                result["exp_month"] = m.group(1)
                yr = m.group(2)
                if len(yr) == 2:
                    yr = "20" + yr
                result["exp_year"] = yr
                break

    # ── CVV ──
    for k in ("cvv", "cvc", "安全码"):
        if k in kv:
            m = _re.search(r'\b(\d{3,4})\b', kv[k])
            if m:
                result["cvv"] = m.group(1)
                break
    if "cvv" not in result:
        for i, line in enumerate(lines):
            if _re.search(r'(?i)\b(?:cvv|cvc|安全码)\b', line):
                m = _re.search(r'\b(\d{3,4})\b', line)
                if m:
                    result["cvv"] = m.group(1)
                elif i + 1 < len(lines):
                    m2 = _re.search(r'\b(\d{3,4})\b', lines[i + 1])
                    if m2:
                        result["cvv"] = m2.group(1)
                break

    # ── 地址: 键值对模式 (地址/城市/州/邮编/国家 分字段) ──
    kv_addr = None
    for k in ("地址", "address", "address_line1"):
        if k in kv:
            kv_addr = kv[k]
            break
    kv_city = None
    for k in ("城市", "city"):
        if k in kv:
            kv_city = kv[k]
            break
    kv_state = None
    for k in ("州", "state", "省"):
        if k in kv:
            kv_state = kv[k]
            break
    kv_zip = None
    for k in ("邮编", "postal_code", "zip", "zipcode", "zip_code"):
        if k in kv:
            kv_zip = kv[k]
            break
    kv_country = None
    for k in ("国家", "country", "地区"):
        if k in kv:
            kv_country = kv[k]
            break

    if kv_addr:
        result["address_line1"] = kv_addr
        if kv_city:
            result["address_city"] = kv_city
            result["address_state"] = kv_state or kv_city
        elif kv_state:
            result["address_state"] = kv_state
        if kv_zip:
            result["postal_code"] = kv_zip
        if kv_country:
            ci = _COUNTRY_ALIAS.get(kv_country)
            if ci:
                result["country_code"] = ci[0]
                result["currency"] = ci[1]
        # 构建 raw_address
        parts = [kv_addr]
        if kv_city:
            parts.append(kv_city)
        if kv_state:
            parts.append(kv_state)
        if kv_zip:
            parts.append(kv_zip)
        if kv_country:
            parts.append(kv_country)
        result["raw_address"] = ", ".join(parts)

    # ── 地址: 回退 "账单地址" / "billing address" 单行模式 ──
    if "address_line1" not in result:
        addr_text = ""
        for i, line in enumerate(lines):
            if _re.search(r'(?i)账单地址|billing\s*address', line):
                after = _re.sub(r'(?i)^.*?(账单地址|billing\s*address)\s*[:：]?\s*', '', line).strip()
                if after and len(after) > 3:
                    addr_text = after
                else:
                    for j in range(i + 1, min(i + 5, len(lines))):
                        candidate = lines[j]
                        if candidate and candidate not in ("复制", "copy", ""):
                            addr_text = candidate
                            break
                break

        if addr_text:
            result["raw_address"] = addr_text
            parts = [p.strip() for p in addr_text.split(",")]
            if len(parts) >= 2:
                last = parts[-1].strip()
                country_info = _COUNTRY_ALIAS.get(last)
                if country_info:
                    result["country_code"] = country_info[0]
                    result["currency"] = country_info[1]
                    parts = parts[:-1]

                for idx, p in enumerate(parts):
                    if _re.search(r'\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b', p.strip(), _re.IGNORECASE):
                        result["postal_code"] = p.strip()
                        parts.pop(idx)
                        break
                    elif _re.search(r'\b\d{5}(-\d{4})?\b', p.strip()):
                        result["postal_code"] = p.strip()
                        parts.pop(idx)
                        break
                    elif _re.search(r'\b\d{3}-\d{4}\b', p.strip()):
                        result["postal_code"] = p.strip()
                        parts.pop(idx)
                        break

                if len(parts) == 1:
                    result["address_line1"] = parts[0]
                elif len(parts) == 2:
                    result["address_line1"] = parts[0]
                    result["address_state"] = parts[1]
                elif len(parts) >= 3:
                    result["address_line1"] = parts[0]
                    result["address_state"] = parts[1]

    # ── 姓名 ──
    for k in ("姓名", "name", "cardholder", "持卡人"):
        if k in kv:
            result["billing_name"] = kv[k]
            break

    # ── 纯文本多行回退: 从非卡/非地址行中提取姓名 ──
    if "billing_name" not in result:
        for line in lines:
            # 跳过已被解析的行 (卡号行、地址行)
            stripped = line.strip()
            if not stripped:
                continue
            # 跳过纯数字/卡号行
            if _re.match(r'^[\d\s/\-]+$', stripped):
                continue
            # 跳过含邮编/地址的行
            if _re.search(r'\d{5}', stripped) and ',' in stripped:
                continue
            # 跳过键值对行
            if _re.match(r'^.+?[:：]', stripped):
                continue
            # 可能是姓名: 2-5 个英文单词 (首字母大写)
            if _re.match(r'^[A-Z][a-z]+(\s+[A-Z][a-z]+){0,4}$', stripped):
                result["billing_name"] = stripped
                break

    # ── 纯文本多行回退: 从第二行解析地址 (如 "38 Pearl Avenue, Louisville, MS 39339, US") ──
    if "address_line1" not in result and len(lines) >= 2:
        for line in lines:
            stripped = line.strip()
            # 跳过卡号行 (全数字+空格)
            if _re.match(r'^[\d\s/\-]+$', stripped):
                continue
            # 候选地址行: 含逗号、有数字(门牌号或邮编)
            if ',' in stripped and _re.search(r'\d', stripped):
                result["raw_address"] = stripped
                parts = [p.strip() for p in stripped.split(",")]
                # 检查最后部分是否是国家
                if len(parts) >= 2:
                    last = parts[-1].strip()
                    country_info = _COUNTRY_ALIAS.get(last)
                    if country_info:
                        result["country_code"] = country_info[0]
                        result["currency"] = country_info[1]
                        parts = parts[:-1]
                # 提取邮编
                for idx, p in enumerate(parts):
                    zip_match = _re.search(r'\b(\d{5}(?:-\d{4})?)\b', p)
                    if zip_match:
                        result["postal_code"] = zip_match.group(1)
                        # 带邮编的部分可能是 "MS 39339" 或 "Louisville, MS 39339"
                        # 提取 state 代码
                        state_match = _re.match(r'^([A-Z]{2})\s+\d{5}', p.strip())
                        if state_match:
                            result["address_state"] = state_match.group(1)
                            parts.pop(idx)
                        else:
                            # 邮编在地址部分中, 分离
                            clean = _re.sub(r'\s*\d{5}(?:-\d{4})?\s*', '', p).strip()
                            if clean:
                                parts[idx] = clean
                            else:
                                parts.pop(idx)
                        break
                # 分配剩余部分
                if len(parts) >= 1:
                    result["address_line1"] = parts[0]
                if len(parts) >= 2 and "address_state" not in result:
                    # 可能是 city 或 city, state
                    city_state = parts[1].strip()
                    csm = _re.match(r'^(.+?)\s+([A-Z]{2})$', city_state)
                    if csm:
                        result["address_city"] = csm.group(1)
                        result["address_state"] = csm.group(2)
                    else:
                        result["address_city"] = city_state
                elif len(parts) >= 2:
                    result["address_city"] = parts[1]
                break
            break

    return result


# 国家 → (code, currency, state, address, postal_code)
COUNTRY_MAP = {
    "US - 美国": ("US", "USD", "California", "123 Main St", "90001"),
    "DE - 德国": ("DE", "EUR", "Berlin", "Hauptstraße 1", "10115"),
    "JP - 日本": ("JP", "JPY", "Tokyo", "1-1-1 Shibuya", "150-0002"),
    "GB - 英国": ("GB", "GBP", "London", "10 Downing St", "SW1A 2AA"),
    "FR - 法国": ("FR", "EUR", "Paris", "1 Rue de Rivoli", "75001"),
    "SG - 新加坡": ("SG", "SGD", "Singapore", "1 Raffles Place", "048616"),
    "HK - 香港": ("HK", "HKD", "Hong Kong", "1 Queen's Road", "000000"),
    "KR - 韩国": ("KR", "KRW", "Seoul", "1 Gangnam-daero", "06000"),
    "AU - 澳大利亚": ("AU", "AUD", "NSW", "1 George St", "2000"),
    "CA - 加拿大": ("CA", "CAD", "Ontario", "123 King St", "M5H 1A1"),
    "NL - 荷兰": ("NL", "EUR", "Amsterdam", "Damrak 1", "1012 LG"),
    "IT - 意大利": ("IT", "EUR", "Rome", "Via Roma 1", "00100"),
    "ES - 西班牙": ("ES", "EUR", "Madrid", "Calle Mayor 1", "28013"),
    "CH - 瑞士": ("CH", "CHF", "Zurich", "Bahnhofstrasse 1", "8001"),
}

st.set_page_config(page_title="Let's ABC", page_icon="A", layout="wide")

# ── CSS ──
st.markdown("""
<style>
    /* 终极真全屏宽度强制覆盖 */
    html, body, [data-testid="stAppViewContainer"], .main {
        max-width: 100% !important;
        width: 100% !important;
        margin: 0 !important;
        padding-left: 0 !important;
        padding-right: 0 !important;
        overflow: hidden !important;
        height: 100vh !important;
    }
    
    .block-container {
        max-width: 100% !important;
        width: 100% !important;
        margin: 0 !important;
        padding-top: 1rem !important; 
        padding-bottom: 0 !important;
        padding-left: 2rem !important;  /* 左右左右稍留间隙 */
        padding-right: 2rem !important; 
        overflow: hidden !important;
        height: 100vh !important;
        font-family: 'Inter', system-ui, sans-serif;
    }
    
    /* 隐藏默认 header 以节省空间 */
    header[data-testid="stHeader"] { display: none !important; }
    
    /* 强行平分最外层三个列，设置独立滚动、等宽高宽满屏 */
    div[data-testid="stVerticalBlock"] > div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        flex: 1 1 33.333% !important;
        width: 33.333% !important;
        min-width: 0 !important;
        height: calc(100vh - 30px) !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        padding: 0 1.5rem 2.5rem !important; /* 给底部增加 2.5rem 避免紧靠最下方边缘 */
        display: flex !important;
        flex-direction: column !important;
        gap: 0.5rem; /* 稍微控制组件间距 */
    }
    
    /* 让中间列里的组件不被压缩 */
    div[data-testid="column"] > div[data-testid="element-container"] { flex-shrink: 0; }
    
    /* 使中间栏的执行日志容器自动占满下方空间并和底部留微小间距 */
    div[data-testid="column"]:nth-child(2) div[data-testid="stVerticalBlockBorderWrapper"],
    div[data-testid="column"]:nth-child(2) div[style*="height: 750px"] {
        flex-grow: 1 !important;
        height: auto !important;
        min-height: 400px;
        margin-bottom: 5px !important;
    }
    div[data-testid="column"]:nth-child(2) div[data-testid="stVerticalBlockBorderWrapper"] > div {
        max-height: 100% !important;
        height: 100% !important;
    }

    /* 缩小滚动条，更美观 */
    div[data-testid="column"]::-webkit-scrollbar { width: 4px; }
    div[data-testid="column"]::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 2px; }

    /* 选项卡排版优化 */
    .stTabs [data-baseweb="tab-list"] { gap: 0.5rem; border-bottom: 2px solid #e2e8f0; }
    .stTabs [data-baseweb="tab"] {
        padding: 0.6rem 1.2rem; font-weight: 600; letter-spacing: 0.03em;
        border-bottom: 2px solid transparent; transition: all 0.2s ease;
        margin-bottom: -2px; color: #64748b;
    }
    .stTabs [aria-selected="true"] { border-bottom-color: #6366f1; color: #334155; }
    
    /* 进度条精细化 */
    .stProgress > div > div > div { background: linear-gradient(90deg, #6366f1, #818cf8); border-radius: 4px; }
    
    /* 按钮质感 */
    .stButton > button { border-radius: 8px; font-weight: 600; letter-spacing: 0.02em; transition: all 0.2s; border: 1px solid #e2e8f0; }
    .stButton > button[kind="primary"] { background: linear-gradient(135deg, #6366f1, #4f46e5); border: none; color: #ffffff; box-shadow: 0 4px 6px -1px rgba(99, 102, 241, 0.2), 0 2px 4px -1px rgba(99, 102, 241, 0.1); }
    .stButton > button[kind="primary"]:hover { transform: translateY(-1px); box-shadow: 0 6px 8px -1px rgba(99, 102, 241, 0.3), 0 3px 6px -1px rgba(99, 102, 241, 0.15); }
    
    /* 输入框与 Expander 卡片感 */
    .stTextInput > div > div > input { border-radius: 8px; border: 1px solid #cbd5e1; box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05); }
    .stTextInput > div > div > input:focus { border-color: #6366f1; box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2); }
    
    .streamlit-expanderHeader { font-weight: 600; letter-spacing: 0.02em; border-radius: 8px; }
    [data-testid="stExpander"] { border: 1px solid #e2e8f0; border-radius: 8px; box-shadow: 0 1px 3px 0 rgba(0,0,0,0.05); background-color: #ffffff; overflow: hidden; }
    
    /* 分割线淡化 */
    hr { opacity: 0.08; margin: 1.5em 0; border-top: 2px solid #cbd5e1; }
    
    /* dataframe 外边框 */
    [data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px 0 rgba(0,0,0,0.05); }

    /* 日志区域自动换行 */
    .stCodeBlock pre {
        white-space: pre-wrap !important;
        word-wrap: break-word !important;
        overflow-wrap: break-word !important;
    }
</style>
""", unsafe_allow_html=True)

# 后台日志缓存 — 使用 cache_resource 确保跨 rerun 同一对象
@st.cache_resource
def _get_log_shared():
    return {"cache": deque(maxlen=5000), "lock": threading.Lock()}

_log_shared = _get_log_shared()


# ── 日志 ──
class LogCapture(logging.Handler):
    def __init__(self, shared):
        super().__init__()
        self._cache = shared["cache"]
        self._lock = shared["lock"]
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        with self._lock:
            self._cache.append(msg)


def pull_captured_logs():
    """将后台日志搬运到 session_state，需在主线程调用。"""
    if "log_buffer" not in st.session_state:
        st.session_state.log_buffer = []
    cache = _log_shared["cache"]
    lock = _log_shared["lock"]
    with lock:
        if not cache:
            return
        st.session_state.log_buffer.extend(list(cache))
        cache.clear()


def clear_captured_logs():
    cache = _log_shared["cache"]
    lock = _log_shared["lock"]
    with lock:
        cache.clear()


def init_logging():
    handler = LogCapture(_log_shared)
    handler.setLevel(logging.INFO)
    handler._is_log_capture = True
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [h for h in root.handlers if not getattr(h, '_is_log_capture', False)]
    root.addHandler(handler)
    # 同时输出到 stdout (systemd/journalctl 可读)
    if not any(isinstance(h, logging.StreamHandler) and not getattr(h, '_is_log_capture', False) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(sh)
    logging.getLogger("watchdog").setLevel(logging.WARNING)


for k, v in {"log_buffer": [], "running": False, "result": None}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# 每次 rerun 先同步一次日志缓存
pull_captured_logs()

# ── widget 默认值初始化 (只在首次运行时设置) ──
_widget_defaults = {
    "w_exp_month": "12",
    "w_exp_year": "2030",
    "w_proxy": "",
    "w_billing_name": "",
}
for _dk, _dv in _widget_defaults.items():
    if _dk not in st.session_state:
        st.session_state[_dk] = _dv

# 配置加载移到 verified_code 初始化之后 (见下方兑换码门禁后)

# ── 延迟的解析结果应用 (必须在 widget 渲染之前) ──
_parse_just_applied = False
if "_pending_parse" in st.session_state:
    _pp = st.session_state.pop("_pending_parse")
    for _pk, _pv in _pp.items():
        st.session_state[_pk] = _pv
    _parse_just_applied = True


# ════════════════════════════════════════
# 顶部
# ════════════════════════════════════════
st.markdown(
    '<h1 style="text-align:center;letter-spacing:3px;color:#1e293b;margin-bottom:0.8rem;font-weight:800;">'
    'OpenAI Auto Register &amp; Bind Card'
    '</h1>',
    unsafe_allow_html=True,
)

# ── 开发者模式: 启动时通过 -- --dev 参数开启 ──
# 用法: streamlit run ui.py -- --dev
dev_mode = "--dev" in sys.argv

# ═══════════════════════════════════════
# 兑换码验证门禁 (仅在 code_system_enabled=true 时启用)
# ═══════════════════════════════════════
if "verified_code" not in st.session_state:
    st.session_state.verified_code = "" if _ENABLE_CODE_SYSTEM else "__disabled__"

if _ENABLE_CODE_SYSTEM and not st.session_state.verified_code:
    st.markdown(
        '<div style="text-align:center;margin:40px 0 20px;opacity:0.7">输入兑换码开始使用</div>',
        unsafe_allow_html=True,
    )
    _code_col1, _code_col2 = st.columns([3, 1])
    with _code_col1:
        _input_code = st.text_input("兑换码", placeholder="XXXX-XXXX-XXXX", label_visibility="collapsed")
    with _code_col2:
        _verify_btn = st.button("验证", type="primary", use_container_width=True)
    if _verify_btn and _input_code:
        _valid, _msg = validate_code(_input_code.strip())
        if _valid:
            st.session_state.verified_code = _input_code.strip()
            st.rerun()
        else:
            st.error(_msg)
    st.stop()

# ── 兑换码验证通过后, 加载配置 (此时 verified_code 已有正确值) ──
_load_config_defaults()

# ── 已验证: 显示兑换码状态 ──
_code_info = get_code_info(st.session_state.verified_code) if _ENABLE_CODE_SYSTEM else None
if _code_info:
    _remaining = _code_info["total_uses"] - _code_info["used_count"]
    _status_col1, _status_col2 = st.columns([5, 1])
    with _status_col1:
        st.caption(f"兑换码: `{st.session_state.verified_code}` — 剩余 {_remaining}/{_code_info['total_uses']} 次")
    with _status_col2:
        if st.button("退出", key="logout_code"):
            st.session_state.verified_code = ""
            st.rerun()


# 日志关键词 → 进度百分比映射 (按模式区分)
_PROGRESS_KEYWORDS_FULL = [
    ("使用已有凭证", 5),
    ("邮箱创建成功", 3),
    ("注册完成", 10),
    ("创建 Checkout Session", 12),
    ("Checkout 创建成功", 18),
    ("启动 Chrome", 22),
    ("Chrome ready", 28),
    ("通过 Cloudflare", 32),
    ("Cloudflare 已通过", 38),
    ("加载 checkout 页面", 42),
    ("Stripe Payment Element", 48),
    ("Stripe Element 已加载", 55),
    ("填写卡片信息", 60),
    ("已输入卡号", 65),
    ("已输入 CVC", 70),
    ("填写账单地址", 73),
    ("地址-邮编", 78),
    ("已点击提交按钮", 82),
    ("等待支付处理", 85),
    ("hCaptcha", 88),
    ("checkbox 已点击", 92),
    ("支付成功", 98),
    ("支付被拒", 98),
    ("支付失败", 98),
]

# 仅注册模式: 注册相关关键词拉伸到 0~98%
_PROGRESS_KEYWORDS_REG_ONLY = [
    ("网络正常", 5),
    ("CSRF Token", 15),
    ("OpenAI 授权地址", 25),
    ("OAuth 初始化", 30),
    ("邮箱创建成功", 10),
    ("Sentinel Token", 40),
    ("注册邮箱已提交", 50),
    ("OTP 已发送", 55),
    ("OTP 验证成功", 65),
    ("密码注册提交成功", 50),
    ("账户创建成功", 75),
    ("重定向链完成", 85),
    ("认证 Session", 90),
    ("注册流程完成", 98),
    ("密码注册流程完成", 98),
    ("注册完成", 98),
]

# 仅绑卡模式: 支付相关关键词拉伸到 0~98%
_PROGRESS_KEYWORDS_BIND_ONLY = [
    ("使用已有凭证", 5),
    ("access_token 刷新成功", 8),
    ("创建 Checkout Session", 12),
    ("Checkout 创建成功", 18),
    ("启动 Chrome", 22),
    ("Chrome ready", 28),
    ("通过 Cloudflare", 35),
    ("Cloudflare 已通过", 42),
    ("加载 checkout 页面", 48),
    ("Stripe Payment Element", 55),
    ("Stripe Element 已加载", 60),
    ("填写卡片信息", 65),
    ("已输入卡号", 70),
    ("已输入 CVC", 75),
    ("填写账单地址", 78),
    ("地址-邮编", 82),
    ("已点击提交按钮", 86),
    ("等待支付处理", 90),
    ("hCaptcha", 92),
    ("checkbox 已点击", 95),
    ("支付成功", 98),
    ("支付被拒", 98),
    ("支付失败", 98),
]

def _calc_progress_pct():
    """根据 session_state.log_buffer (累积) 计算当前进度百分比"""
    pull_captured_logs()  # 先把 _LOG_CACHE 搬运到 log_buffer
    logs = st.session_state.get("log_buffer", [])
    if not logs:
        return 1
    text = "\n".join(logs[-30:])
    best = 1
    # 根据当前执行模式选择关键词表
    flow_mode = st.session_state.get("_active_flow_mode", "注册 + 绑卡")
    if flow_mode == "仅注册":
        keywords = _PROGRESS_KEYWORDS_REG_ONLY
    elif flow_mode == "仅绑卡":
        keywords = _PROGRESS_KEYWORDS_BIND_ONLY
    else:
        keywords = _PROGRESS_KEYWORDS_FULL
    for keyword, pct in keywords:
        if keyword in text and pct > best:
            best = pct
    return best


def _run_flow_thread(rd, cs):
    """在后台线程中执行完整流程 (cs = config_snapshot)"""
    try:
        cfg = Config()
        cfg.proxy = cs["proxy"]
        cfg.mail.email_domain = cs["mail_domain"]
        cfg.mail.worker_domain = cs["mail_worker"]
        cfg.mail.admin_token = cs["mail_token"]
        cfg.team_plan.workspace_name = cs["workspace_name"]
        cfg.team_plan.seat_quantity = cs["seat_quantity"]
        cfg.team_plan.promo_campaign_id = cs["promo_campaign"]
        cfg.captcha = CaptchaConfig(api_url=cs["captcha_api_url"], client_key=cs["captcha_key"])
        cfg.billing = BillingInfo(
            name=cs["billing_name"], email="",
            country=cs["country_code"], currency=cs["currency"],
            address_line1=cs["address_line1"], address_state=cs["address_state"],
            postal_code=cs["postal_code"])
        if cs["do_payment"]:
            cfg.card = CardInfo(number=cs["card_number"], cvc=cs["card_cvc"],
                                exp_month=cs["exp_month"], exp_year=cs["exp_year"])

        store = ResultStore(output_dir=OUTPUT_DIR)
        auth_result = None
        af = None

        if cs["do_register"]:
            mp = MailProvider(worker_domain=cfg.mail.worker_domain, admin_token=cfg.mail.admin_token, email_domain=cfg.mail.email_domain)
            af = AuthFlow(cfg)
            if cs.get("register_mode") == "password":
                auth_result = af.run_register_with_password(mp, password=cs.get("default_password", ""))
            else:
                auth_result = af.run_register(mp)
            rd.update(auth_result.to_dict())  # 完整写入所有字段 (含 refresh_token 等)
            store.save_credentials(auth_result.to_dict())
            store.append_credentials_csv(auth_result.to_dict())
        elif cs["use_existing_creds"] and cs["do_checkout"]:
            if not cs["cred_access_token"]:
                raise RuntimeError("必须提供 access_token")
            af = AuthFlow(cfg)
            auth_result = af.from_existing_credentials(
                session_token=cs["cred_session_token"],
                access_token=cs["cred_access_token"],
                device_id=cs["cred_device_id"],
            )
            auth_result.email = cs["cred_email"] or "unknown@example.com"
            rd["email"] = auth_result.email

        if cs["do_checkout"]:
            if not auth_result:
                raise RuntimeError("需先注册或提供凭证")

            if cs["use_browser_mode"] and cs["do_payment"]:
                import subprocess as _sp
                import sys as _sys
                if _sys.platform.startswith("linux"):
                    _xvfb_ok = False
                    try:
                        _sp.check_output(["pgrep", "-f", "Xvfb :99"], stderr=_sp.DEVNULL)
                        _xvfb_ok = True
                    except Exception:
                        pass
                    if not _xvfb_ok:
                        _sp.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac"],
                                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                        import time as _t; _t.sleep(1)
                    os.environ["DISPLAY"] = ":99"

                from browser_payment import BrowserPayment
                bp = BrowserPayment(proxy=cfg.proxy, headless=False, slow_mo=80)
                br = bp.run_full_flow(
                    session_token=auth_result.session_token,
                    access_token=auth_result.access_token,
                    device_id=auth_result.device_id,
                    card_number=cs["card_number"], card_exp_month=cs["exp_month"],
                    card_exp_year=cs["exp_year"], card_cvc=cs["card_cvc"],
                    billing_name=cs["billing_name"], billing_country=cs["country_code"],
                    billing_zip=cs["postal_code"], billing_line1=cs["address_line1"],
                    billing_city=cs["address_city"], billing_state=cs["address_state"],
                    billing_email=auth_result.email, billing_currency=cs["currency"],
                    chatgpt_proxy=cfg.proxy, timeout=120,
                    plan_type=cs["plan_type"],
                )
                rd["checkout_data"] = br.get("checkout_data")
                rd["checkout_session_id"] = br.get("checkout_data", {}).get("checkout_session_id", "")
                rd["success"] = br.get("success", False)
                rd["error"] = br.get("error", "")
                rd["confirm_response"] = br

            else:
                cfg.billing.email = auth_result.email
                pf = PaymentFlow(cfg, auth_result)
                if af: pf.session = af.session
                cs_id = pf.create_checkout_session()
                rd["checkout_session_id"] = cs_id
                rd["checkout_data"] = pf.checkout_data
                pf.fetch_stripe_fingerprint()
                pf.extract_stripe_pk(pf.checkout_url)
                if cs["do_payment"]:
                    pf.payment_method_id = pf.create_payment_method()
                    pf.fetch_payment_page_details(cs_id)
                    pay = pf.confirm_payment(cs_id)
                    rd["confirm_status"] = pay.confirm_status
                    rd["confirm_response"] = pay.confirm_response
                    rd["success"] = pay.success
                    rd["error"] = pay.error
                else:
                    rd["success"] = True
        elif cs["do_register"]:
            rd["success"] = True

    except Exception as e:
        rd["error"] = str(e)
        logging.getLogger("ui").error(f"EXCEPTION: {traceback.format_exc()}")
    finally:
        rd["_done"] = True

    try:
        store = ResultStore(output_dir=OUTPUT_DIR)
        store.save_result(rd, "ui_run")
        if rd.get("email"):
            store.append_history(email=rd["email"], status="ui_run",
                                 checkout_session_id=rd.get("checkout_session_id", ""),
                                 payment_status=rd.get("confirm_status", ""),
                                 error=rd.get("error", ""))
    except Exception:
        pass



# ════════════════════════════════════════
# Widget 值备份/恢复 — 防止切换模式时 Streamlit 自动清理被隐藏 widget 的值
# Streamlit 行为: widget 不渲染时，其 key 会被从 session_state 中删除
# 解决方案: 用 _bak_xxx 前缀备份，widget 重新出现时从备份恢复
# ════════════════════════════════════════
_PRESERVE_WIDGET_KEYS = [
    # 邮箱配置 (do_register=False 时隐藏)
    "w_mail_worker_reg", "w_mail_domain_reg", "w_mail_token_reg",
    "w_register_mode", "w_default_password",
    # 卡片信息 (do_payment=False 时隐藏)
    "w_card_number", "w_card_cvc", "w_exp_month", "w_exp_year",
    # 账单地址 (do_payment=False 时隐藏)
    "w_billing_name", "w_currency", "w_address_line1", "w_address_city",
    "w_address_state", "w_postal_code", "w_country",
    # 代理
    "w_proxy",
]

# 备份: 将当前 widget 值复制到 _bak_ 前缀 key
for _wk in _PRESERVE_WIDGET_KEYS:
    if _wk in st.session_state:
        st.session_state[f"_bak_{_wk}"] = st.session_state[_wk]

# 恢复: 如果 widget key 被 Streamlit 清理了，从备份恢复
for _wk in _PRESERVE_WIDGET_KEYS:
    if _wk not in st.session_state and f"_bak_{_wk}" in st.session_state:
        st.session_state[_wk] = st.session_state[f"_bak_{_wk}"]


# ════════════════════════════════════════
# ════════════════════════════════════════
# 布局分栏 (左中右)
# ════════════════════════════════════════
# 左右顶格展示，三块三等分
col_left, col_mid, col_right = st.columns([1, 1, 1], gap="medium")

with col_left:
    # ── 执行模式选择 ──
    # 从数据库获取当前兑换码的有 token 的执行记录 (用于「选择已有账号」)
    _code_history = get_code_history(st.session_state.verified_code) if _ENABLE_CODE_SYSTEM else []
    _code_success_creds = []
    for _h in _code_history:
        if _h.get("result_json"):
            try:
                _rd = json.loads(_h["result_json"])
                if _rd.get("email") and _rd.get("access_token"):
                    _code_success_creds.append(_rd)
            except Exception:
                pass

    mode_col, proxy_col = st.columns([3, 2])
    with mode_col:
        flow_mode = st.radio(
            "执行模式",
            ["仅注册", "仅绑卡", "注册 + 绑卡"],
            index=2,  # 默认二合一
            horizontal=True,
            key="w_flow_mode",
        )

    # 根据模式派生控制标志
    do_register = flow_mode in ["仅注册", "注册 + 绑卡"]
    do_checkout = flow_mode in ["仅绑卡", "注册 + 绑卡"]
    do_payment = flow_mode in ["仅绑卡", "注册 + 绑卡"]

    if dev_mode:
        with proxy_col:
            sc1, sc2 = st.columns(2)
            do_checkout = sc1.checkbox("创建 Checkout", value=do_checkout)
            do_payment = sc2.checkbox("提交支付", value=do_payment)

    with proxy_col:
        proxy = st.text_input("代理", placeholder="http://127.0.0.1:7897", key="w_proxy", on_change=_on_config_change)

    # ── 仅绑卡模式: 已有账号选择 / Token 输入 ──
    cred_email = ""
    cred_session_token = ""
    cred_access_token = ""
    cred_device_id = ""
    use_existing_creds = (flow_mode == "仅绑卡")

    if flow_mode == "仅绑卡":
        account_source = st.radio(
            "账号来源",
            ["选择已有账号", "手动输入 Token"],
            index=0 if _code_success_creds else 1,
            horizontal=True,
        )

        if account_source == "选择已有账号":
            if _code_success_creds:
                _cred_options = {}
                for _cd in _code_success_creds:
                    _label = f"{_cd.get('email', '未知')}"
                    _cred_options[_label] = _cd
                if _cred_options:
                    sel_label = st.selectbox("选择账号", list(_cred_options.keys()), key="w_acct_select")
                    _sel_data = _cred_options[sel_label]
                    cred_email = _sel_data.get("email", "")
                    cred_session_token = _sel_data.get("session_token", "")
                    cred_access_token = _sel_data.get("access_token", "")
                    cred_device_id = _sel_data.get("device_id", "")
                    with st.expander("查看凭证详情", expanded=False):
                        st.json({k: (v[:40] + "..." if isinstance(v, str) and len(v) > 50 else v) for k, v in _sel_data.items()})
                else:
                    st.warning("未找到有效的凭证")
            else:
                st.warning("暂无已注册的账号，请先使用「仅注册」模式注册")

        elif account_source == "手动输入 Token":
            cred_access_token = st.text_input("access_token", placeholder="eyJhbGciOi...", type="password", key="w_manual_at")
            cred_session_token = st.text_input("session_token", placeholder="eyJhbGciOi...", type="password", key="w_manual_st",
                                                help="浏览器 F12 → Application → Cookies → __Secure-next-auth.session-token")
            cred_email = st.text_input("邮箱 (可选)", placeholder="user@example.com", key="w_manual_email")

    # ── 注册模式下显示邮箱配置 ──
    if do_register:
        with st.expander("邮箱配置", expanded=True):
            _mc1, _mc2, _mc3 = st.columns(3)
            mail_worker = _mc1.text_input("Worker API", placeholder="https://mail-api.example.com", key="w_mail_worker_reg", on_change=_on_config_change)
            mail_domain = _mc2.text_input("邮箱域名", placeholder="example.com", key="w_mail_domain_reg", on_change=_on_config_change)
            mail_token = _mc3.text_input("密码", placeholder="your-mail-token", type="password", key="w_mail_token_reg", on_change=_on_config_change)
        with st.expander("注册模式", expanded=True):
            _rm_col1, _rm_col2 = st.columns(2)
            register_mode_label = _rm_col1.radio(
                "注册方式",
                ["OTP 注册", "密码注册"],
                index=1 if st.session_state.get("w_register_mode", "OTP 注册") != "OTP 注册" else 0,
                horizontal=True,
                key="w_register_mode",
                on_change=_on_config_change,
            )
            is_password_mode = "密码" in (register_mode_label or "")
            if is_password_mode:
                default_password = _rm_col2.text_input(
                    "注册密码", placeholder="留空则自动生成 14 位密码",
                    key="w_default_password", on_change=_on_config_change,
                    help="密码要求: 至少1个大写、1个小写、1个数字、1个特殊字符",
                )
            else:
                default_password = ""
    else:
        is_password_mode = False
        default_password = ""


    # 默认值 (非开发者模式下不显示这些设置)
    use_browser_mode = True
    captcha_key = ""
    captcha_api_url = ""
    if not do_register:
        # 非注册模式时，邮箱配置使用空默认值 (开发者模式下有单独的输入框)
        mail_worker = ""
        mail_domain = ""
        mail_token = ""
    # 计划类型选择 (仅绑卡/注册+绑卡时显示)
    if do_checkout:
        plan_type_label = st.radio(
            "选择计划",
            ["Business · 团队版免费试用 1 个月", "Plus · 个人版免费试用 1 个月"],
            index=0,
            horizontal=True,
        )
        plan_type = "plus" if "Plus" in plan_type_label else "team"
    else:
        plan_type = "team"  # 仅注册模式下不需要计划选择
    if plan_type == "plus":
        workspace_name = ""
        seat_quantity = 0
        promo_campaign = "plus-1-month-free"
    else:
        workspace_name = "MyWorkspace"
        seat_quantity = 5
        promo_campaign = "team-1-month-free"

    if dev_mode:
        with st.expander("高级设置", expanded=False):
            adv_col1, adv_col2 = st.columns(2)
            with adv_col1:
                payment_mode = st.radio(
                    "支付模式",
                    ["浏览器模式 (推荐)", "API 模式"],
                    index=0,
                    horizontal=True,
                )
                use_browser_mode = payment_mode.startswith("浏览")
            with adv_col2:
                if use_browser_mode:
                    import subprocess as _sp
                    _xvfb_running = False
                    try:
                        _xvfb_pids = _sp.check_output(["pgrep", "-f", "Xvfb :99"], stderr=_sp.DEVNULL).decode().strip()
                        _xvfb_running = bool(_xvfb_pids)
                    except Exception:
                        pass
                    if _xvfb_running:
                        st.success("Xvfb 运行中 (:99)")
                    else:
                        st.info("将自动启动 Xvfb :99")
                else:
                    st.info("API 模式")

            if not use_browser_mode:
                captcha_col1, captcha_col2 = st.columns([3, 1])
                with captcha_col1:
                    captcha_key = st.text_input("YesCaptcha API Key", placeholder="your-yescaptcha-key", type="password")
                with captcha_col2:
                    captcha_api_url = st.text_input("打码 API", value="https://api.yescaptcha.com")

            st.markdown("---")
            st.markdown("**邮箱 & 计划设置**")
            if not do_register:
                mail_worker = st.text_input("邮箱 Worker", placeholder="https://mail-api.example.com", key="w_mail_worker_dev")
                adv_mc1, adv_mc2 = st.columns(2)
                mail_domain = adv_mc1.text_input("邮箱域名", placeholder="example.com", key="w_mail_domain_dev")
                mail_token = adv_mc2.text_input("密码", placeholder="your-mail-token", type="password", key="w_mail_token_dev")
            if plan_type == "team":
                adv_tc1, adv_tc2, adv_tc3 = st.columns(3)
                workspace_name = adv_tc1.text_input("Workspace", value="MyWorkspace")
                seat_quantity = adv_tc2.number_input("席位数", min_value=2, max_value=50, value=5)
                promo_campaign = adv_tc3.text_input("活动 ID", value="team-1-month-free")
            else:
                promo_campaign = st.text_input("活动 ID", value="plus-1-month-free")

    # ════════════════════════════════════════
    # 配置区: 卡片信息优先 (去除原来的 divider 避免上方留白)
    # ════════════════════════════════════════

    if do_payment:
        with st.expander("粘贴卡片信息", expanded=True):
            paste_text = st.text_area(
                "粘贴卡片/账单文本",
                height=120,
                placeholder="支持两种格式:\n\n格式1 (键值对):\n卡号: 4242424242424242\n有效期: 1230\nCVV: 123\n姓名: John Smith\n地址: 123 Main Street\n城市: San Francisco\n州: CA\n邮编: 94102\n国家: United States\n\n格式2 (纯文本):\n4242 4242 4242 4242\n12/30\nCVV 123",
                key="paste_card_text",
            )
            if st.button("识别并填充", key="parse_btn", disabled=not paste_text):
                parsed = _parse_card_text(paste_text)
                pending = {}
                if parsed.get("card_number"):
                    pending["w_card_number"] = parsed["card_number"]
                if parsed.get("exp_month"):
                    pending["w_exp_month"] = parsed["exp_month"]
                if parsed.get("exp_year"):
                    pending["w_exp_year"] = parsed["exp_year"]
                if parsed.get("cvv"):
                    pending["w_card_cvc"] = parsed["cvv"]
                if parsed.get("address_line1"):
                    pending["w_address_line1"] = parsed["address_line1"]
                if parsed.get("address_city"):
                    pending["w_address_city"] = parsed["address_city"]
                if parsed.get("address_state"):
                    pending["w_address_state"] = parsed["address_state"]
                if parsed.get("postal_code"):
                    pending["w_postal_code"] = parsed["postal_code"]
                if parsed.get("country_code"):
                    cc = parsed["country_code"]
                    for i, label in enumerate(COUNTRY_MAP.keys()):
                        if label.startswith(cc):
                            pending["w_country"] = label
                            break
                if parsed.get("currency"):
                    pending["w_currency"] = parsed["currency"]
                if parsed.get("billing_name"):
                    pending["w_billing_name"] = parsed["billing_name"]
                st.session_state["_pending_parse"] = pending
                filled = []
                if parsed.get("card_number"):
                    filled.append(f"卡号: {parsed['card_number'][:4]}****{parsed['card_number'][-4:]}")
                if parsed.get("exp_month"):
                    filled.append(f"有效期: {parsed['exp_month']}/{parsed['exp_year']}")
                if parsed.get("cvv"):
                    filled.append(f"CVV: ***")
                if parsed.get("raw_address"):
                    filled.append(f"地址: {parsed['raw_address']}")
                if parsed.get("billing_name"):
                    filled.append(f"姓名: {parsed['billing_name']}")
                if filled:
                    st.success("已识别: " + " | ".join(filled))
                else:
                    st.warning("未能识别卡片信息，请检查文本格式")
                st.rerun()

    cfg_col1, cfg_col2 = st.columns(2)

    with cfg_col1:
        if do_payment:
            with st.expander("信用卡", expanded=True):
                TEST_CARDS = {
                    "4242 4242 4242 4242 (Visa 标准)": ("4242424242424242", "123"),
                    "4000 0000 0000 0002 (Visa 被拒)": ("4000000000000002", "123"),
                    "4000 0000 0000 0069 (Visa 过期)": ("4000000000000069", "123"),
                    "4000 0000 0000 9995 (Visa 余额不足)": ("4000000000009995", "123"),
                    "5555 5555 5555 4444 (Mastercard)": ("5555555555554444", "123"),
                    "5200 8282 8282 8210 (MC Debit)": ("5200828282828210", "123"),
                    "2223 0031 2200 3222 (MC 2系列)": ("2223003122003222", "123"),
                    "3782 822463 10005 (Amex)": ("378282246310005", "1234"),
                }
                tc_sel = st.selectbox("快速填充测试卡", ["不填充"] + list(TEST_CARDS.keys()), key="tc_sel")
                if tc_sel != "不填充":
                    tc_num, tc_cvc = TEST_CARDS[tc_sel]
                    st.session_state["w_card_number"] = tc_num
                    st.session_state["w_card_cvc"] = tc_cvc

                cc1, cc2, cc3, cc4 = st.columns([5, 2, 2, 2])
                card_number = cc1.text_input("卡号", placeholder="真实卡号", key="w_card_number", on_change=_on_config_change)
                exp_month = cc2.text_input("月", key="w_exp_month", on_change=_on_config_change)
                exp_year = cc3.text_input("年", key="w_exp_year", on_change=_on_config_change)
                card_cvc = cc4.text_input("CVC", key="w_card_cvc", on_change=_on_config_change)

                if card_number and card_number.startswith("4"):
                    st.caption("Live 模式下所有测试卡都会被拒绝，仅用于验证流程")
        else:
            card_number = exp_month = exp_year = card_cvc = ""

    with cfg_col2:
        if do_payment:
            with st.expander("账单地址", expanded=True):
                # 如果有解析出的国家，自动选择对应国家
                country_label = st.selectbox("国家", list(COUNTRY_MAP.keys()), key="w_country", on_change=_on_config_change)
                country_code, default_currency, default_state, default_addr, default_zip = COUNTRY_MAP[country_label]
                # 当国家变更时，更新地址默认值 (但不覆盖刚解析的值)
                _prev_country = st.session_state.get("_prev_country", "")
                if _prev_country and _prev_country != country_label and not _parse_just_applied:
                    st.session_state["w_currency"] = default_currency
                    st.session_state["w_address_line1"] = default_addr
                    st.session_state["w_address_state"] = default_state
                    st.session_state["w_postal_code"] = default_zip
                st.session_state["_prev_country"] = country_label
                bc1, bc2 = st.columns(2)
                billing_name = bc1.text_input("姓名", key="w_billing_name", on_change=_on_config_change)
                if "w_currency" not in st.session_state:
                    st.session_state["w_currency"] = default_currency
                currency = bc2.text_input("货币", key="w_currency", on_change=_on_config_change)
                bc3, bc4, bc5, bc6 = st.columns(4)
                if "w_address_line1" not in st.session_state:
                    st.session_state["w_address_line1"] = default_addr
                if "w_address_city" not in st.session_state:
                    st.session_state["w_address_city"] = ""
                if "w_address_state" not in st.session_state:
                    st.session_state["w_address_state"] = default_state
                if "w_postal_code" not in st.session_state:
                    st.session_state["w_postal_code"] = default_zip
                address_line1 = bc3.text_input("地址", key="w_address_line1", on_change=_on_config_change)
                address_city = bc4.text_input("城市", key="w_address_city", on_change=_on_config_change)
                address_state = bc5.text_input("州/省", key="w_address_state", on_change=_on_config_change)
                postal_code = bc6.text_input("邮编", key="w_postal_code", on_change=_on_config_change)
        else:
            billing_name = country_code = currency = ""
            address_line1 = address_city = address_state = postal_code = ""

    st.divider()


steps_list = []
if do_register: steps_list.append("注册")
if do_checkout: steps_list.append("Checkout")
if do_payment: steps_list.append("支付")


with col_mid:
    # 额度提示
    if flow_mode == "仅注册":
        st.info("🆕 仅注册模式: 消耗 **1** 次额度")
    elif flow_mode == "仅绑卡":
        st.info("💳 仅绑卡模式: 消耗 **1** 次额度")
    else:
        st.info("🔗 注册 + 绑卡模式: 成功消耗 **2** 次额度，失败消耗 **1** 次")
    btn_col1, btn_col2 = st.columns([4, 1])
    with btn_col1:
        run_btn = st.button("开始执行", disabled=st.session_state.running or not steps_list,
                            type="primary", use_container_width=True)
    with btn_col2:
        stop_btn = st.button("终止", disabled=not st.session_state.running, use_container_width=True)

    # ── 点击开始: 表单验证 → 验证兑换码 → 预留额度 → 启动线程 ──
    if run_btn and not st.session_state.running:
        # 表单验证 (按模式区分)
        _errors = []
        if do_register:
            if not mail_worker or not mail_worker.startswith("http"):
                _errors.append("请填写邮箱 Worker API 地址")
            if not mail_domain:
                _errors.append("请填写邮箱域名")
            if not mail_token:
                _errors.append("请填写密码")
        if use_existing_creds and do_checkout:
            if not cred_access_token:
                _errors.append("请提供 access_token")
        if do_payment:
            if not st.session_state.get("w_card_number", ""):
                _errors.append("请填写信用卡卡号")
        if _errors:
            for _e in _errors:
                st.error(_e)
            st.stop()

        # 再次验证兑换码
        if _ENABLE_CODE_SYSTEM:
            _v, _vm = validate_code(st.session_state.verified_code)
            if not _v:
                st.error(f"兑换码不可用: {_vm}")
                st.stop()

        # 预留使用额度: 注册+绑卡=2, 其他=1
        if _ENABLE_CODE_SYSTEM:
            _reserve_amount = 2 if (flow_mode == "注册 + 绑卡") else 1
            _exec_id = reserve_use(st.session_state.verified_code, plan_type=plan_type, amount=_reserve_amount)
            if _exec_id is None:
                st.error("兑换码额度不足")
                st.stop()
        else:
            _exec_id = None

        st.session_state._execution_id = _exec_id
        if _exec_id:
            update_execution(_exec_id, status="running")

        # 记录当前执行模式 (供进度条使用)
        st.session_state["_active_flow_mode"] = flow_mode

        # 保存当前配置到 config.json
        _save_config_to_file(
            proxy=proxy, mail_worker=mail_worker,
            mail_domain=mail_domain, mail_token=mail_token,
        )

        st.session_state._flow_config = {
            "proxy": proxy or None,
            "mail_domain": mail_domain, "mail_worker": mail_worker, "mail_token": mail_token,
            "workspace_name": workspace_name, "seat_quantity": seat_quantity, "promo_campaign": promo_campaign,
            "plan_type": plan_type,
            "captcha_api_url": captcha_api_url, "captcha_key": captcha_key,
            "billing_name": billing_name if do_payment else "",
            "country_code": country_code if do_payment else "US",
            "currency": currency if do_payment else "USD",
            "address_line1": address_line1 if do_payment else "",
            "address_city": address_city if do_payment else "",
            "address_state": address_state if do_payment else "",
            "postal_code": postal_code if do_payment else "",
            "card_number": card_number if do_payment else "",
            "card_cvc": card_cvc if do_payment else "",
            "exp_month": exp_month if do_payment else "",
            "exp_year": exp_year if do_payment else "",
            "do_register": do_register, "do_checkout": do_checkout, "do_payment": do_payment,
            "use_existing_creds": use_existing_creds, "use_browser_mode": use_browser_mode,
            "cred_session_token": cred_session_token, "cred_access_token": cred_access_token,
            "cred_device_id": cred_device_id, "cred_email": cred_email,
            "register_mode": "password" if is_password_mode else "otp",
            "default_password": default_password,
        }
        st.session_state._flow_result = {"success": False, "error": "", "email": "", "steps": {}}
        st.session_state.running = True
        st.session_state.log_buffer = []
        st.session_state.result = None
        clear_captured_logs()
        init_logging()
        _t = threading.Thread(
            target=_run_flow_thread,
            args=(st.session_state._flow_result, st.session_state._flow_config),
            daemon=True,
        )
        _t.start()
        st.rerun()

    # ── 点击终止 ──
    if stop_btn and st.session_state.running:
        import subprocess as _sp
        try:
            _sp.run(["pkill", "-f", "remote-debugging-port"], capture_output=True)
        except Exception:
            pass
        st.session_state.running = False
        st.session_state.result = {"success": False, "error": "用户手动终止", "email": ""}
        # 终止不扣额度
        _eid = st.session_state.get("_execution_id")
        if _eid:
            complete_use(_eid, success=False, error_msg="用户手动终止")
            st.session_state._execution_id = None
        st.warning("已终止执行")
        st.rerun()

    # ── 运行中: 显示进度 + 实时日志 ──
    if st.session_state.running:
        pct = _calc_progress_pct()
        st.progress(pct / 100.0)
        st.markdown(
            f'<div style="text-align:center;font-size:28px;font-weight:bold;margin:-15px 0 10px;color:#2d3436">{pct}%</div>',
            unsafe_allow_html=True,
        )

        # ── 实时日志: 必须在 st.rerun() 之前渲染，否则不会被执行到 ──
        st.markdown('<div style="margin-top: 10px; margin-bottom: 5px; font-weight: bold; font-size: 16px;">执行日志</div>', unsafe_allow_html=True)
        pull_captured_logs()
        import streamlit.components.v1 as _components
        import html as _html_mod
        if st.session_state.log_buffer:
            _log_text = "\n".join(st.session_state.log_buffer[-150:])
            _log_escaped = _html_mod.escape(_log_text)
            _components.html(f"""
                <style>
                    body {{ text-size-adjust: 100%; -webkit-text-size-adjust: 100%; margin: 0; padding: 0; }}
                    #log-box {{
                        height: 720px;
                        overflow-y: auto;
                        background: #f8f9fb;
                        color: #31333f;
                        font-family: "Source Code Pro", "Courier New", monospace;
                        font-size: 14px;
                        line-height: 1.5;
                        padding: 1rem;
                        border-radius: 0.5rem;
                        border: 1px solid rgba(49, 51, 63, 0.2);
                        white-space: pre-wrap;
                        word-wrap: break-word;
                        overflow-wrap: break-word;
                    }}
                </style>
                <div id="log-box"><pre style="margin:0;white-space:pre-wrap;word-wrap:break-word;">{_log_escaped}</pre></div>
                <script>
                    var box = document.getElementById('log-box');
                    box.scrollTop = box.scrollHeight;
                    setTimeout(function(){{ box.scrollTop = box.scrollHeight; }}, 50);
                </script>
            """, height=750)
        else:
            st.info("等待执行...")

        rd = st.session_state.get("_flow_result", {})
        if rd.get("_done"):
            st.session_state.running = False
            st.session_state.result = rd
            # ── 完成兑换码计次 ──
            _eid = st.session_state.get("_execution_id")
            if _eid:
                complete_use(
                    _eid,
                    success=rd.get("success", False),
                    email=rd.get("email", ""),
                    error_msg=rd.get("error", ""),
                    result_json=json.dumps(rd, ensure_ascii=False, default=str),
                )
                st.session_state._execution_id = None
            # ── 同步保存配置到兑换码数据库 (与账号数据持久化时机一致) ──
            _cur_code = st.session_state.get("verified_code", "")
            if _ENABLE_CODE_SYSTEM and _cur_code and _cur_code != "__disabled__":
                try:
                    save_code_config(_cur_code, _collect_config_from_session())
                except Exception:
                    pass
            st.rerun()
        else:
            import time as _time
            _time.sleep(1)
            st.rerun()

    # ── 显示结果 ──
    if st.session_state.result and not st.session_state.running:
        r = st.session_state.result
        if r.get("success"):
            st.progress(1.0)
            st.success(f"全部完成 — {r.get('email', '')}")
        elif r.get("error"):
            st.error(_sanitize_error(r.get('error', '')))

        if dev_mode:
            st.divider()
            cols = st.columns(4)
            cols[0].metric("邮箱", r.get("email") or "-")
            cols[1].metric("Checkout", (r.get("checkout_session_id", "")[:20] + "...") if r.get("checkout_session_id") else "-")
            cols[2].metric("Confirm", r.get("confirm_status") or "-")
            cols[3].metric("状态", "成功" if r.get("success") else "失败")
            if r.get("confirm_response"):
                with st.expander("Stripe 原始响应", expanded=False):
                    st.json(r["confirm_response"])

    # ── 非运行中的日志区 (完成后 / 空闲时显示) ──
    if not st.session_state.running:
        st.markdown('<div style="margin-top: 10px; margin-bottom: 5px; font-weight: bold; font-size: 16px;">执行日志</div>', unsafe_allow_html=True)
        pull_captured_logs()
        import streamlit.components.v1 as _components
        import html as _html_mod
        if st.session_state.log_buffer:
            _log_text = "\n".join(st.session_state.log_buffer[-150:])
            _log_escaped = _html_mod.escape(_log_text)
            _components.html(f"""
                <style>
                    body {{ text-size-adjust: 100%; -webkit-text-size-adjust: 100%; margin: 0; padding: 0; }}
                    #log-box {{
                        height: 720px;
                        overflow-y: auto;
                        background: #f8f9fb;
                        color: #31333f;
                        font-family: "Source Code Pro", "Courier New", monospace;
                        font-size: 14px;
                        line-height: 1.5;
                        padding: 1rem;
                        border-radius: 0.5rem;
                        border: 1px solid rgba(49, 51, 63, 0.2);
                        white-space: pre-wrap;
                        word-wrap: break-word;
                        overflow-wrap: break-word;
                    }}
                </style>
                <div id="log-box"><pre style="margin:0;white-space:pre-wrap;word-wrap:break-word;">{_log_escaped}</pre></div>
                <script>
                    var box = document.getElementById('log-box');
                    box.scrollTop = box.scrollHeight;
                    setTimeout(function(){{ box.scrollTop = box.scrollHeight; }}, 50);
                </script>
            """, height=750)
        else:
            st.info("等待执行...")

with col_right:
    tab_accounts, tab_history, tab_sync = st.tabs(["账号", "历史", "同步"])


    # Tab: 账号
    # ════════════════════════════════════════
    with tab_accounts:
        _history = get_code_history(st.session_state.verified_code)
        # 显示所有有邮箱的账号 (注册成功的, 不管支付是否成功)
        _acct_rows = []
        for r in _history:
            if r.get("result_json"):
                try:
                    rd = json.loads(r["result_json"])
                    if rd.get("email"):
                        _acct_rows.append({
                            "exec_id": r["id"],
                            "email": rd["email"],
                            "plan_type": r.get("plan_type") or "-",
                            "status": r["status"],
                            "created_at": r["created_at"][:19],
                            "has_token": bool(rd.get("access_token")),
                            "_data": rd,
                        })
                except Exception:
                    pass

        if _acct_rows:
            import pandas as pd
            _disp_rows = []
            for a in _acct_rows:
                _disp_rows.append({
                    "邮箱": a["email"],
                    "计划": a["plan_type"],
                    "支付": "✅ 成功" if a["status"] == "success" else "❌ 失败",
                    "时间": a["created_at"],
                })
            st.dataframe(pd.DataFrame(_disp_rows), hide_index=True, use_container_width=True)
            st.caption(f"共 {len(_acct_rows)} 个账号")

            st.divider()
            for idx, acct in enumerate(_acct_rows):
                _data = acct["_data"]
                with st.expander(f"{acct['email']}  {'✅' if acct['status'] == 'success' else '❌'}", expanded=False):
                    if _data.get("access_token"):
                        st.code(
                            f"access_token: {_data.get('access_token', 'N/A')}\n"
                            f"session_token: {_data.get('session_token', 'N/A')}\n"
                            f"device_id: {_data.get('device_id', 'N/A')}",
                            language="yaml",
                        )
                    else:
                        st.caption("无 Token 信息")
        else:
            st.info("暂无已注册的账号。执行完成后自动显示。")

        if st.button("刷新", key="ref_acc"):
            st.rerun()


    # ════════════════════════════════════════
    # Tab: 历史
    # ════════════════════════════════════════
    with tab_history:
        _history = get_code_history(st.session_state.verified_code)
        if _history:
            import pandas as pd
            _disp = []
            for r in _history:
                _disp.append({
                    "状态": {"success": "✅ 成功", "failed": "❌ 失败", "running": "🔄 运行中", "pending": "⏳ 等待"}.get(r["status"], r["status"]),
                    "邮箱": r.get("email") or "-",
                    "计划": r.get("plan_type") or "-",
                    "备注": _sanitize_error(r.get("error_msg") or "") if r["status"] == "failed" else "",
                    "时间": r["created_at"][:19],
                })
            st.dataframe(pd.DataFrame(_disp), hide_index=True, use_container_width=True)
            st.caption(f"共 {len(_history)} 条记录")
        else:
            st.info("暂无执行历史")

        if st.button("刷新", key="ref_hist"):
            st.rerun()


    # ════════════════════════════════════════
    # Tab: NewAPI 同步
    # ════════════════════════════════════════
    with tab_sync:
        st.subheader("NewAPI 渠道同步")
        st.caption("将注册生成的 credentials 自动导入到 NewAPI 平台")

        # ── 配置区域 ──
        with st.expander("NewAPI 配置", expanded=False):
            _nc1, _nc2 = st.columns(2)
            _newapi_base = _nc1.text_input(
                "API 地址", placeholder="http://your-newapi.com",
                key="w_newapi_base", on_change=_on_config_change,
            )
            _newapi_token = _nc2.text_input(
                "Admin Token", type="password", placeholder="Bearer Token",
                key="w_newapi_token", on_change=_on_config_change,
            )
            _nc3, _nc4, _nc5 = st.columns(3)
            _newapi_type = _nc3.text_input(
                "渠道类型 (type)", value=st.session_state.get("w_newapi_type", "57"),
                key="w_newapi_type", on_change=_on_config_change,
                help="57 = OpenAI Codex",
            )
            _newapi_group = _nc4.text_input(
                "分组 (group)", value=st.session_state.get("w_newapi_group", "default,vip,svip"),
                key="w_newapi_group", on_change=_on_config_change,
            )
            _nc5a, _nc5b = _nc5.columns(2)
            _newapi_priority = _nc5a.text_input(
                "优先级", value=st.session_state.get("w_newapi_priority", "0"),
                key="w_newapi_priority", on_change=_on_config_change,
            )
            _newapi_weight = _nc5b.text_input(
                "权重", value=st.session_state.get("w_newapi_weight", "0"),
                key="w_newapi_weight", on_change=_on_config_change,
            )
            _newapi_models = st.text_area(
                "模型列表 (逗号分隔)",
                value=st.session_state.get("w_newapi_models",
                    "gpt-5,gpt-5-codex,gpt-5-codex-mini,gpt-5.1,gpt-5.1-codex,gpt-5.1-codex-max,"
                    "gpt-5.1-codex-mini,gpt-5.2,gpt-5.2-codex,gpt-5.3-codex"),
                key="w_newapi_models", on_change=_on_config_change,
                height=80,
            )

        # ── 获取当前兑换码下的 credentials 数据 ──
        _cred_data_list = []
        if _ENABLE_CODE_SYSTEM:
            # 从兑换码历史中提取有 refresh_token/access_token 的记录
            _sync_history = get_code_history(st.session_state.verified_code)
            for _h in _sync_history:
                if _h.get("result_json"):
                    try:
                        _rd = json.loads(_h["result_json"])
                        if _rd.get("email") and (_rd.get("access_token") or _rd.get("refresh_token")):
                            _cred_data_list.append(_rd)
                    except Exception:
                        pass
        else:
            # 兑换码系统未启用时，回退到扫描 credentials 文件
            _cred_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_DIR)
            if os.path.isdir(_cred_dir):
                _cred_files = sorted(
                    [f for f in os.listdir(_cred_dir) if f.startswith("credentials_") and f.endswith(".json")],
                    reverse=True,
                )
                for _cf in _cred_files:
                    _cf_path = os.path.join(_cred_dir, _cf)
                    try:
                        with open(_cf_path, encoding="utf-8") as _f:
                            _cd = json.load(_f)
                        _cd["_filename"] = _cf
                        _cd["_filepath"] = _cf_path
                        _cred_data_list.append(_cd)
                    except Exception:
                        pass

        if not _cred_data_list:
            st.info("当前兑换码下暂无可同步的账号。注册成功后自动显示。")
        else:
            # ── 导入函数 (成功后回写 synced 标记) ──
            def _do_newapi_import(cred_list, base_url, admin_token, ch_type, models, group, priority, weight):
                """将 credentials 列表导入到 NewAPI，成功后标记 synced_to_newapi"""
                import requests as _req
                results = []
                headers = {
                    "Authorization": f"Bearer {admin_token}",
                    "Content-Type": "application/json",
                    "New-Api-User": "1",
                }
                base_url = base_url.rstrip("/")
                for cd in cred_list:
                    email = cd.get("email", "unknown")
                    # 构建 key: 仅保留 credentials 字段 (与 auth_result.to_dict() 一致)
                    _CRED_KEYS = {
                        "type", "email", "expired", "id_token", "account_id",
                        "access_token", "last_refresh", "refresh_token",
                        "session_token", "device_id", "csrf_token", "password",
                    }
                    key_data = {k: v for k, v in cd.items() if k in _CRED_KEYS}
                    key_json = json.dumps(key_data, ensure_ascii=False)
                    payload = {
                        "mode": "single",
                        "channel": {
                            "type": int(ch_type) if ch_type.isdigit() else 57,
                            "name": f"codex-{email}",
                            "key": key_json,
                            "models": models.strip(),
                            "group": group.strip(),
                            "priority": int(priority) if str(priority).lstrip("-").isdigit() else 0,
                            "weight": int(weight) if str(weight).lstrip("-").isdigit() else 0,
                        },
                    }
                    try:
                        resp = _req.post(
                            f"{base_url}/api/channel/",
                            headers=headers, json=payload, timeout=30,
                        )
                        if resp.status_code == 200:
                            try:
                                rj = resp.json()
                                if rj.get("success"):
                                    results.append((email, True, "成功"))
                                    # 回写 synced 标记到 JSON 文件
                                    _mark_synced(cd)
                                else:
                                    results.append((email, False, rj.get("message", resp.text[:200])))
                            except Exception:
                                results.append((email, False, f"返回非 JSON: {resp.text[:200]}"))
                        else:
                            results.append((email, False, f"HTTP {resp.status_code}: {resp.text[:200]}"))
                    except Exception as e:
                        results.append((email, False, f"请求异常: {str(e)[:200]}"))
                return results

            def _mark_synced(cd):
                """导入成功后在 JSON 文件中标记 synced_to_newapi=true"""
                filepath = cd.get("_filepath")
                if not filepath:
                    return
                try:
                    with open(filepath, encoding="utf-8") as f:
                        data = json.load(f)
                    data["synced_to_newapi"] = True
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    cd["synced_to_newapi"] = True  # 同步内存状态
                except Exception:
                    pass

            # ── 列表 (固定高度滚动) ──
            with st.container(height=680):
                _hdr = st.columns([2.5, 1.2, 1.2, 1, 1])
                _hdr[0].markdown("**邮箱**")
                _hdr[1].markdown("**refresh_token**")
                _hdr[2].markdown("**access_token**")
                _hdr[3].markdown("**状态**")
                _hdr[4].markdown("**操作**")

                for _idx, _cd in enumerate(_cred_data_list):
                    _email = _cd.get("email", "unknown")
                    _has_rt = bool(_cd.get("refresh_token"))
                    _is_synced = bool(_cd.get("synced_to_newapi"))
                    _row = st.columns([2.5, 1.2, 1.2, 1, 1])
                    _row[0].text(_email)
                    _row[1].markdown("✅" if _has_rt else "❌")
                    _row[2].markdown("✅" if _cd.get("access_token") else "❌")
                    _row[3].markdown("✅ 已导入" if _is_synced else "⏳ 待导入")
                    with _row[4]:
                        if st.button(
                            "已导入" if _is_synced else "导入",
                            key=f"sync_single_{_idx}",
                            disabled=not _newapi_base or not _newapi_token or not _has_rt or _is_synced,
                        ):
                            with st.spinner(f"导入 {_email}..."):
                                _sr = _do_newapi_import(
                                    [_cd], _newapi_base, _newapi_token,
                                    _newapi_type, _newapi_models, _newapi_group,
                                    _newapi_priority, _newapi_weight,
                                )
                            if _sr and _sr[0][1]:
                                st.success(f"✅ {_email} 导入成功")
                                st.rerun()
                            elif _sr:
                                st.error(f"❌ {_email}: {_sr[0][2]}")

            # ── 统计 ──
            _synced_count = sum(1 for cd in _cred_data_list if cd.get("synced_to_newapi"))
            st.caption(f"共 {len(_cred_data_list)} 个账号，已导入 {_synced_count} 个")

            # ── 全部导入 (过滤已同步 + 无 refresh_token) ──
            _syncable = [cd for cd in _cred_data_list
                         if cd.get("refresh_token") and not cd.get("synced_to_newapi")]
            _sync_col1, _sync_col2 = st.columns([3, 1])
            with _sync_col1:
                _sync_all_btn = st.button(
                    f"全部导入 ({len(_syncable)} 个待同步)",
                    type="primary", use_container_width=True, key="sync_all_btn",
                    disabled=not _newapi_base or not _newapi_token or not _syncable,
                )
            with _sync_col2:
                if st.button("刷新列表", key="sync_refresh"):
                    st.rerun()

            if not _newapi_base or not _newapi_token:
                st.warning("请先配置 API 地址和 Admin Token")

            if _sync_all_btn:
                with st.spinner(f"正在导入 {len(_syncable)} 个账号..."):
                    _import_results = _do_newapi_import(
                        _syncable, _newapi_base, _newapi_token,
                        _newapi_type, _newapi_models, _newapi_group,
                        _newapi_priority, _newapi_weight,
                    )
                _ok = sum(1 for _, s, _ in _import_results if s)
                _fail = sum(1 for _, s, _ in _import_results if not s)
                if _ok:
                    st.success(f"✅ 成功导入 {_ok} 个账号")
                if _fail:
                    st.error(f"❌ 失败 {_fail} 个账号")
                for _email, _succ, _msg in _import_results:
                    if _succ:
                        st.write(f"✅ {_email}")
                    else:
                        st.write(f"❌ {_email}: {_msg}")
                if _ok:
                    st.rerun()

# ════════════════════════════════════════
# 所有 widget 渲染完毕后, 统一保存配置到兑换码数据库
# (on_change 回调在 widget 渲染前执行, 其他 tab 的值可能不完整,
#  这里保证收集到所有 tab 的最新值)
# ════════════════════════════════════════
_cur_code = st.session_state.get("verified_code", "")
if _ENABLE_CODE_SYSTEM and _cur_code and _cur_code != "__disabled__":
    try:
        save_code_config(_cur_code, _collect_config_from_session())
    except Exception:
        pass
