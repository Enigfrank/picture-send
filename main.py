"""AstrBot 插件入口：作业图片发送。"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

_plugin_dir = Path(__file__).parent.resolve()
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

import config as plugin_config
from http_client import HttpClient
from image_compressor import ImageCompressor
from stats_manager import StatsManager
from utils import clean_text, format_request_time, mask_user_id
from wecom_client import WecomClient

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:
    get_astrbot_data_path = None


def _get_config_default(name: str, fallback: Any) -> Any:
    """读取插件配置常量，并在旧版 config.py 缺失字段时使用兼容默认值。"""
    return getattr(plugin_config, name, fallback)


HOMEWORK_BASE_DIR = _get_config_default("HOMEWORK_BASE_DIR", Path("/AstrBot/data/homework"))
HOMEWORK_SUFFIXES = _get_config_default("HOMEWORK_SUFFIXES", (".png", ".jpg", ".jpeg", ".webp"))
DEFAULT_STATS_FILE_NAME = _get_config_default("DEFAULT_STATS_FILE_NAME", "homework_stats.json")
DEFAULT_LOG_TEMPLATE = _get_config_default(
    "DEFAULT_LOG_TEMPLATE",
    "作业请求 | 微信ID={user_id} | 微信昵称={user_name} [api={api}] | "
    "计数={count} | 时间={time}",
)
DEFAULT_COMPRESSION_ENABLED = _get_config_default("DEFAULT_COMPRESSION_ENABLED", False)
DEFAULT_COMPRESSION_QUALITY = _get_config_default("DEFAULT_COMPRESSION_QUALITY", 85)
DEFAULT_COMPRESSION_MAX_WIDTH = _get_config_default("DEFAULT_COMPRESSION_MAX_WIDTH", 1920)
DEFAULT_COMPRESSION_MAX_HEIGHT = _get_config_default("DEFAULT_COMPRESSION_MAX_HEIGHT", 1080)
DEFAULT_ERROR_FORWARD_ENABLED = _get_config_default("DEFAULT_ERROR_FORWARD_ENABLED", False)
DEFAULT_ERROR_FORWARD_INCLUDE_TRACEBACK = _get_config_default(
    "DEFAULT_ERROR_FORWARD_INCLUDE_TRACEBACK", True
)
DEFAULT_ERROR_FORWARD_NOTIFY_USER = _get_config_default("DEFAULT_ERROR_FORWARD_NOTIFY_USER", True)
DEFAULT_ERROR_FORWARD_MAX_LENGTH = _get_config_default("DEFAULT_ERROR_FORWARD_MAX_LENGTH", 600)


@register("picture-send", "Enigfrank", "发送作业图片", "1.5.2")
class MyPlugin(Star):
    """仅适配企业微信微信客服(wecom)的作业图片插件。"""

    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        super().__init__(context)
        self.config = config or {}

        self._plugin_data_dir = self._build_plugin_data_dir()
        self._stats_file = self._plugin_data_dir / self._get_stats_filename()

        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
            # 优先使用框架专属临时目录
            self._safe_temp_dir = Path(get_astrbot_temp_path()) / "homework_compress"
        except Exception:
            # 降级使用插件数据目录（属于 workspace 白名单）
            self._safe_temp_dir = self._plugin_data_dir / "tmp"

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

        compression = self.config.get("image_compression", {}) or {}
        self._compression_enabled = bool(compression.get("enabled", DEFAULT_COMPRESSION_ENABLED))
        self._compression_quality = int(compression.get("quality", DEFAULT_COMPRESSION_QUALITY))
        self._compression_max_width = int(compression.get("max_width", DEFAULT_COMPRESSION_MAX_WIDTH))
        self._compression_max_height = int(compression.get("max_height", DEFAULT_COMPRESSION_MAX_HEIGHT))

        error_forward = self.config.get("error_forward", {}) or {}
        self._error_forward_enabled = bool(
            error_forward.get("enabled", DEFAULT_ERROR_FORWARD_ENABLED)
        )
        self._error_forward_target_uid = clean_text(error_forward.get("target_uid", ""))
        self._error_forward_include_traceback = bool(
            error_forward.get("include_traceback", DEFAULT_ERROR_FORWARD_INCLUDE_TRACEBACK)
        )
        self._error_forward_notify_user = bool(
            error_forward.get("notify_user", DEFAULT_ERROR_FORWARD_NOTIFY_USER)
        )
        self._error_forward_max_length = int(
            error_forward.get("max_length", DEFAULT_ERROR_FORWARD_MAX_LENGTH) or 0
        )

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
        self._compressor = ImageCompressor(
            enabled=self._compression_enabled,
            quality=self._compression_quality,
            max_width=self._compression_max_width,
            max_height=self._compression_max_height,
        )

    async def initialize(self):
        logger.info(
            "作业发送插件初始化完成 | data_dir=%s | safe_temp_dir=%s",
            self._plugin_data_dir,
            self._safe_temp_dir,
        )

    @filter.command("homework")
    async def homework(self, event: AstrMessageEvent):
        """发送作业图片.指令: homework"""
        try:
            async for result in self._homework_impl(event):
                yield result
        except Exception as exc:
            logger.exception("homework 命令处理失败")
            forwarded = await self._forward_error(exc, event, "homework")
            if self._error_forward_notify_user:
                if forwarded:
                    yield event.plain_result("插件处理出错，已通知管理员。")
                else:
                    yield event.plain_result("插件处理出错，请联系管理员。")

    async def _homework_impl(self, event: AstrMessageEvent):
        platform = clean_text(event.get_platform_name()).lower()
        if platform != "wecom":
            yield event.plain_result("本插件仅支持企业微信(wecom)")
            return

        hitokoto_text = ""
        try:
            hitokoto_data = await self._http.get_json("https://v1.hitokoto.cn/", {})
            hitokoto_text = str(hitokoto_data.get("hitokoto", "")).strip()
        except Exception as exc:
            logger.debug("获取一言失败: %s", exc)

        base_msg = "正在为您拉取作业图片..."
        if hitokoto_text:
            yield event.plain_result(f"{base_msg}\n💡 一言: {hitokoto_text}")
        else:
            yield event.plain_result(base_msg)

        image_paths = self._resolve_homework_images()
        if not image_paths:
            yield event.plain_result("未找到图片,请联系许工!")
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

        for image_path in image_paths:
            # 传入安全临时目录
            compressed_path = self._compressor.compress(image_path, temp_dir=self._safe_temp_dir)
            yield event.image_result(compressed_path)
            if compressed_path != image_path:
                try:
                    Path(compressed_path).unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("清理临时文件失败: %s | error=%s", compressed_path, exc)

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
            yield event.plain_result(f"用户ID:{target_user_id}\n微信昵称:{nickname}")
            return

        yield event.plain_result(f"未查询到该用户昵称。\n用户ID：{target_user_id}")

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
            lines.append(f"{idx}. {u['user_name']} ({u['user_id']}): {u['total_count']} 次")

        yield event.plain_result("\n".join(lines))

    async def _forward_error(
        self,
        exc: Exception,
        event: AstrMessageEvent | None = None,
        command_name: str = "unknown",
    ) -> bool:
        if not self._error_forward_enabled or not self._error_forward_target_uid:
            return False
        if getattr(self, "_error_forwarding", False):
            return False

        self._error_forwarding = True
        try:
            session = self._build_error_forward_session()
            message = self._format_error_message(exc, event, command_name)
            chain = MessageChain().message(message)
            return bool(await self.context.send_message(session, chain))
        except Exception as forward_exc:
            logger.warning("错误转发失败: %s", forward_exc)
            return False
        finally:
            self._error_forwarding = False

    def _build_error_forward_session(self) -> str:
        if self._error_forward_target_uid.count(":") >= 2:
            return self._error_forward_target_uid
        return f"wecom:friend:{self._error_forward_target_uid}"

    def _format_error_message(
        self,
        exc: Exception,
        event: AstrMessageEvent | None,
        command_name: str,
    ) -> str:
        platform = "unknown"
        user_id = "unknown"
        user_name = "未知用户"
        unified_msg_origin = "unknown"
        request_time = format_request_time(None)

        if event is not None:
            platform = clean_text(event.get_platform_name()) or platform
            user_id = self._get_user_id(event)
            user_name = clean_text(event.get_sender_name()) or user_name
            unified_msg_origin = clean_text(getattr(event, "unified_msg_origin", "")) or unified_msg_origin
            request_time = format_request_time(
                getattr(getattr(event, "message_obj", None), "timestamp", None)
            )

        lines = [
            "⚠️ 作业插件出现错误",
            f"命令: {command_name}",
            f"平台: {platform}",
            f"触发用户: {user_name} ({user_id})",
            f"会话: {unified_msg_origin}",
            f"时间: {request_time}",
            f"异常: {type(exc).__name__}: {exc}",
        ]

        if self._error_forward_include_traceback:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            if detail:
                lines.extend(["", "Traceback:", detail])

        message = "\n".join(lines)
        if self._error_forward_max_length > 20 and len(message) > self._error_forward_max_length:
            return message[: self._error_forward_max_length - 20] + "\n...（已截断）"
        return message

    def _resolve_homework_images(self) -> list[str]:
        if not HOMEWORK_BASE_DIR.exists() or not HOMEWORK_BASE_DIR.is_dir():
            return []

        images = []
        for ext in HOMEWORK_SUFFIXES:
            for file_path in HOMEWORK_BASE_DIR.glob(f"*{ext}"):
                if file_path.is_file():
                    images.append(str(file_path))

        images.sort()
        return images

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
