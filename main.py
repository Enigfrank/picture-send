from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


@register("picture-send", "Enigfrank", "发送作业图片并记录企微用户请求统计", "1.2.0")
class MyPlugin(Star):
    """只适配企业微信（wecom）的作业图片发送插件。"""

    _HOMEWORK_BASE_DIR = Path("/AstrBot/data/homework")
    _HOMEWORK_DEFAULT_STEM = "hm"
    _HOMEWORK_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

    _WECOM_GETTOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    _WECOM_EXTERNALCONTACT_GET_URL = "https://qyapi.weixin.qq.com/cgi-bin/externalcontact/get"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self._data_lock = asyncio.Lock()
        self._token_lock = asyncio.Lock()

        self._access_token: str = ""
        self._access_token_expire_at: int = 0

        self._plugin_data_dir = self._build_plugin_data_dir()
        self._stats_file = self._plugin_data_dir / self._get_stats_filename()

        self._wecom_corp_id = str(self.config.get("wecom_corp_id", "")).strip()
        self._wecom_contact_secret = str(self.config.get("wecom_contact_secret", "")).strip()
        self._enable_name_lookup = bool(self.config.get("enable_wecom_name_lookup", True))

        log_settings = self.config.get("log_settings", {}) or {}
        self._log_enabled = bool(log_settings.get("enabled", True))
        self._mask_user_id_enabled = bool(log_settings.get("mask_user_id", True))
        self._log_template = str(
            log_settings.get(
                "template",
                "📊 作业请求 | user_id={user_id} | user_name={user_name} [api={api}] | platform={platform} | count={count} | time={time}",
            )
        )

        stats_storage = self.config.get("stats_storage", {}) or {}
        self._stats_enabled = bool(stats_storage.get("enabled", True))
        self._keep_latest_records = int(stats_storage.get("keep_latest_records", 0) or 0)

    async def initialize(self):
        logger.info(
            "作业插件已初始化 | data_dir=%s | stats_file=%s",
            self._plugin_data_dir,
            self._stats_file,
        )

    @filter.command("作业")
    @filter.command("homework")
    async def homework(self, event: AstrMessageEvent):
        """发送默认作业图片，并记录企微用户请求统计。"""
        platform = (event.get_platform_name() or "").strip().lower()
        if platform != "wecom":
            yield event.plain_result("本插件仅支持企业微信（wecom）。")
            return

        image_path = self._resolve_default_homework_image()
        if image_path is None:
            yield event.plain_result("未找到默认作业图片，请检查 homework 目录中的 hm 文件。")
            return

        user_id = self._get_user_id(event)
        request_time = self._get_request_time(event)

        # 先取事件里已有昵称，再尝试企微 API 补查
        event_user_name = self._clean_text(event.get_sender_name())
        user_name = event_user_name or "未知用户"

        api_ok = False
        if self._enable_name_lookup and user_id and user_id != "unknown":
            fetched_name = await self._fetch_wecom_user_name(user_id)
            if fetched_name:
                user_name = fetched_name
                api_ok = True

        total_count = 0
        if self._stats_enabled:
            total_count = await self._record_request(
                user_id=user_id,
                user_name=user_name,
                platform=platform,
                request_time=request_time,
            )

        if self._log_enabled:
            log_user_id = self._mask_user_id(user_id) if self._mask_user_id_enabled else user_id
            logger.info(
                self._log_template.format(
                    user_id=log_user_id,
                    user_name=user_name,
                    api="✓" if api_ok else "×",
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

    def _resolve_default_homework_image(self) -> str | None:
        for suffix in self._HOMEWORK_SUFFIXES:
            candidate = self._HOMEWORK_BASE_DIR / f"{self._HOMEWORK_DEFAULT_STEM}{suffix}"
            if candidate.is_file():
                return str(candidate)
        return None

    def _build_plugin_data_dir(self) -> Path:
        base_dir = Path(get_astrbot_data_path())
        plugin_name = getattr(self, "name", None) or "picture-send"
        plugin_data_dir = base_dir / "plugin_data" / plugin_name
        plugin_data_dir.mkdir(parents=True, exist_ok=True)
        return plugin_data_dir

    def _get_stats_filename(self) -> str:
        stats_storage = self.config.get("stats_storage", {}) or {}
        file_name = str(stats_storage.get("file_name", "homework_stats.json")).strip()
        return file_name or "homework_stats.json"

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        user_id = self._clean_text(event.get_sender_id())
        if user_id:
            return user_id

        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        if sender is not None:
            sender_user_id = self._clean_text(getattr(sender, "user_id", ""))
            if sender_user_id:
                return sender_user_id

        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict):
            for key in ("external_userid", "userid", "user_id", "FromUserName"):
                value = self._clean_text(raw_message.get(key, ""))
                if value:
                    return value

        return "unknown"

    def _get_request_time(self, event: AstrMessageEvent) -> str:
        timestamp = getattr(getattr(event, "message_obj", None), "timestamp", None)
        try:
            if timestamp is not None:
                return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _mask_user_id(self, user_id: str) -> str:
        if not user_id:
            return "unknown"
        if len(user_id) <= 14:
            return user_id
        return f"{user_id[:14]}..."

    async def _record_request(
        self,
        user_id: str,
        user_name: str,
        platform: str,
        request_time: str,
    ) -> int:
        async with self._data_lock:
            data = await self._load_stats()

            data["version"] = 1
            data["updated_at"] = request_time
            data["total_requests"] = int(data.get("total_requests", 0)) + 1

            users = data.setdefault("users", {})
            user_item = users.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "user_name": user_name,
                    "platform": platform,
                    "total_count": 0,
                    "request_timestamps": [],
                    "last_request_at": "",
                },
            )

            user_item["user_id"] = user_id
            user_item["user_name"] = user_name
            user_item["platform"] = platform
            user_item["total_count"] = int(user_item.get("total_count", 0)) + 1
            user_item["last_request_at"] = request_time

            timestamps = user_item.setdefault("request_timestamps", [])
            timestamps.append(request_time)

            if self._keep_latest_records > 0:
                user_item["request_timestamps"] = timestamps[-self._keep_latest_records :]

            await self._save_stats(data)
            return int(user_item["total_count"])

    async def _load_stats(self) -> dict[str, Any]:
        if not self._stats_file.exists():
            return {
                "version": 1,
                "updated_at": "",
                "total_requests": 0,
                "users": {},
            }

        def _read() -> dict[str, Any]:
            with self._stats_file.open("r", encoding="utf-8") as f:
                return json.load(f)

        try:
            return await asyncio.to_thread(_read)
        except Exception as exc:
            logger.warning("加载统计文件失败 %s: %s", self._stats_file, exc)
            return {
                "version": 1,
                "updated_at": "",
                "total_requests": 0,
                "users": {},
            }

    async def _save_stats(self, data: dict[str, Any]) -> None:
        def _write_atomic() -> None:
            self._stats_file.parent.mkdir(parents=True, exist_ok=True)

            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self._stats_file.parent),
                delete=False,
                prefix="homework_stats_",
                suffix=".tmp",
            ) as tmp:
                json.dump(data, tmp, ensure_ascii=False, indent=2)
                tmp_path = Path(tmp.name)

            tmp_path.replace(self._stats_file)

        try:
            await asyncio.to_thread(_write_atomic)
        except Exception as exc:
            logger.error("保存统计文件失败 %s: %s", self._stats_file, exc)

    async def _fetch_wecom_user_name(self, external_userid: str) -> str:
        """
        通过 external_userid 获取外部联系人昵称。
        如果 sender_id 不是 external_userid，这里会失败，然后回退到 event.get_sender_name()。
        """
        if not self._wecom_corp_id or not self._wecom_contact_secret:
            logger.warning("跳过 wecom 昵称查找：缺少 wecom_corp_id 或 wecom_contact_secret")
            return ""

        access_token = await self._get_wecom_access_token()
        if not access_token:
            return ""

        try:
            payload = await self._http_get_json(
                self._WECOM_EXTERNALCONTACT_GET_URL,
                {
                    "access_token": access_token,
                    "external_userid": external_userid,
                },
            )
        except Exception as exc:
            logger.warning("昵称查找请求失败 | user_id=%s | error=%s", external_userid, exc)
            return ""

        errcode = int(payload.get("errcode", -1))
        if errcode != 0:
            logger.warning(
                "昵称查找失败 | user_id=%s | errcode=%s | errmsg=%s",
                external_userid,
                payload.get("errcode"),
                payload.get("errmsg"),
            )
            return ""

        external_contact = payload.get("external_contact") or {}
        name = self._clean_text(external_contact.get("name", ""))
        return name

    async def _get_wecom_access_token(self) -> str:
        now = int(datetime.now().timestamp())
        if self._access_token and now < self._access_token_expire_at:
            return self._access_token

        async with self._token_lock:
            now = int(datetime.now().timestamp())
            if self._access_token and now < self._access_token_expire_at:
                return self._access_token

            try:
                payload = await self._http_get_json(
                    self._WECOM_GETTOKEN_URL,
                    {
                        "corpid": self._wecom_corp_id,
                        "corpsecret": self._wecom_contact_secret,
                    },
                )
            except Exception as exc:
                logger.error("未能获取 wecom access_token: %s", exc)
                return ""

            errcode = int(payload.get("errcode", -1))
            if errcode != 0:
                logger.error(
                    "获取 wecom access_token 失败 | errcode=%s | errmsg=%s",
                    payload.get("errcode"),
                    payload.get("errmsg"),
                )
                return ""

            access_token = self._clean_text(payload.get("access_token", ""))
            expires_in = int(payload.get("expires_in", 7200) or 7200)

            if not access_token:
                logger.error("缺少 access_token")
                return ""

            self._access_token = access_token
            self._access_token_expire_at = now + max(expires_in - 120, 60)
            return self._access_token

    async def _http_get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        def _request() -> dict[str, Any]:
            query = urlencode(params)
            req = Request(
                url=f"{url}?{query}",
                headers={
                    "User-Agent": "AstrBot-Picture-Send/1.2.0",
                },
                method="GET",
            )
            with urlopen(req, timeout=10) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                text = resp.read().decode(charset)
                return json.loads(text)

        return await asyncio.to_thread(_request)

    async def terminate(self):
        logger.info("homework plugin terminated")