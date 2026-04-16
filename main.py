from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("picture-send", "Enigfrank", "发送作业插件插件", "1.0.1")
class MyPlugin(Star):
    """提供作业图片发送能力的 AstrBot 插件。"""

    _HOMEWORK_BASE_DIR = Path("/AstrBot/data/homework")
    _HOMEWORK_DEFAULT_STEM = "hm"
    _HOMEWORK_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

    def __init__(self, context: Context):
        """初始化插件上下文。"""
        super().__init__(context)

    async def initialize(self):
        """插件加载后输出初始化日志。"""
        logger.info("homework plugin initialized")

    @filter.command("homework")
    async def homework(self, event: AstrMessageEvent):
        """发送默认作业图片，并动态匹配可用后缀。"""
        image_path = self._resolve_default_homework_image()
        if image_path is None:
            yield event.plain_result("未找到默认作业图片，请检查 homework 目录中的 hm 文件。")
            return

        logger.info(
            "send default homework image, sender=%s, image=%s",
            event.get_sender_name(),
            image_path,
        )
        yield event.image_result(image_path)

    def _resolve_default_homework_image(self):
        """在固定目录中按后缀优先级解析默认作业图片路径。"""
        for suffix in self._HOMEWORK_SUFFIXES:
            candidate = self._HOMEWORK_BASE_DIR / f"{self._HOMEWORK_DEFAULT_STEM}{suffix}"
            if candidate.is_file():
                return str(candidate)
        return None

    async def terminate(self):
        """插件卸载前输出销毁日志。"""
        logger.info("homework plugin terminated")
