"""AstrBot 插件入口：作业图片发送。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# 将插件目录加入模块搜索路径，确保子模块可被正确导入
_plugin_dir = Path(__file__).parent.resolve()
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from config import (
    DEFAULT_LOG_TEMPLATE,
    DEFAULT_STATS_FILE_NAME,
    HOMEWORK_BASE_DIR,
    HOMEWORK_DEFAULT_STEM,
    HOMEWORK_SUFFIXES,
)
from http_client import HttpClient
from stats_manager import StatsManager
from utils import clean_text, format_request_time, mask_user_id
from wecom_client import WecomClient

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:
    get_astrbot_data_path = None


@register("picture-send", "Enigfrank", "发送作业图片", "1.5.0")
class MyPlugin(Star):
    """仅适配企业微信微信客服(wecom)的作业图片插件。"""

    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        super().__init__(context)
        self.config = config or {}

        self._plugin_data_dir = self._build_plugin_data_dir()
        self._stats_file = self._plugin_data_dir / self._get_stats_filename()

        self._wecom_corp_id = clean_text(self.config.get("wecom_corp_id", ""))
        self._wecom_kf_secret = clean_text(self.config.get("wecom_kf_secret", ""))
        self._enable_name_lookup = bool(self.config.get("enable_wecom_name_lookup", True))

        stats_storage = self.config.get("stats_storage", {}) or {}
        self._stats_enabled = bool(stats_storage.get("enabled", True))
        self._keep_latest_records = int(stats_storage.get("keep_latest_records", 0) or 0)

        log_settings = self.config.get("log_settings", {}) or {}
        self._log_enabled = bool(log_settings.get("enabled", True))
        self._mask_user_id_enabled = bool(log_settings.get("mask_user_id", True))
        self._log_template = str(log_settings.get("template", DEFAULT_LOG_TEMPLATE))

        # 初始化子模块
        self._http = HttpClient()
        self._wecom = WecomClient(
            corp_id=self._wecom_corp_id,
            kf_secret=self._wecom_kf_secret,
            http_client=self._http,
        )
        self._stats = StatsManager(
            stats_file=self._stats_file,
            keep_latest_records=self._keep_latest_records,
        )

    async def initialize(self):
        logger.info(
            "作业发送插件初始化完成 | data_dir=%s | stats_file=%s",
            self._plugin_data_dir,
            self._stats_file,
        )

    @filter.command("homework")
    async def homework(self, event: AstrMessageEvent):
        """发送作业图片。用法: /homework"""
        platform = clean_text(event.get_platform_name()).lower()
        if platform != "wecom":
            yield event.plain_result("本插件仅支持企业微信(wecom)")
            return

        yield event.plain_result("收到请求!")
        yield event.plain_result("正在从服务器拉取图片...")

        image_path = self._resolve_default_homework_image()
        if image_path is None:
            yield event.plain_result("未找到默认作业图片,请上报问题给许工!")
            return

        user_id = self._get_user_id(event)
        request_time = format_request_time(
            getattr(getattr(event, "message_obj", None), "timestamp", None)
        )

        event_user_name = clean_text(event.get_sender_name())
        user_name = event_user_name or "未知用户"

        api_ok = False
        if self._enable_name_lookup and user_id and user_id != "unknown":
            fetched_name = await self._wecom.fetch_user_name(user_id)
            if fetched_name:
                user_name = fetched_name
                api_ok = True

        total_count = 0
        if self._stats_enabled:
            total_count = await self._stats.record_request(
                user_id=user_id,
                user_name=user_name,
                platform=platform,
                request_time=request_time,
            )

        if self._log_enabled:
            log_user_id = mask_user_id(user_id) if self._mask_user_id_enabled else user_id
            logger.info(
                self._log_template.format(
                    user_id=log_user_id,
                    user_name=user_name,
                    api="ok" if api_ok else "fail",
                    platform=platform,
                    count=total_count,
                    time=request_time,
                )
            )

        logger.info(
            "发送作业图片, sender_id=%s, sender_name=%s, image=%s",
            user_id,
            user_name,
            image_path,
        )
        yield event.image_result(image_path)

    @filter.command("userid")
    async def userid_lookup(self, event: AstrMessageEvent, target_user_id: str):
        """查询指定用户ID对应的微信昵称。用法: /userid {用户微信ID}"""
        platform = clean_text(event.get_platform_name()).lower()
        if platform != "wecom":
            yield event.plain_result("本插件仅支持企业微信(wecom)")
            return

        target_user_id = clean_text(target_user_id)
        if not target_user_id:
            yield event.plain_result("用法：/userid {用户微信ID}")
            return

        nickname = await self._wecom.fetch_user_name(target_user_id)
        if nickname:
            logger.info(
                "用户昵称查询 | target_user_id=%s | nickname=%s | platform=%s",
                mask_user_id(target_user_id) if self._mask_user_id_enabled else target_user_id,
                nickname,
                platform,
            )
            yield event.plain_result(
                f"用户ID:{target_user_id}\n微信昵称:{nickname}"
            )
            return

        logger.warning(
            "用户昵称查询失败 | target_user_id=%s | platform=%s",
            mask_user_id(target_user_id) if self._mask_user_id_enabled else target_user_id,
            platform,
        )
        yield event.plain_result(
            f"未查询到该用户昵称。\n用户ID：{target_user_id}"
        )

    @filter.command("homework_stats")
    async def homework_stats(self, event: AstrMessageEvent):
        """查询作业请求统计。用法: /homework_stats"""
        platform = clean_text(event.get_platform_name()).lower()
        if platform != "wecom":
            yield event.plain_result("本插件仅支持企业微信(wecom)")
            return

        summary = await self._stats.get_summary()
        total = summary["total_requests"]
        user_count = summary["user_count"]
        users = summary["users"]

        lines = ["作业请求统计", f"总请求次数: {total}", f"用户数: {user_count}", ""]
        lines.append("各用户调用明细:")
        for idx, u in enumerate(users, start=1):
            lines.append(
                f"{idx}. {u['user_name']} ({u['user_id']}): {u['total_count']} 次"
            )

        yield event.plain_result("\n".join(lines))

    def _resolve_default_homework_image(self) -> str | None:
        """解析默认作业图片路径。"""
        for ext in HOMEWORK_SUFFIXES:
            candidate = HOMEWORK_BASE_DIR / f"{HOMEWORK_DEFAULT_STEM}{ext}"
            if candidate.is_file():
                return str(candidate)
        return None

    def _build_plugin_data_dir(self) -> Path:
        if callable(get_astrbot_data_path):
            base_dir = Path(get_astrbot_data_path())
        else:
            base_dir = Path("/AstrBot/data")

        plugin_name = getattr(self, "name", None) or "picture-send"
        plugin_data_dir = base_dir / "plugin_data" / plugin_name
        plugin_data_dir.mkdir(parents=True, exist_ok=True)
        return plugin_data_dir

    def _get_stats_filename(self) -> str:
        stats_storage = self.config.get("stats_storage", {}) or {}
        file_name = clean_text(stats_storage.get("file_name", DEFAULT_STATS_FILE_NAME))
        return file_name or DEFAULT_STATS_FILE_NAME

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        user_id = clean_text(event.get_sender_id())
        if user_id:
            return user_id

        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        if sender is not None:
            sender_user_id = clean_text(getattr(sender, "user_id", ""))
            if sender_user_id:
                return sender_user_id

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict):
            for key in ("external_userid", "userid", "user_id", "FromUserName"):
                value = clean_text(raw_message.get(key, ""))
                if value:
                    return value

        return "unknown"

    async def terminate(self):
        logger.info("作业插件已卸载")
