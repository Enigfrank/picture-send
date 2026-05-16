"""统计数据的加载、保存与查询管理。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from astrbot.api import logger

from config import DEFAULT_KEEP_LATEST_RECORDS


class StatsManager:
    """管理作业请求统计的持久化存储。"""

    def __init__(self, stats_file: Path, keep_latest_records: int = DEFAULT_KEEP_LATEST_RECORDS) -> None:
        self._stats_file = stats_file
        self._keep_latest_records = keep_latest_records
        self._data_lock = asyncio.Lock()

    async def record_request(
        self,
        user_id: str,
        user_name: str,
        request_time: str,
    ) -> int:
        """记录一次请求，返回该用户的累计请求次数。"""
        async with self._data_lock:
            data = await self._load()

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

            await self._save(data)
            return int(user_item["total_count"])

    async def get_summary(self) -> dict[str, Any]:
        """获取统计摘要，包含总次数和各用户明细。"""
        async with self._data_lock:
            data = await self._load()

        total_requests = int(data.get("total_requests", 0))
        users = data.get("users", {})

        user_list = []
        for uid, item in users.items():
            user_list.append(
                {
                    "user_id": uid,
                    "user_name": item.get("user_name", "未知用户"),
                    "total_count": int(item.get("total_count", 0)),
                    "last_request_at": item.get("last_request_at", ""),
                }
            )

        # 按请求次数降序排列
        user_list.sort(key=lambda x: x["total_count"], reverse=True)

        return {
            "total_requests": total_requests,
            "user_count": len(user_list),
            "users": user_list,
        }

    async def _load(self) -> dict[str, Any]:
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
            logger.warning("无法加载统计文件 %s: %s", self._stats_file, exc)
            return {
                "version": 1,
                "updated_at": "",
                "total_requests": 0,
                "users": {},
            }

    async def _save(self, data: dict[str, Any]) -> None:
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
            logger.error("无法保存统计文件 %s: %s", self._stats_file, exc)
