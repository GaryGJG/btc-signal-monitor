"""加载配置：以 config_defaults.py 为基础，config.py（若存在）覆盖同名大写项。"""
import logging

import config_defaults as _defaults

try:
    import config as _user
except ImportError:
    _user = None

for _name in dir(_defaults):
    if _name.isupper():
        globals()[_name] = getattr(_user, _name, None) if _user and hasattr(_user, _name) \
            else getattr(_defaults, _name)

if not globals().get("TELEGRAM_BOT_TOKEN"):
    logging.getLogger("settings").warning(
        "未配置 TELEGRAM_BOT_TOKEN，信号将只写日志/数据库，不推送 Telegram")
