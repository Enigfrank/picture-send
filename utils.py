"""通用工具函数。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from astrbot.api import logger

from config import MASK_MAX_LENGTH


def clean_text(value: Any) -> str:
    """将任意值清洗为字符串，去除首尾空白。"""
    if value is None:
        return ""
    return str(value).strip()


def mask_user_id(user_id: str) -> str:
    """对用户 ID 进行脱敏处理，超过最大长度时截断并追加省略号。"""
    if not user_id:
        return "unknown"
    if len(user_id) <= MASK_MAX_LENGTH:
        return user_id
    return f"{user_id[:MASK_MAX_LENGTH]}..."


def format_request_time(timestamp: Any) -> str:
    """将时间戳格式化为可读字符串，失败时返回当前时间。"""
    try:
        if timestamp is not None:
            ts = int(timestamp)
            # 简单校验时间戳范围：1970-01-01 到 2100-01-01
            if 0 <= ts <= 4102444800:
                return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError) as exc:
        logger.debug("时间戳解析失败: %s (timestamp=%s)", exc, timestamp)
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
