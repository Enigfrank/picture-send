"""企业微信 API 客户端，包含 access_token 管理和昵称查询。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from astrbot.api import logger

from config import (
    NICKNAME_CACHE_TTL_SECONDS,
    TOKEN_MIN_VALID_SECONDS,
    TOKEN_REFRESH_BUFFER_SECONDS,
    WECOM_GETTOKEN_URL,
    WECOM_KF_CUSTOMER_BATCHGET_URL,
)
from http_client import HttpClient


class WecomClient:
    """企业微信客服 API 客户端。

    负责 access_token 的获取与缓存，以及用户昵称的查询与缓存。
    """

    def __init__(
        self,
        corp_id: str,
        kf_secret: str,
        http_client: HttpClient,
    ) -> None:
        self._corp_id = corp_id
        self._kf_secret = kf_secret
        self._http = http_client

        self._token_lock = asyncio.Lock()
        self._access_token: str = ""
        self._access_token_expire_at: int = 0

        # 昵称缓存: {external_userid: {"nickname": str, "cached_at": int}}
        self._nickname_cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = asyncio.Lock()

    async def fetch_user_name(self, external_userid: str) -> str:
        """查询指定 external_userid 的微信昵称，优先使用缓存。"""
        if not external_userid or external_userid == "unknown":
            return ""

        if not self._corp_id or not self._kf_secret:
            logger.warning("跳过查找：缺少 wecom_corp_id 或 wecom_kf_secret")
            return ""

        # 检查缓存
        cached = await self._get_cached_nickname(external_userid)
        if cached:
            return cached

        access_token = await self._get_access_token()
        if not access_token:
            return ""

        try:
            payload = await self._http.post_json(
                WECOM_KF_CUSTOMER_BATCHGET_URL,
                {"access_token": access_token},
                {
                    "external_userid_list": [external_userid],
                    "need_enter_session_context": 0,
                },
            )
        except Exception as exc:
            logger.warning("请求失败 | user_id=%s | error=%s", external_userid, exc)
            return ""

        errcode = int(payload.get("errcode", -1))
        if errcode != 0:
            logger.warning(
                "查找失败 | user_id=%s | errcode=%s | errmsg=%s",
                external_userid,
                payload.get("errcode"),
                payload.get("errmsg"),
            )
            return ""

        customer_list = payload.get("customer_list") or []
        if not customer_list:
            invalid_list = payload.get("invalid_external_userid") or []
            logger.warning(
                "查找无结果 | user_id=%s | invalid_external_userid=%s",
                external_userid,
                invalid_list,
            )
            return ""

        nickname = str(customer_list[0].get("nickname", "")).strip()
        if nickname:
            await self._set_cached_nickname(external_userid, nickname)

        return nickname

    async def _get_access_token(self) -> str:
        """获取有效的 access_token，支持双重检查锁缓存。"""
        now = int(datetime.now().timestamp())
        if self._access_token and now < self._access_token_expire_at:
            return self._access_token

        async with self._token_lock:
            now = int(datetime.now().timestamp())
            if self._access_token and now < self._access_token_expire_at:
                return self._access_token

            try:
                payload = await self._http.get_json(
                    WECOM_GETTOKEN_URL,
                    {
                        "corpid": self._corp_id,
                        "corpsecret": self._kf_secret,
                    },
                )
            except Exception as exc:
                logger.error("无法获取wecom令牌: %s", exc)
                return ""

            errcode = int(payload.get("errcode", -1))
            if errcode != 0:
                logger.error(
                    "获取令牌失败 | errcode=%s | errmsg=%s",
                    payload.get("errcode"),
                    payload.get("errmsg"),
                )
                return ""

            access_token = str(payload.get("access_token", "")).strip()
            expires_in = int(payload.get("expires_in", 7200) or 7200)

            if not access_token:
                logger.error("访问令牌缺失")
                return ""

            self._access_token = access_token
            self._access_token_expire_at = now + max(
                expires_in - TOKEN_REFRESH_BUFFER_SECONDS,
                TOKEN_MIN_VALID_SECONDS,
            )
            return self._access_token

    async def _get_cached_nickname(self, external_userid: str) -> str:
        """从缓存中获取昵称，如果过期则返回空。"""
        async with self._cache_lock:
            entry = self._nickname_cache.get(external_userid)
            if not entry:
                return ""

            now = int(datetime.now().timestamp())
            if now - entry.get("cached_at", 0) > NICKNAME_CACHE_TTL_SECONDS:
                self._nickname_cache.pop(external_userid, None)
                return ""

            return entry.get("nickname", "")

    async def _set_cached_nickname(self, external_userid: str, nickname: str) -> None:
        """将昵称写入缓存。"""
        async with self._cache_lock:
            self._nickname_cache[external_userid] = {
                "nickname": nickname,
                "cached_at": int(datetime.now().timestamp()),
            }
