"""
注册/登录流程 - 协议直连方式
完整链路:
  chatgpt_csrf -> chatgpt_signin_openai -> auth_oauth_init -> sentinel
  -> signup -> send_otp -> verify_otp -> create_account
  -> redirect_chain -> auth_session -> (optional) oauth_token_exchange
"""
import base64
import hashlib
import json
import logging
import random
import re
import secrets
import string
import time
import uuid
from typing import Optional
from urllib.parse import urlparse, parse_qs, urljoin, urlencode

from config import Config
from mail_provider import MailProvider
from http_client import create_http_session

logger = logging.getLogger(__name__)


# ── Codex OAuth 常量 ──
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_ISSUER = "https://auth.openai.com"


def generate_password(length: int = 14) -> str:
    """生成随机密码: 至少1个小写/大写/数字/特殊字符"""
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%&*"
    pwd = [
        random.choice(lower),
        random.choice(upper),
        random.choice(digits),
        random.choice(special),
    ]
    all_chars = lower + upper + digits + special
    pwd += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


def _generate_pkce() -> tuple:
    """生成 PKCE code_verifier 和 code_challenge"""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _extract_code_from_url(url: str) -> Optional[str]:
    """从 URL 中提取 authorization code"""
    if not url or "code=" not in url:
        return None
    try:
        return parse_qs(urlparse(url).query).get("code", [None])[0]
    except Exception:
        return None


def _decode_jwt_payload(token: str) -> dict:
    """解码 JWT payload（不校验签名，仅提取数据）"""
    if not token:
        return {}
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        # 补齐 base64 padding
        pad = 4 - len(payload_b64) % 4
        if pad != 4:
            payload_b64 += "=" * pad
        raw = base64.urlsafe_b64decode(payload_b64)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


class AuthResult:
    """认证结果"""

    def __init__(self):
        self.email: str = ""
        self.password: str = ""
        self.session_token: str = ""
        self.access_token: str = ""
        self.device_id: str = ""
        self.csrf_token: str = ""
        self.id_token: str = ""
        self.refresh_token: str = ""

    def is_valid(self) -> bool:
        return bool(self.session_token and self.access_token)

    def to_dict(self) -> dict:
        from datetime import datetime, timezone, timedelta

        # 从 access_token JWT 解析 expired 和 account_id
        payload = _decode_jwt_payload(self.access_token)
        auth_info = payload.get("https://api.openai.com/auth", {})
        account_id = auth_info.get("chatgpt_account_id", "")

        # expired: 从 JWT exp 转为 ISO 8601 +08:00
        expired_str = ""
        exp_timestamp = payload.get("exp")
        if isinstance(exp_timestamp, int) and exp_timestamp > 0:
            exp_dt = datetime.fromtimestamp(exp_timestamp, tz=timezone(timedelta(hours=8)))
            expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        # last_refresh: 当前时间
        now = datetime.now(tz=timezone(timedelta(hours=8)))
        last_refresh = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        d = {
            "type": "codex",
            "email": self.email,
            "expired": expired_str,
            "id_token": self.id_token,
            "account_id": account_id,
            "access_token": self.access_token,
            "last_refresh": last_refresh,
            "refresh_token": self.refresh_token,
            # OpenAi-AGBC 额外字段
            "session_token": self.session_token,
            "device_id": self.device_id,
            "csrf_token": self.csrf_token,
        }
        if self.password:
            d["password"] = self.password
        return d


