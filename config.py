"""插件常量与默认配置。"""

from __future__ import annotations

from pathlib import Path

# 作业图片相关常量
HOMEWORK_BASE_DIR = Path("/AstrBot/data/homework")
HOMEWORK_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# 企业微信 API 相关常量
WECOM_GETTOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_KF_CUSTOMER_BATCHGET_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/customer/batchget"

# HTTP 请求超时（秒）
HTTP_TIMEOUT = 10

# Token 提前刷新缓冲时间（秒），避免在边界时刻因网络延迟导致 token 过期
TOKEN_REFRESH_BUFFER_SECONDS = 120

# Token 最小有效时间（秒），防止 expires_in 被篡改或异常值
TOKEN_MIN_VALID_SECONDS = 60

# 用户 ID 脱敏最大显示长度
MASK_MAX_LENGTH = 14

# 昵称缓存有效期（秒），默认 1 小时
NICKNAME_CACHE_TTL_SECONDS = 3600

# 统计相关默认值
DEFAULT_STATS_FILE_NAME = "homework_stats.json"
DEFAULT_KEEP_LATEST_RECORDS = 100

# 日志默认模板
DEFAULT_LOG_TEMPLATE = (
    "作业请求 | user_id={user_id} | user_name={user_name} [api={api}] | "
    "platform={platform} | count={count} | time={time}"
)

# 图片压缩默认配置
DEFAULT_COMPRESSION_ENABLED = False
DEFAULT_COMPRESSION_QUALITY = 85
DEFAULT_COMPRESSION_MAX_WIDTH = 1920
DEFAULT_COMPRESSION_MAX_HEIGHT = 1080
