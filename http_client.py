"""HTTP 客户端封装，预留异步接口便于后续升级。"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from astrbot.api import logger

from config import HTTP_TIMEOUT


class HttpClient:
    """同步 HTTP 客户端的异步包装器。

    当前使用 urllib + asyncio.to_thread 实现，后续可无缝替换为 aiohttp/httpx。
    """

    def __init__(self, user_agent: str = "AstrBot-Picture-Send/1.5.0") -> None:
        self._user_agent = user_agent

    async def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送 GET 请求并返回 JSON 响应。"""

        def _request() -> dict[str, Any]:
            query = urlencode(params)
            req = Request(
                url=f"{url}?{query}",
                headers={"User-Agent": self._user_agent},
                method="GET",
            )
            with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                text = resp.read().decode(charset)
                return json.loads(text)

        return await asyncio.to_thread(_request)

    async def post_json(
        self,
        url: str,
        query_params: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """发送 POST 请求并返回 JSON 响应。"""

        def _request() -> dict[str, Any]:
            full_url = f"{url}?{urlencode(query_params)}"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

            req = Request(
                url=full_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": self._user_agent,
                },
                method="POST",
            )

            with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                text = resp.read().decode(charset)
                return json.loads(text)

        return await asyncio.to_thread(_request)
