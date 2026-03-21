"""
邮箱服务 - 临时邮箱创建 & OTP 获取
对接 moemail API: https://github.com/buxuku/moemail
"""
import re
import time
import random
import string
import logging

from http_client import create_http_session

logger = logging.getLogger(__name__)


class MailProvider:
    """临时邮箱提供者 (moemail API)"""

    def __init__(self, worker_domain: str, admin_token: str, email_domain: str):
        self.worker_domain = worker_domain.rstrip("/")
        self.api_key = admin_token  # moemail 使用 X-API-Key 认证
        self.email_domain = email_domain
        self.session = create_http_session()
        self.email_id: str | None = None  # moemail 返回的 emailId，用于后续查询邮件

    def _api_headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _random_name(self) -> str:
        letters1 = "".join(random.choices(string.ascii_lowercase, k=5))
        numbers = "".join(random.choices(string.digits, k=random.randint(1, 3)))
        letters2 = "".join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))
        return letters1 + numbers + letters2

    def create_mailbox(self) -> str:
        """创建临时邮箱，返回邮箱地址"""
        name = self._random_name()
        resp = self.session.post(
            f"{self.worker_domain}/api/emails/generate",
            json={
                "name": name,
                "domain": self.email_domain,
                "expiryTime": 3600000,  # 1小时过期
            },
            headers=self._api_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"邮箱创建失败: {resp.status_code} - {resp.text[:500]}")
            raise RuntimeError(f"邮箱创建失败: {resp.status_code} - {resp.text[:300]}")
        result = resp.json()
        # 尝试从响应中获取邮箱地址和 ID
        self.email_id = result.get("id") or result.get("emailId")
        email = result.get("address") or result.get("email")
        # 如果返回中没有完整地址，自行拼接
        if not email and self.email_id:
            email = f"{name}@{self.email_domain}"
        if not email:
            raise RuntimeError(f"邮箱创建失败: {result}")
        logger.info(f"临时邮箱已创建: {email}")
        return email

    def _fetch_emails(self):
        """获取该邮箱的邮件列表"""
        if not self.email_id:
            return []
        headers = {"X-API-Key": self.api_key}
        resp = self.session.get(
            f"{self.worker_domain}/api/emails/{self.email_id}",
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            # 兼容不同返回格式: 可能是列表，也可能是 {"results": [...]} 或 {"messages": [...]}
            if isinstance(data, list):
                return data
            return data.get("messages", data.get("results", data.get("data", [])))
        return []

    def _fetch_email_detail(self, message_id: str) -> dict:
        """获取单封邮件详情"""
        if not self.email_id:
            return {}
        headers = {"X-API-Key": self.api_key}
        resp = self.session.get(
            f"{self.worker_domain}/api/emails/{self.email_id}/{message_id}",
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
        return {}

    @staticmethod
    def _extract_otp(content: str) -> str | None:
        """从邮件内容中提取 OTP"""
        patterns = [r"代码为\s*(\d{6})", r"code is\s*(\d{6})", r"(\d{6})"]
        for pattern in patterns:
            matches = re.findall(pattern, content)
            if matches:
                return matches[0]
        return None

    def wait_for_otp(self, email: str, timeout: int = 120) -> str:
        """阻塞等待 OTP 验证码"""
        logger.info(f"等待 OTP 验证码 (最长 {timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            emails = self._fetch_emails()
            for item in emails:
                # 尝试多种字段名以兼容不同版本 moemail
                sender = (item.get("source") or item.get("from") or item.get("sender") or "").lower()
                raw = item.get("raw") or item.get("text") or item.get("body") or item.get("html") or ""
                subject = (item.get("subject") or "").lower()

                if "openai" in sender or "openai" in raw.lower() or "openai" in subject:
                    otp = self._extract_otp(raw)
                    if otp:
                        logger.info(f"收到 OTP: {otp}")
                        return otp

                    # 如果 raw 里没找到，尝试获取邮件详情
                    msg_id = item.get("id") or item.get("messageId")
                    if msg_id and not otp:
                        detail = self._fetch_email_detail(msg_id)
                        detail_text = detail.get("raw") or detail.get("text") or detail.get("html") or ""
                        otp = self._extract_otp(detail_text)
                        if otp:
                            logger.info(f"收到 OTP: {otp}")
                            return otp
            time.sleep(3)
        raise TimeoutError(f"等待 OTP 超时 ({timeout}s)")

    def fetch_all_otp_codes(self, email: str) -> list:
        """获取邮箱中所有 OTP 验证码 (用于 Codex OAuth 阶段逐个尝试)"""
        codes = []
        try:
            emails = self._fetch_emails()
            for item in emails:
                sender = (item.get("source") or item.get("from") or item.get("sender") or "").lower()
                raw = item.get("raw") or item.get("text") or item.get("body") or item.get("html") or ""
                subject = (item.get("subject") or "").lower()

                if "openai" in sender or "openai" in raw.lower() or "openai" in subject:
                    otp = self._extract_otp(raw)
                    if otp and otp not in codes:
                        codes.append(otp)

                    # 尝试获取邮件详情
                    msg_id = item.get("id") or item.get("messageId")
                    if msg_id:
                        detail = self._fetch_email_detail(msg_id)
                        detail_text = detail.get("raw") or detail.get("text") or detail.get("html") or ""
                        otp = self._extract_otp(detail_text)
                        if otp and otp not in codes:
                            codes.append(otp)
        except Exception as e:
            logger.warning(f"获取 OTP 列表异常: {e}")
        return codes