class AuthFlow:
    """注册/登录协议流"""

    def __init__(self, config: Config):
        self.config = config
        self._impersonate_candidates = ["chrome136", "chrome124", "chrome120"]
        self._impersonate_idx = 0
        self.session = create_http_session(
            proxy=config.proxy,
            impersonate=self._impersonate_candidates[self._impersonate_idx],
        )
        self.result = AuthResult()

    @staticmethod
    def _is_tls_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        markers = ["curl: (35)", "tls connect error", "openssl_internal", "sslerror"]
        return any(m in msg for m in markers)

    def _rotate_impersonate_session(self) -> bool:
        """仅在 curl_cffi 指纹模式内切换 UA 指纹版本重试。"""
        if self._impersonate_idx >= len(self._impersonate_candidates) - 1:
            return False
        self._impersonate_idx += 1
        imp = self._impersonate_candidates[self._impersonate_idx]
        logger.warning(f"TLS 异常，切换指纹重试: impersonate={imp}")
        self.session = create_http_session(proxy=self.config.proxy, impersonate=imp)
        return True

    def _common_headers(self, referer: str = "https://chatgpt.com/") -> dict:
        return {
            "Accept": "application/json",
            "Referer": referer,
            "Origin": "https://chatgpt.com",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
            ),
        }

    # ── Step 1: 检查代理连通性 ──
    def check_proxy(self) -> bool:
        logger.info("检查网络连通性...")
        try:
            resp = self.session.get("https://cloudflare.com/cdn-cgi/trace", timeout=15)
            if resp.status_code == 200:
                loc = re.search(r"loc=(\w+)", resp.text)
                ip = re.search(r"ip=([^\n]+)", resp.text)
                logger.info(f"网络正常 - IP: {ip.group(1) if ip else 'N/A'}, "
                            f"地区: {loc.group(1) if loc else 'N/A'}")
            else:
                logger.warning(f"网络探测异常: cloudflare trace {resp.status_code}")

            # 关键链路探测: chatgpt csrf
            csrf_headers = self._common_headers("https://chatgpt.com/auth/login")
            csrf_resp = self.session.get(
                "https://chatgpt.com/api/auth/csrf",
                headers=csrf_headers,
                timeout=20,
            )
            if csrf_resp.status_code == 200:
                logger.info("chatgpt csrf 连通正常")
                return True

            logger.warning(f"chatgpt csrf 连通异常: {csrf_resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"网络检查失败: {e}")
        return False

    # ── Step 2: 获取 CSRF Token ──
    def get_csrf_token(self) -> str:
        logger.info("[1/10] 获取 CSRF Token...")
        headers = self._common_headers("https://chatgpt.com/auth/login")

        # Cloudflare 可能在短时间内多次请求后返回 403，重试 3 次
        for attempt in range(3):
            try:
                resp = self.session.get(
                    "https://chatgpt.com/api/auth/csrf",
                    headers=headers,
                    timeout=30,
                )
            except Exception as e:
                if self._is_tls_error(e) and self._rotate_impersonate_session():
                    continue
                if self._is_tls_error(e):
                    raise RuntimeError(
                        "chatgpt.com TLS 握手失败，当前网络无法建立到 /api/auth/csrf 的 HTTPS 连接。"
                        "请切换可直连 chatgpt.com 的网络或在界面中配置可用代理后重试。"
                    ) from e
                raise
            if resp.status_code == 403 and attempt < 2:
                wait = (attempt + 1) * 5
                logger.warning(f"Cloudflare 403, {wait}s 后重试 ({attempt + 1}/3)...")
                import time
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break

        csrf = resp.json().get("csrfToken", "")
        if not csrf:
            raise RuntimeError("CSRF Token 获取失败")
        self.result.csrf_token = csrf
        logger.info(f"CSRF Token: {csrf[:20]}...")
        return csrf

    # ── Step 3: 获取 auth URL ──
    def get_auth_url(self, csrf_token: str) -> str:
        logger.info("[2/10] 获取 OpenAI 授权地址...")
        headers = self._common_headers("https://chatgpt.com/auth/login")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self.session.post(
            "https://chatgpt.com/api/auth/signin/openai",
            headers=headers,
            data={
                "csrfToken": csrf_token,
                "callbackUrl": "https://chatgpt.com/",
                "json": "true",
            },
            timeout=30,
        )
        resp.raise_for_status()
        auth_url = resp.json().get("url", "")
        if not auth_url:
            raise RuntimeError("Auth URL 获取失败")
        logger.info(f"Auth URL: {auth_url[:80]}...")
        return auth_url

    # ── Step 4: OAuth 初始化 & 获取 device_id ──
    def auth_oauth_init(self, auth_url: str) -> str:
        logger.info("[3/10] OAuth 初始化...")
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://chatgpt.com/auth/login",
            "User-Agent": self._common_headers()["User-Agent"],
        }
        resp = self.session.get(auth_url, headers=headers, timeout=30, allow_redirects=True)

        # 从 cookie 获取 oai-did
        device_id = ""
        for cookie in self.session.cookies:
            if hasattr(cookie, "name"):
                if cookie.name == "oai-did":
                    device_id = cookie.value
                    break
            elif isinstance(cookie, str) and cookie == "oai-did":
                device_id = self.session.cookies.get("oai-did", "")
                break

        # curl_cffi cookies 访问方式
        if not device_id:
            try:
                device_id = self.session.cookies.get("oai-did", "")
            except Exception:
                pass

        # fallback: 从 HTML 提取
        if not device_id:
            m = re.search(r'oai-did["\s:=]+([a-f0-9-]{36})', resp.text)
            if m:
                device_id = m.group(1)

        if not device_id:
            device_id = str(uuid.uuid4())
            logger.warning(f"未从响应中获取 device_id，使用生成值: {device_id}")

        self.result.device_id = device_id
        logger.info(f"Device ID: {device_id}")
        return device_id

    # ── Step 5: 获取 Sentinel Token ──
    def get_sentinel_token(self, device_id: str) -> str:
        logger.info("[4/10] 获取 Sentinel Token...")
        body = json.dumps({"p": "", "id": device_id, "flow": "authorize_continue"})
        headers = {
            "Origin": "https://sentinel.openai.com",
            "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
            "Content-Type": "text/plain;charset=UTF-8",
        }
        resp = self.session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers=headers,
            data=body,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Sentinel 异常，状态码: {resp.status_code}")
        token = resp.json().get("token", "")
        sentinel_header = json.dumps({
            "p": "", "t": "", "c": token, "id": device_id, "flow": "authorize_continue"
        })
        logger.info("Sentinel Token 获取成功")
        return sentinel_header

    def _get_sentinel_token_with_flow(self, device_id: str, flow: str = "authorize_continue") -> str:
        """获取指定 flow 的 Sentinel Token"""
        logger.info(f"获取 Sentinel Token (flow={flow})...")
        body = json.dumps({"p": "", "id": device_id, "flow": flow})
        headers = {
            "Origin": "https://sentinel.openai.com",
            "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
            "Content-Type": "text/plain;charset=UTF-8",
        }
        resp = self.session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers=headers,
            data=body,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Sentinel 异常，状态码: {resp.status_code}")
        token = resp.json().get("token", "")
        sentinel_header = json.dumps({
            "p": "", "t": "", "c": token, "id": device_id, "flow": flow
        })
        return sentinel_header

    # ── Step 6: 提交注册邮箱 ──
    def signup(self, email: str, sentinel_token: str):
        logger.info("[5/10] 提交注册邮箱...")
        headers = self._common_headers("https://auth.openai.com/create-account")
        headers["Content-Type"] = "application/json"
        headers["openai-sentinel-token"] = sentinel_token
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers=headers,
            json={
                "username": {"value": email, "kind": "email"},
                "screen_hint": "signup",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"注册失败: {resp.status_code} - {resp.text[:500]}")
            raise RuntimeError(f"注册失败: HTTP {resp.status_code} - {resp.text[:300]}")
        logger.info("注册邮箱已提交")

    # ── Step 7: 发送 OTP ──
    def send_otp(self):
        logger.info("[6/10] 发送 OTP...")
        headers = self._common_headers("https://auth.openai.com/create-account/password")
        headers["Content-Type"] = "application/json"
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/passwordless/send-otp",
            headers=headers,
            json={},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"发送 OTP 失败: {resp.status_code} - {resp.text[:200]}")
        logger.info("OTP 已发送到邮箱")

    # ── Step 8: 验证 OTP ──
    def verify_otp(self, otp_code: str):
        logger.info("[7/10] 验证 OTP...")
        headers = self._common_headers("https://auth.openai.com/email-verification")
        headers["Content-Type"] = "application/json"
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers=headers,
            json={"code": otp_code},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OTP 验证失败: {resp.status_code}")
        logger.info("OTP 验证成功")

    # ── Step 9: 创建账户 ──
    def create_account(self) -> str:
        logger.info("[8/10] 创建账户...")
        headers = self._common_headers("https://auth.openai.com/about-you")
        headers["Content-Type"] = "application/json"
        name = "Neo"
        birthdate = f"{random.randint(1985, 2000)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/create_account",
            headers=headers,
            json={"name": name, "birthdate": birthdate},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"创建账户失败: {resp.status_code}")
        data = resp.json()
        continue_url = data.get("continue_url", "")

        # 尝试 workspace select
        if not continue_url:
            workspace_id = self._extract_workspace_id()
            if workspace_id:
                continue_url = self._workspace_select(workspace_id)

        if not continue_url:
            raise RuntimeError("创建账户后未获取到 continue_url")

        logger.info("账户创建成功")
        return continue_url

    def _extract_workspace_id(self) -> str:
        """从 cookie 中提取 workspace_id"""
        try:
            auth_session = self.session.cookies.get("oai-client-auth-session", "")
            if auth_session:
                # base64 解码 JWT payload
                import base64
                parts = auth_session.split(".")
                if len(parts) >= 2:
                    payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                    decoded = json.loads(base64.b64decode(payload))
                    return decoded.get("workspace_id", "")
        except Exception:
            pass
        return ""

    def _workspace_select(self, workspace_id: str) -> str:
        logger.info("执行 workspace 选择...")
        headers = self._common_headers("https://auth.openai.com/sign-in-with-chatgpt/codex/consent")
        headers["Content-Type"] = "application/json"
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/workspace/select",
            headers=headers,
            json={"workspace_id": workspace_id},
            timeout=30,
        )
        return resp.json().get("continue_url", "") if resp.status_code == 200 else ""

    # ── Step 10: 跟踪重定向链 ──
    def follow_redirect_chain(self, start_url: str) -> tuple[str, str]:
        """手动跟踪重定向，返回 (callback_url, final_url)"""
        logger.info("[9/10] 跟踪重定向链...")
        current_url = start_url
        callback_url = ""
        max_hops = 12

        for i in range(max_hops):
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://chatgpt.com/",
                "User-Agent": self._common_headers()["User-Agent"],
            }
            resp = self.session.get(
                current_url, headers=headers, timeout=30, allow_redirects=False
            )

            if "/api/auth/callback/openai" in current_url:
                callback_url = current_url

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if not location:
                    break
                if location.startswith("/"):
                    parsed = urlparse(current_url)
                    location = f"{parsed.scheme}://{parsed.netloc}{location}"
                current_url = location
                logger.debug(f"  重定向 {i + 1}: {current_url[:80]}...")
            else:
                break

        # 补一跳首页
        if not current_url.rstrip("/").endswith("chatgpt.com"):
            self.session.get(
                "https://chatgpt.com/",
                headers={"Referer": current_url},
                timeout=30,
            )

        logger.info(f"重定向链完成, callback: {'有' if callback_url else '无'}")
        return callback_url, current_url

    # ── Step 11: 获取 session ──
    def get_auth_session(self) -> tuple[str, str]:
        """获取 session_token 和 access_token"""
        logger.info("[10/10] 获取认证 Session...")
        headers = self._common_headers("https://chatgpt.com/")
        resp = self.session.get(
            "https://chatgpt.com/api/auth/session",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()

        session_token = self.session.cookies.get("__Secure-next-auth.session-token", "")
        access_token = resp.json().get("accessToken", "")

        if session_token:
            self.result.session_token = session_token
        if access_token:
            self.result.access_token = access_token

        logger.info(f"session_token: {'有' if session_token else '无'}, "
                     f"access_token: {'有' if access_token else '无'}")
        return session_token, access_token

    # ── 可选: OAuth Token 交换 ──
    def oauth_token_exchange(self, callback_url: str, continue_url: str):
        """用 auth_code + login_verifier 交换完整 token"""
        parsed_cb = parse_qs(urlparse(callback_url).query)
        parsed_cu = parse_qs(urlparse(continue_url).query)
        auth_code = parsed_cb.get("code", [None])[0]
        login_verifier = parsed_cu.get("login_verifier", [None])[0]

        if not (auth_code and login_verifier):
            logger.info("缺少 auth_code 或 login_verifier, 跳过 token 交换")
            return

        logger.info("执行 OAuth Token 交换...")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": callback_url,
        }
        resp = self.session.post(
            "https://auth.openai.com/oauth/token",
            headers=headers,
            data={
                "grant_type": "authorization_code",
                "client_id": "app_X8zY6vW2pQ9tR3dE7nK1jL5gH",
                "code": auth_code,
                "redirect_uri": "https://chatgpt.com/api/auth/callback/openai",
                "code_verifier": login_verifier,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            self.result.id_token = data.get("id_token", "")
            self.result.access_token = data.get("access_token", self.result.access_token)
            self.result.refresh_token = data.get("refresh_token", "")
            logger.info("Token 交换成功")
        else:
            logger.warning(f"Token 交换失败: {resp.status_code}")

    # ── 完整注册流程 ──
    def run_register(self, mail_provider: MailProvider) -> AuthResult:
        """执行完整注册流程"""
        # 检查网络
        if not self.check_proxy():
            logger.warning("网络预检查未通过，继续尝试注册链路以获取精确错误...")

        # 创建邮箱
        email = mail_provider.create_mailbox()
        self.result.email = email

        # 登录/注册链路
        csrf_token = self.get_csrf_token()
        auth_url = self.get_auth_url(csrf_token)
        device_id = self.auth_oauth_init(auth_url)
        sentinel = self.get_sentinel_token(device_id)
        self.signup(email, sentinel)
        self.send_otp()

        # 等待 OTP
        otp_code = mail_provider.wait_for_otp(email)
        self.verify_otp(otp_code)

        # 创建账户 & 重定向
        continue_url = self.create_account()
        callback_url, final_url = self.follow_redirect_chain(continue_url)

        # 获取 session
        self.get_auth_session()

        # 可选 token 交换
        if callback_url and continue_url:
            self.oauth_token_exchange(callback_url, continue_url)

        if not self.result.is_valid():
            raise RuntimeError("注册完成但未获取有效凭证")

        logger.info("注册流程完成!")
        return self.result

    # ══════════════════════════════════════
    # 密码注册模式 (按 chatgpt_register 实现)
    # ══════════════════════════════════════

    def register_with_password(self, email: str, password: str):
        """使用密码注册: POST /api/accounts/user/register"""
        logger.info("[5/10] 提交密码注册...")
        headers = self._common_headers("https://auth.openai.com/create-account/password")
        headers["Content-Type"] = "application/json"
        resp = self.session.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers=headers,
            json={"username": email, "password": password},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"密码注册失败: {resp.status_code} - {resp.text[:500]}")
            raise RuntimeError(f"密码注册失败: HTTP {resp.status_code} - {resp.text[:300]}")
        logger.info("密码注册提交成功")

    def _codex_oauth_follow_for_code(self, start_url: str, referer: str = None, max_hops: int = 16) -> tuple:
        """手动跟踪重定向提取 authorization code"""
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self._common_headers()["User-Agent"],
        }
        if referer:
            headers["Referer"] = referer

        current_url = start_url
        last_url = start_url

        for hop in range(max_hops):
            try:
                resp = self.session.get(
                    current_url, headers=headers,
                    allow_redirects=False, timeout=30,
                )
            except Exception as e:
                # localhost 回调会导致连接异常，从异常信息提取 code
                maybe_localhost = re.search(r'(https?://localhost[^\s\'\"]+)', str(e))
                if maybe_localhost:
                    code = _extract_code_from_url(maybe_localhost.group(1))
                    if code:
                        logger.info(f"[OAuth] follow[{hop+1}] 命中 localhost 回调")
                        return code, maybe_localhost.group(1)
                logger.warning(f"[OAuth] follow[{hop+1}] 请求异常: {e}")
                return None, last_url

            last_url = str(resp.url)
            code = _extract_code_from_url(last_url)
            if code:
                return code, last_url

            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location", "")
                if not loc:
                    return None, last_url
                if loc.startswith("/"):
                    loc = f"{OAUTH_ISSUER}{loc}"
                code = _extract_code_from_url(loc)
                if code:
                    return code, loc
                current_url = loc
                headers["Referer"] = last_url
                continue

            return None, last_url

        return None, last_url

    def _codex_oauth_allow_redirect(self, url: str, referer: str = None) -> Optional[str]:
        """允许自动重定向并提取 code"""
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self._common_headers()["User-Agent"],
        }
        if referer:
            headers["Referer"] = referer
        try:
            resp = self.session.get(url, headers=headers, allow_redirects=True, timeout=30)
            final_url = str(resp.url)
            code = _extract_code_from_url(final_url)
            if code:
                return code
            for r in getattr(resp, "history", []) or []:
                loc = r.headers.get("Location", "")
                code = _extract_code_from_url(loc)
                if code:
                    return code
                code = _extract_code_from_url(str(r.url))
                if code:
                    return code
        except Exception as e:
            maybe_localhost = re.search(r'(https?://localhost[^\s\'\"]+)', str(e))
            if maybe_localhost:
                code = _extract_code_from_url(maybe_localhost.group(1))
                if code:
                    return code
        return None

    def _decode_oauth_session_cookie(self) -> Optional[dict]:
        """
        解码 oai-client-auth-session cookie。
        格式为 Flask/itsdangerous: base64(json).timestamp.signature
        第一段 base64 解码后就是 JSON（包含 workspaces/orgs 等）。
        完全按照 chatgpt_register._decode_oauth_session_cookie 实现。
        """
        jar = getattr(self.session.cookies, "jar", None)
        if jar is not None:
            cookie_items = list(jar)
        else:
            cookie_items = []

        for c in cookie_items:
            name = getattr(c, "name", "") or ""
            if "oai-client-auth-session" not in name:
                continue

            raw_val = (getattr(c, "value", "") or "").strip()
            if not raw_val:
                continue

            # 尝试原始值和 URL 解码值
            from urllib.parse import unquote
            candidates = [raw_val]
            decoded = unquote(raw_val)
            if decoded != raw_val:
                candidates.append(decoded)

            for val in candidates:
                try:
                    # 去除可能的引号包裹
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]

                    # Flask/itsdangerous 格式: 第一段是 base64 JSON
                    part = val.split(".")[0] if "." in val else val
                    pad = 4 - len(part) % 4
                    if pad != 4:
                        part += "=" * pad
                    raw = base64.urlsafe_b64decode(part)
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        logger.info(f"[OAuth] oai-client-auth-session 解码成功, keys={list(data.keys())}")
                        return data
                except Exception:
                    continue
        return None

    def _do_workspace_and_org_select(self, workspace_id: str, session_data: dict, consent_url: str) -> Optional[str]:
        """执行 workspace/select + organization/select 获取 authorization code"""
        headers = self._common_headers(consent_url)
        headers["Content-Type"] = "application/json"

        logger.info(f"[OAuth] workspace/select workspace_id={workspace_id}")
        resp = self.session.post(
            f"{OAUTH_ISSUER}/api/accounts/workspace/select",
            headers=headers,
            json={"workspace_id": workspace_id},
            allow_redirects=False,
            timeout=30,
        )
        logger.info(f"[OAuth] workspace/select -> {resp.status_code}")

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if loc.startswith("/"):
                loc = f"{OAUTH_ISSUER}{loc}"
            code = _extract_code_from_url(loc)
            if code:
                return code
            code, _ = self._codex_oauth_follow_for_code(loc, referer=consent_url)
            if not code:
                code = self._codex_oauth_allow_redirect(loc, referer=consent_url)
            return code

        if resp.status_code != 200:
            logger.warning(f"[OAuth] workspace/select 失败: {resp.status_code}")
            return None

        try:
            ws_data = resp.json()
        except Exception:
            logger.warning("[OAuth] workspace/select 响应不是 JSON")
            return None

        ws_next = ws_data.get("continue_url", "")
        orgs = ws_data.get("data", {}).get("orgs", [])
        ws_page = (ws_data.get("page") or {}).get("type", "")
        logger.info(f"[OAuth] workspace/select page={ws_page or '-'} next={(ws_next or '-')[:140]}")

        # organization/select
        org_id = None
        project_id = None
        if orgs:
            org_id = (orgs[0] or {}).get("id")
            projects = (orgs[0] or {}).get("projects", [])
            if projects:
                project_id = (projects[0] or {}).get("id")

        if org_id:
            org_body = {"org_id": org_id}
            if project_id:
                org_body["project_id"] = project_id

            h_org = self._common_headers(ws_next or consent_url)
            h_org["Content-Type"] = "application/json"

            resp_org = self.session.post(
                f"{OAUTH_ISSUER}/api/accounts/organization/select",
                json=org_body, headers=h_org,
                allow_redirects=False, timeout=30,
            )
            logger.info(f"[OAuth] organization/select -> {resp_org.status_code}")
            if resp_org.status_code in (301, 302, 303, 307, 308):
                loc = resp_org.headers.get("Location", "")
                if loc.startswith("/"):
                    loc = f"{OAUTH_ISSUER}{loc}"
                code = _extract_code_from_url(loc)
                if code:
                    return code
                code, _ = self._codex_oauth_follow_for_code(loc, referer=h_org.get("Referer"))
                if not code:
                    code = self._codex_oauth_allow_redirect(loc, referer=h_org.get("Referer"))
                return code
            if resp_org.status_code == 200:
                try:
                    org_data = resp_org.json()
                    org_next = org_data.get("continue_url", "")
                    org_page = (org_data.get("page") or {}).get("type", "")
                    logger.info(f"[OAuth] organization/select page={org_page or '-'} next={(org_next or '-')[:140]}")
                    if org_next:
                        if org_next.startswith("/"):
                            org_next = f"{OAUTH_ISSUER}{org_next}"
                        code, _ = self._codex_oauth_follow_for_code(org_next, referer=h_org.get("Referer"))
                        if not code:
                            code = self._codex_oauth_allow_redirect(org_next, referer=h_org.get("Referer"))
                        return code
                except Exception:
                    pass

        if ws_next:
            if ws_next.startswith("/"):
                ws_next = f"{OAUTH_ISSUER}{ws_next}"
            code, _ = self._codex_oauth_follow_for_code(ws_next, referer=consent_url)
            if not code:
                code = self._codex_oauth_allow_redirect(ws_next, referer=consent_url)
            return code

        return None


    def perform_codex_oauth(self, email: str, password: str, mail_provider: MailProvider = None) -> dict:
        """
        独立 Codex OAuth PKCE 流程: 用密码登录获取 refresh_token。
        参照 chatgpt_register.perform_codex_oauth_login_http。
        """
        logger.info("[OAuth] 开始 Codex OAuth 流程...")

        # 确保 auth 域也有 oai-did
        device_id = self.result.device_id or str(uuid.uuid4())
        self.session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", device_id, domain="auth.openai.com")

        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(24)

        authorize_params = {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": CODEX_REDIRECT_URI,
            "scope": "openid profile email offline_access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        authorize_url = f"{OAUTH_ISSUER}/oauth/authorize?{urlencode(authorize_params)}"

        # Step 1: GET /oauth/authorize - 建立 login_session
        logger.info("[OAuth] 1/7 GET /oauth/authorize")
        try:
            resp = self.session.get(
                authorize_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://chatgpt.com/",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": self._common_headers()["User-Agent"],
                },
                allow_redirects=True,
                timeout=30,
            )
            authorize_final_url = str(resp.url)
        except Exception as e:
            logger.error(f"[OAuth] /oauth/authorize 异常: {e}")
            return {}

        continue_referer = authorize_final_url if authorize_final_url.startswith(OAUTH_ISSUER) else f"{OAUTH_ISSUER}/log-in"

        # Step 2: POST /api/accounts/authorize/continue - 提交邮箱
        logger.info("[OAuth] 2/7 POST /api/accounts/authorize/continue")
        try:
            sentinel = self.get_sentinel_token(device_id)
        except Exception as e:
            logger.warning(f"[OAuth] sentinel (authorize_continue) 获取失败, 使用空 token: {e}")
            sentinel = json.dumps({"p": "", "t": "", "c": "", "id": device_id, "flow": "authorize_continue"})

        headers_continue = self._common_headers(continue_referer)
        headers_continue["Content-Type"] = "application/json"
        headers_continue["openai-sentinel-token"] = sentinel

        try:
            resp_continue = self.session.post(
                f"{OAUTH_ISSUER}/api/accounts/authorize/continue",
                json={"username": {"kind": "email", "value": email}},
                headers=headers_continue,
                timeout=30,
                allow_redirects=False,
            )
        except Exception as e:
            logger.error(f"[OAuth] authorize/continue 异常: {e}")
            return {}

        if resp_continue.status_code != 200:
            logger.warning(f"[OAuth] 邮箱提交失败: {resp_continue.status_code} {resp_continue.text[:200]}")
            return {}

        try:
            continue_data = resp_continue.json()
        except Exception:
            logger.warning("[OAuth] authorize/continue 响应解析失败")
            return {}

        continue_url = continue_data.get("continue_url", "")
        page_type = (continue_data.get("page") or {}).get("type", "")
        logger.info(f"[OAuth] continue page={page_type} next={continue_url[:140] if continue_url else '-'}")

        # Step 3: POST /api/accounts/password/verify - 密码验证
        logger.info("[OAuth] 3/7 POST /api/accounts/password/verify")
        # 获取 password_verify 的 sentinel (使用正确的 flow)
        try:
            sentinel_pwd = self._get_sentinel_token_with_flow(device_id, "password_verify")
        except Exception as e:
            logger.warning(f"[OAuth] sentinel (password_verify) 获取失败, 使用空 token: {e}")
            sentinel_pwd = json.dumps({"p": "", "t": "", "c": "", "id": device_id, "flow": "password_verify"})

        headers_verify = self._common_headers(f"{OAUTH_ISSUER}/log-in/password")
        headers_verify["Content-Type"] = "application/json"
        headers_verify["openai-sentinel-token"] = sentinel_pwd

        try:
            resp_verify = self.session.post(
                f"{OAUTH_ISSUER}/api/accounts/password/verify",
                json={"password": password},
                headers=headers_verify,
                timeout=30,
                allow_redirects=False,
            )
        except Exception as e:
            logger.error(f"[OAuth] password/verify 异常: {e}")
            return {}

        logger.info(f"[OAuth] /password/verify -> {resp_verify.status_code}")
        if resp_verify.status_code != 200:
            logger.warning(f"[OAuth] 密码校验失败: {resp_verify.text[:200]}")
            return {}

        try:
            verify_data = resp_verify.json()
        except Exception:
            logger.warning("[OAuth] password/verify 响应解析失败")
            return {}

        continue_url = verify_data.get("continue_url", "") or continue_url
        page_type = (verify_data.get("page") or {}).get("type", "") or page_type

        # Step 4: 处理可能的 OTP 验证
        need_otp = (
            page_type == "email_otp_verification"
            or "email-verification" in (continue_url or "")
            or "email-otp" in (continue_url or "")
        )

        if need_otp:
            logger.info("[OAuth] 4/7 需要邮箱 OTP 验证")
            if not mail_provider:
                logger.warning("[OAuth] 需要 OTP 但未提供 mail_provider")
                return {}

            # 按 chatgpt_register 逻辑: 轮询邮件, 提取多个候选 OTP, 逐个尝试
            headers_otp = self._common_headers(f"{OAUTH_ISSUER}/email-verification")
            headers_otp["Content-Type"] = "application/json"
            tried_codes = set()
            otp_success = False
            otp_deadline = time.time() + 120

            while time.time() < otp_deadline and not otp_success:
                # 获取所有候选 OTP (从邮件中提取)
                candidate_codes = mail_provider.fetch_all_otp_codes(email)

                new_codes = [c for c in candidate_codes if c not in tried_codes]
                if not new_codes:
                    elapsed = int(120 - max(0, otp_deadline - time.time()))
                    logger.info(f"[OAuth] OTP 等待中... ({elapsed}s/120s)")
                    time.sleep(3)
                    continue

                for otp_code in new_codes:
                    tried_codes.add(otp_code)
                    logger.info(f"[OAuth] 尝试 OTP: {otp_code}")
                    try:
                        resp_otp = self.session.post(
                            f"{OAUTH_ISSUER}/api/accounts/email-otp/validate",
                            json={"code": otp_code},
                            headers=headers_otp,
                            timeout=30,
                            allow_redirects=False,
                        )
                    except Exception as e:
                        logger.error(f"[OAuth] email-otp/validate 异常: {e}")
                        continue

                    logger.info(f"[OAuth] /email-otp/validate -> {resp_otp.status_code}")
                    if resp_otp.status_code != 200:
                        logger.info(f"[OAuth] OTP 无效，继续尝试下一条")
                        continue

                    try:
                        otp_data = resp_otp.json()
                        continue_url = otp_data.get("continue_url", "") or continue_url
                        page_type = (otp_data.get("page") or {}).get("type", "") or page_type
                    except Exception:
                        pass
                    logger.info(f"[OAuth] OTP 验证通过")
                    otp_success = True
                    break

                if not otp_success:
                    time.sleep(3)

            if not otp_success:
                logger.warning(f"[OAuth] OAuth 阶段 OTP 验证失败，已尝试 {len(tried_codes)} 个验证码")
                return {}

        # Step 5: consent 多步流程 → 提取 authorization code
        # 按 chatgpt_register 步骤4:
        #   4a: GET consent 页面（设置 cookie）
        #   4b: 解码 oai-client-auth-session → workspace/select
        #   4c: organization/select → 提取 code
        #   4d: 备用策略（allow_redirects=True）
        code = None
        consent_url = continue_url
        if consent_url and consent_url.startswith("/"):
            consent_url = f"{OAUTH_ISSUER}{consent_url}"

        if not consent_url and "consent" in page_type:
            consent_url = f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"

        if consent_url:
            code = _extract_code_from_url(consent_url)

        # Step 5a: GET consent 页面（关键！触发服务端设置 oai-client-auth-session cookie）
        if not code and consent_url:
            logger.info("[OAuth] 5/7 GET consent 页面")
            try:
                resp_consent = self.session.get(
                    consent_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Upgrade-Insecure-Requests": "1",
                        "User-Agent": self._common_headers()["User-Agent"],
                        "Referer": f"{OAUTH_ISSUER}/log-in/password",
                    },
                    allow_redirects=False,
                    timeout=30,
                )
                if resp_consent.status_code in (301, 302, 303, 307, 308):
                    loc = resp_consent.headers.get("Location", "")
                    code = _extract_code_from_url(loc)
                    if code:
                        logger.info("[OAuth] consent 直接 302 获取到 code")
                    else:
                        # 继续跟随重定向
                        if loc.startswith("/"):
                            loc = f"{OAUTH_ISSUER}{loc}"
                        code, _ = self._codex_oauth_follow_for_code(loc, referer=consent_url)
                        if code:
                            logger.info("[OAuth] consent 302 跟踪获取到 code")
                elif resp_consent.status_code == 200:
                    logger.info(f"[OAuth] consent 页面已加载 ({len(resp_consent.text)} 字节)")
            except Exception as e:
                # localhost 连接失败，从异常信息提取 code
                maybe_localhost = re.search(r'(https?://localhost[^\s\'\"]+)', str(e))
                if maybe_localhost:
                    code = _extract_code_from_url(maybe_localhost.group(1))
                    if code:
                        logger.info("[OAuth] consent 异常中获取到 code")
                if not code:
                    logger.warning(f"[OAuth] consent 请求异常: {e}")

        # Step 5b: 解码 oai-client-auth-session cookie → workspace/select
        if not code:
            logger.info("[OAuth] 6/7 解码 session → workspace/select")
            session_data = self._decode_oauth_session_cookie()
            if session_data:
                workspaces = session_data.get("workspaces", [])
                if workspaces:
                    workspace_id = (workspaces[0] or {}).get("id")
                    if workspace_id:
                        code = self._do_workspace_and_org_select(
                            workspace_id, session_data,
                            consent_url or f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
                        )
                else:
                    logger.warning("[OAuth] session 中无 workspace 信息")
            else:
                logger.warning("[OAuth] 无法解码 oai-client-auth-session cookie")

        # Step 5c: 备用策略 — follow 或 allow_redirects=True
        if not code:
            fallback_consent = consent_url or f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
            logger.info("[OAuth] 6/7 备用策略: follow consent URL")
            code, _ = self._codex_oauth_follow_for_code(fallback_consent, referer=f"{OAUTH_ISSUER}/log-in/password")

        if not code:
            fallback_consent = consent_url or f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
            logger.info("[OAuth] 6/7 备用策略: allow_redirects=True")
            code = self._codex_oauth_allow_redirect(fallback_consent, referer=f"{OAUTH_ISSUER}/log-in/password")

        if not code:
            logger.warning("[OAuth] 未获取到 authorization code")
            return {}

        # Step 7: POST /oauth/token - 用 code + code_verifier 换 token
        logger.info("[OAuth] 7/7 POST /oauth/token")
        token_resp = self.session.post(
            f"{OAUTH_ISSUER}/oauth/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self._common_headers()["User-Agent"],
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CODEX_REDIRECT_URI,
                "client_id": CODEX_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            timeout=60,
        )
        logger.info(f"[OAuth] /oauth/token -> {token_resp.status_code}")

        if token_resp.status_code != 200:
            logger.warning(f"[OAuth] token 交换失败: {token_resp.status_code} {token_resp.text[:200]}")
            return {}

        try:
            data = token_resp.json()
        except Exception:
            logger.warning("[OAuth] token 响应解析失败")
            return {}

        if not data.get("access_token"):
            logger.warning("[OAuth] 响应中缺少 access_token")
            return {}

        logger.info("[OAuth] Codex Token 获取成功!")
        return data

    def run_register_with_password(self, mail_provider: MailProvider, password: str = "") -> AuthResult:
        """
        密码注册模式 (按 chatgpt_register 实现):
        signin 传 login_hint → authorize 重定向到 create-account/password → register → send_otp → verify_otp → create_account → callback → session → codex_oauth
        """
        import time as _time

        # 生成或使用提供的密码
        if not password:
            password = generate_password()
            logger.info(f"自动生成密码: {password}")

        # 检查网络
        if not self.check_proxy():
            logger.warning("网络预检查未通过，继续尝试...")

        # 创建邮箱
        email = mail_provider.create_mailbox()
        self.result.email = email
        self.result.password = password

        # Step 1: CSRF
        logger.info("[1/10] 获取 CSRF Token...")
        csrf_token = self.get_csrf_token()
        _time.sleep(random.uniform(0.2, 0.5))

        # Step 2: signin (带 login_hint=email, 关键差异!)
        logger.info("[2/10] 获取 OpenAI 授权地址 (带邮箱提示)...")
        headers = self._common_headers("https://chatgpt.com/auth/login")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        auth_session_logging_id = str(uuid.uuid4())
        resp = self.session.post(
            "https://chatgpt.com/api/auth/signin/openai",
            headers=headers,
            params={
                "prompt": "login",
                "ext-oai-did": self.result.device_id or str(uuid.uuid4()),
                "auth_session_logging_id": auth_session_logging_id,
                "screen_hint": "login_or_signup",
                "login_hint": email,
            },
            data={
                "csrfToken": csrf_token,
                "callbackUrl": "https://chatgpt.com/",
                "json": "true",
            },
            timeout=30,
        )
        resp.raise_for_status()
        auth_url = resp.json().get("url", "")
        if not auth_url:
            raise RuntimeError("Auth URL 获取失败")
        logger.info(f"Auth URL: {auth_url[:80]}...")
        _time.sleep(random.uniform(0.3, 0.8))

        # Step 3: authorize (follow redirects → 应落在 create-account/password)
        logger.info("[3/10] OAuth 授权初始化...")
        resp_auth = self.session.get(
            auth_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://chatgpt.com/",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": self._common_headers()["User-Agent"],
            },
            allow_redirects=True,
            timeout=30,
        )
        final_url = str(resp_auth.url)
        final_path = urlparse(final_url).path
        logger.info(f"Authorize → {final_path}")

        # 获取 device_id
        device_id = ""
        for cookie in self.session.cookies:
            if hasattr(cookie, "name") and cookie.name == "oai-did":
                device_id = cookie.value
                break
        if not device_id:
            try:
                device_id = self.session.cookies.get("oai-did", "")
            except Exception:
                pass
        if not device_id:
            device_id = str(uuid.uuid4())
        self.result.device_id = device_id
        _time.sleep(random.uniform(0.3, 0.8))

        # Step 4: 根据 authorize 返回的 URL 决策 (与 chatgpt_register 一致)
        need_otp = False

        if "create-account/password" in final_path:
            # 全新注册流程: 直接 register (不需要 sentinel 和 signup)
            logger.info("[4/10] 全新注册流程 → 提交密码注册...")
            self.register_with_password(email, password)
            _time.sleep(random.uniform(0.3, 0.8))
            # send_otp (chatgpt_register 用 GET /email-otp/send)
            logger.info("[5/10] 发送 OTP (GET)...")
            otp_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://auth.openai.com/create-account/password",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": self._common_headers()["User-Agent"],
            }
            resp_otp = self.session.get(
                "https://auth.openai.com/api/accounts/email-otp/send",
                headers=otp_headers,
                allow_redirects=True,
                timeout=30,
            )
            logger.info(f"OTP 发送: {resp_otp.status_code}")
            need_otp = True
        elif "email-verification" in final_path or "email-otp" in final_path:
            logger.info("[4/10] 跳到 OTP 验证阶段 (authorize 已触发 OTP)")
            need_otp = True
        elif "about-you" in final_path:
            logger.info("[4/10] 跳到填写信息阶段")
        elif "callback" in final_path or "chatgpt.com" in final_url:
            logger.info("[4/10] 账号已完成注册")
        else:
            # 未知跳转: 尝试 register 后 send_otp
            logger.warning(f"[4/10] 未知跳转: {final_url}, 尝试 register...")
            self.register_with_password(email, password)
            _time.sleep(random.uniform(0.3, 0.8))
            otp_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://auth.openai.com/create-account/password",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": self._common_headers()["User-Agent"],
            }
            self.session.get(
                "https://auth.openai.com/api/accounts/email-otp/send",
                headers=otp_headers, allow_redirects=True, timeout=30,
            )
            need_otp = True

        # Step 6: OTP 验证
        if need_otp:
            logger.info("[6/10] 等待 OTP...")
            otp_code = mail_provider.wait_for_otp(email)
            if not otp_code:
                raise RuntimeError("OTP 获取失败")
            _time.sleep(random.uniform(0.3, 0.8))
            logger.info("[7/10] 验证 OTP...")
            self.verify_otp(otp_code)

        # Step 8: 创建账户
        _time.sleep(random.uniform(0.5, 1.5))
        continue_url = self.create_account()

        # Step 9: 跟踪重定向
        _time.sleep(random.uniform(0.2, 0.5))
        callback_url, final_url = self.follow_redirect_chain(continue_url)

        # Step 10: 获取 session
        self.get_auth_session()

        # 可选 token 交换 (原有逻辑)
        if callback_url and continue_url:
            self.oauth_token_exchange(callback_url, continue_url)

        if not self.result.is_valid():
            raise RuntimeError("注册完成但未获取有效凭证")

        # 密码注册独有: Codex OAuth 获取 refresh_token (带重试)
        if not self.result.refresh_token:
            _max_oauth_retries = 3
            for _oauth_attempt in range(1, _max_oauth_retries + 1):
                logger.info(f"尝试通过 Codex OAuth 获取 refresh_token... (第 {_oauth_attempt}/{_max_oauth_retries} 次)")
                try:
                    tokens = self.perform_codex_oauth(email, password, mail_provider)
                    if tokens and tokens.get("refresh_token"):
                        self.result.refresh_token = tokens["refresh_token"]
                        if tokens.get("access_token"):
                            self.result.access_token = tokens["access_token"]
                        if tokens.get("id_token"):
                            self.result.id_token = tokens["id_token"]
                        logger.info(f"refresh_token 获取成功! (第 {_oauth_attempt} 次尝试)")
                        break
                    else:
                        logger.warning(f"Codex OAuth 第 {_oauth_attempt} 次未返回 refresh_token")
                except Exception as e:
                    logger.warning(f"Codex OAuth 第 {_oauth_attempt} 次失败: {e}")

                if _oauth_attempt < _max_oauth_retries:
                    _retry_delay = _oauth_attempt * 2  # 2s, 4s
                    logger.info(f"等待 {_retry_delay}s 后重试...")
                    _time.sleep(_retry_delay)

            if not self.result.refresh_token:
                logger.warning(f"Codex OAuth 重试 {_max_oauth_retries} 次后仍未获取到 refresh_token")

        logger.info("密码注册流程完成!")
        return self.result

    # ── 从已有凭证初始化 ──
    def from_existing_credentials(
        self, session_token: str, access_token: str, device_id: str
    ) -> AuthResult:
        """使用已有凭证（跳过注册）"""
        self.result.device_id = device_id or str(uuid.uuid4())
        self.session.cookies.set("oai-did", self.result.device_id, domain=".chatgpt.com")

        # 如果有 session_token, 用它刷新 access_token (旧 access_token 可能已过期)
        if session_token:
            self.session.cookies.set(
                "__Secure-next-auth.session-token",
                session_token,
                domain=".chatgpt.com",
            )
            logger.info("使用 session_token 刷新 access_token...")
            try:
                headers = self._common_headers("https://chatgpt.com/")
                resp = self.session.get(
                    "https://chatgpt.com/api/auth/session",
                    headers=headers,
                    timeout=30,
                )
                new_access_token = resp.json().get("accessToken", "")
                new_session_token = self.session.cookies.get("__Secure-next-auth.session-token", "")
                if new_access_token:
                    access_token = new_access_token
                    logger.info("access_token 刷新成功")
                else:
                    logger.warning(f"access_token 刷新失败 (status={resp.status_code}), 使用原 token")
                if new_session_token:
                    session_token = new_session_token
            except Exception as e:
                logger.warning(f"刷新 access_token 失败: {e}, 使用原 token")
        elif access_token:
            # 没有 session_token, 尝试通过 access_token 获取
            logger.info("未提供 session_token, 尝试通过 access_token 获取...")
            try:
                headers = self._common_headers("https://chatgpt.com/")
                headers["Authorization"] = f"Bearer {access_token}"
                resp = self.session.get(
                    "https://chatgpt.com/api/auth/session",
                    headers=headers,
                    timeout=30,
                )
                session_token = self.session.cookies.get("__Secure-next-auth.session-token", "")
                if session_token:
                    logger.info("通过 access_token 获取 session_token 成功")
                else:
                    logger.warning("未能获取 session_token, 可能需要手动提供")
            except Exception as e:
                logger.warning(f"获取 session_token 失败: {e}")

        self.result.access_token = access_token
        self.result.session_token = session_token
        if session_token:
            self.session.cookies.set(
                "__Secure-next-auth.session-token",
                session_token,
                domain=".chatgpt.com",
            )
        logger.info("使用已有凭证初始化完成")
        return self.result
