"""
批量注册引擎
使用 asyncio + Semaphore 控制并发，每个任务独立创建 Config/MailProvider/AuthFlow 实例。
单任务失败不中断整体流程。
"""
import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from config import Config, CardInfo, BillingInfo, CaptchaConfig
from mail_provider import MailProvider
from auth_flow import AuthFlow
from logger import ResultStore
from code_manager import record_execution

logger = logging.getLogger(__name__)

OUTPUT_DIR = "test_outputs"


@dataclass
class TaskResult:
    """单个注册任务的结果"""
    task_id: int
    status: str = "pending"     # pending | running | success | failed | cancelled
    email: str = ""
    error: str = ""             # 失败原因
    auth_dict: dict = field(default_factory=dict)
    duration: float = 0.0       # 耗时秒数
    sync_status: str = ""       # 自动同步状态: "" | success | failed | skipped
    sync_error: str = ""        # 同步失败原因


class BatchRegisterManager:
    """批量注册调度器"""

    def __init__(self, config_snapshot: dict, total: int, concurrency: int,
                 verified_code: str = "", auto_sync_config: dict = None):
        self.config_snapshot = config_snapshot
        self.total = total
        self.concurrency = concurrency
        self.verified_code = verified_code  # 兑换码, 用于写入数据库执行记录
        self.auto_sync_config = auto_sync_config  # NewApi 自动同步配置, None 表示不自动同步
        self.results: list[TaskResult] = [TaskResult(task_id=i) for i in range(total)]
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── 统计属性 ──

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.status == "success")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.results if r.status in ("success", "failed", "cancelled"))

    @property
    def running_count(self) -> int:
        return sum(1 for r in self.results if r.status == "running")

    @property
    def failed_details(self) -> list[TaskResult]:
        """返回所有失败的任务"""
        return [r for r in self.results if r.status == "failed"]

    @property
    def is_done(self) -> bool:
        return self._done_event.is_set()

    # ── 控制方法 ──

    def start(self):
        """启动后台线程运行批量注册"""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def cancel(self):
        """取消：不再启动新任务，已运行的等其自然结束"""
        logger.info("批量注册收到取消信号")
        self._cancel_event.set()

    # ── 内部实现 ──

    def _run_loop(self):
        """在后台线程中创建并运行 asyncio 事件循环"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_all())
        except Exception as e:
            logger.error(f"批量注册事件循环异常: {e}")
        finally:
            loop.close()
            self._done_event.set()

    async def _run_all(self):
        """用 Semaphore 控制并发，逐个调度任务"""
        sem = asyncio.Semaphore(self.concurrency)

        async def _guarded_run(task_id: int):
            async with sem:
                if self._cancel_event.is_set():
                    self.results[task_id].status = "cancelled"
                    return
                await self._run_single(task_id)

        tasks = [asyncio.create_task(_guarded_run(i)) for i in range(self.total)]
        await asyncio.gather(*tasks)

    async def _run_single(self, task_id: int):
        """执行单个注册任务 — 独立实例，容错不抛出"""
        result = self.results[task_id]
        result.status = "running"
        start_time = time.time()

        task_logger = logging.getLogger(f"batch.task_{task_id + 1}")
        task_logger.info(f"[任务 {task_id + 1}/{self.total}] 开始注册...")

        try:
            # 构建独立 Config
            cs = self.config_snapshot
            cfg = Config()
            cfg.proxy = cs.get("proxy")
            cfg.mail.email_domain = cs.get("mail_domain", "")
            cfg.mail.worker_domain = cs.get("mail_worker", "")
            cfg.mail.admin_token = cs.get("mail_token", "")
            cfg.register_mode = cs.get("register_mode", "otp")
            cfg.default_password = cs.get("default_password", "")

            # 独立 MailProvider
            mp = MailProvider(
                worker_domain=cfg.mail.worker_domain,
                admin_token=cfg.mail.admin_token,
                email_domain=cfg.mail.email_domain,
            )

            # 独立 AuthFlow
            af = AuthFlow(cfg)

            # 执行注册（同步阻塞，通过 to_thread 桥接到线程池）
            if cs.get("register_mode") == "password":
                auth_result = await asyncio.to_thread(
                    af.run_register_with_password, mp, password=cs.get("default_password", "")
                )
            else:
                auth_result = await asyncio.to_thread(af.run_register, mp)

            # 成功
            result.status = "success"
            result.email = auth_result.email
            result.auth_dict = auth_result.to_dict()
            result.auth_dict["code"] = self.verified_code  # 记录兑换码来源
            result.duration = time.time() - start_time

            task_logger.info(
                f"[任务 {task_id + 1}/{self.total}] ✅ 注册成功: {auth_result.email} ({result.duration:.1f}s)"
            )

            # 即时持久化凭证
            try:
                store = ResultStore(output_dir=OUTPUT_DIR)
                store.save_credentials(auth_result.to_dict())
                store.append_credentials_csv(auth_result.to_dict())
            except Exception as e:
                task_logger.warning(f"[任务 {task_id + 1}] 保存凭证异常: {e}")

            # 自动同步到 NewApi
            if self.auto_sync_config:
                sync_ok, sync_msg = self._sync_to_newapi(auth_result.to_dict(), task_id, task_logger)
                result.sync_status = "success" if sync_ok else "failed"
                result.sync_error = "" if sync_ok else sync_msg
                # 同步成功则标记凭证
                if sync_ok:
                    result.auth_dict["synced_to_newapi"] = True
            else:
                result.sync_status = "skipped"

            # 写入数据库执行记录 (供右侧面板展示)
            _result_dict = auth_result.to_dict()
            if result.sync_status == "success":
                _result_dict["synced_to_newapi"] = True
            self._record_to_db(
                status="success", email=auth_result.email,
                result_dict=_result_dict,
            )

        except Exception as e:
            # ⚠️ 失败不抛出，记录原因后继续
            result.status = "failed"
            result.error = str(e)
            result.duration = time.time() - start_time
            task_logger.error(
                f"[任务 {task_id + 1}/{self.total}] ❌ 注册失败: {e} ({result.duration:.1f}s)"
            )

            # 失败也写入数据库
            self._record_to_db(
                status="failed", error_msg=str(e),
            )

    def _sync_to_newapi(self, cred_dict: dict, task_id: int, task_logger) -> tuple:
        """注册成功后自动将凭证同步到 NewApi，返回 (success: bool, message: str)"""
        import requests as _req

        cfg = self.auto_sync_config
        base_url = cfg.get("base_url", "").rstrip("/")
        admin_token = cfg.get("admin_token", "")
        if not base_url or not admin_token:
            task_logger.warning(f"[任务 {task_id + 1}] ⚠️ 自动同步跳过: NewApi 配置不完整")
            return False, "NewApi 配置不完整"

        email = cred_dict.get("email", "unknown")
        _CRED_KEYS = {
            "type", "email", "expired", "id_token", "account_id",
            "access_token", "last_refresh", "refresh_token",
            "session_token", "device_id", "csrf_token", "password",
        }
        key_data = {k: v for k, v in cred_dict.items() if k in _CRED_KEYS}
        key_json = json.dumps(key_data, ensure_ascii=False)

        ch_type = cfg.get("channel_type", "57")
        payload = {
            "mode": "single",
            "channel": {
                "type": int(ch_type) if str(ch_type).isdigit() else 57,
                "name": f"codex-{email}",
                "key": key_json,
                "models": cfg.get("models", "").strip(),
                "group": cfg.get("group", "default").strip(),
                "priority": int(cfg.get("priority", 0)) if str(cfg.get("priority", "0")).lstrip("-").isdigit() else 0,
                "weight": int(cfg.get("weight", 0)) if str(cfg.get("weight", "0")).lstrip("-").isdigit() else 0,
            },
        }
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
            "New-Api-User": "1",
        }

        try:
            resp = _req.post(f"{base_url}/api/channel/", headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                rj = resp.json()
                if rj.get("success"):
                    task_logger.info(f"[任务 {task_id + 1}] 🔗 自动同步成功: {email}")
                    return True, ""
                else:
                    msg = rj.get("message", resp.text[:200])
                    task_logger.warning(f"[任务 {task_id + 1}] ⚠️ 自动同步失败: {email} — {msg}")
                    return False, msg
            else:
                msg = f"HTTP {resp.status_code}: {resp.text[:100]}"
                task_logger.warning(f"[任务 {task_id + 1}] ⚠️ 自动同步失败: {email} — {msg}")
                return False, msg
        except Exception as e:
            msg = str(e)[:200]
            task_logger.warning(f"[任务 {task_id + 1}] ⚠️ 自动同步异常: {email} — {msg}")
            return False, msg

    def _record_to_db(self, status: str, email: str = "",
                      error_msg: str = "", result_dict: dict = None):
        """将任务结果写入数据库执行记录 (供右侧面板展示)"""
        try:
            result_json = json.dumps(result_dict, ensure_ascii=False, default=str) if result_dict else ""
            record_execution(
                code=self.verified_code,
                status=status,
                email=email,
                error_msg=error_msg,
                result_json=result_json,
            )
        except Exception as e:
            logger.warning(f"写入数据库执行记录失败: {e}")

    def get_summary_text(self) -> str:
        """生成文本摘要"""
        lines = [f"批量注册完成: 总共 {self.total} 条, 成功 {self.success_count} 条, 失败 {self.failed_count} 条"]
        cancelled = sum(1 for r in self.results if r.status == "cancelled")
        if cancelled:
            lines[0] += f", 取消 {cancelled} 条"
        for r in self.failed_details:
            lines.append(f"  #{r.task_id + 1} — {r.error}")
        return "\n".join(lines)
