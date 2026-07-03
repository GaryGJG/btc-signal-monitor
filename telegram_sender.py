"""Telegram 推送。未配置 token 时降级为日志输出。"""
import html
import logging

import requests

import settings as cfg

log = logging.getLogger("telegram")

_ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}


def send_signal(sig):
    text = (f"{_ICON.get(sig['direction'], 'ℹ️')} <b>{html.escape(sig['title'])}</b>\n"
            f"{html.escape(sig['content'])}\n"
            f"<i>#{html.escape(sig['name'])} · BTC</i>")
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        log.info("[DRY-RUN 未配置Telegram] %s", text.replace("\n", " | "))
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10)
        ok = resp.status_code == 200 and resp.json().get("ok")
        if not ok:
            log.error("Telegram 发送失败: %s", resp.text[:300])
        return ok
    except Exception as exc:
        log.error("Telegram 请求异常: %s", exc)
        return False
