"""图片压缩工具模块。

依赖 Pillow (PIL)。若未安装则压缩功能不可用，将原样返回图片路径。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from astrbot.api import logger

try:
    from PIL import Image

    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False


class ImageCompressor:
    """基于 Pillow 的图片压缩器，支持质量压缩与尺寸缩放。"""

    def __init__(
        self,
        enabled: bool = False,
        quality: int = 85,
        max_width: int = 0,
        max_height: int = 0,
    ):
        """初始化压缩器。

        Args:
            enabled: 是否启用压缩。
            quality: JPEG/WebP 压缩质量，1-100。
            max_width: 最大宽度，超过则等比缩放，0 表示不限制。
            max_height: 最大高度，超过则等比缩放，0 表示不限制。
        """
        self.enabled = enabled and _PIL_AVAILABLE
        self.quality = max(1, min(100, quality))
        self.max_width = max(0, max_width)
        self.max_height = max(0, max_height)

        if enabled and not _PIL_AVAILABLE:
            logger.warning(
                "图片压缩已启用但 Pillow 未安装，压缩功能不可用。"
                "请执行: pip install Pillow"
            )

    def compress(self, image_path: str) -> str:
        """压缩单张图片，返回压缩后的文件路径。

        若压缩失败或 Pillow 不可用，则返回原始路径。
        压缩后的临时文件由调用方负责生命周期管理；
        若需要持久化缓存，可在外部处理。

        Args:
            image_path: 原始图片绝对路径。

        Returns:
            压缩后的图片路径，或原始路径。
        """
        if not self.enabled or not _PIL_AVAILABLE:
            return image_path

        src_path = Path(image_path)
        if not src_path.is_file():
            logger.warning("压缩目标不存在: %s", image_path)
            return image_path

        try:
            with Image.open(src_path) as img:
                # 转换为 RGB（去除透明通道等，兼容 JPEG）
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                # 尺寸缩放
                new_size = self._calculate_size(img.width, img.height)
                if new_size != (img.width, img.height):
                    img = img.resize(new_size, Image.Resampling.LANCZOS)

                # 确定输出格式与后缀
                fmt = img.format or "JPEG"
                ext = self._format_to_extension(fmt, src_path.suffix.lower())

                # 写入临时文件
                fd, tmp_path = tempfile.mkstemp(suffix=ext)
                os.close(fd)

                save_kwargs: dict[str, Any] = {"quality": self.quality}
                if fmt.upper() == "PNG":
                    # PNG 使用 optimize 而非 quality
                    save_kwargs = {"optimize": True}

                img.save(tmp_path, format=fmt, **save_kwargs)

                logger.info(
                    "图片压缩完成 | src=%s | dst=%s | size=%sx%s | quality=%s",
                    image_path,
                    tmp_path,
                    new_size[0],
                    new_size[1],
                    self.quality,
                )
                return tmp_path

        except Exception as exc:
            logger.warning("图片压缩失败: %s | error=%s", image_path, exc)
            return image_path

    def _calculate_size(self, width: int, height: int) -> tuple[int, int]:
        """根据 max_width / max_height 计算等比缩放后的尺寸。"""
        if self.max_width <= 0 and self.max_height <= 0:
            return width, height

        ratio = 1.0
        if self.max_width > 0 and width > self.max_width:
            ratio = min(ratio, self.max_width / width)
        if self.max_height > 0 and height > self.max_height:
            ratio = min(ratio, self.max_height / height)

        if ratio >= 1.0:
            return width, height

        new_width = int(width * ratio)
        new_height = int(height * ratio)
        return max(1, new_width), max(1, new_height)

    @staticmethod
    def _format_to_extension(fmt: str, fallback: str) -> str:
        """将 PIL 格式映射为文件后缀。"""
        mapping = {
            "JPEG": ".jpg",
            "JPG": ".jpg",
            "PNG": ".png",
            "WEBP": ".webp",
        }
        return mapping.get(fmt.upper(), fallback if fallback else ".jpg")
