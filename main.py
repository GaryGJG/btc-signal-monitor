"""
BTC 多空信号系统主程序。
数据采集（Binance + 恐惧贪婪指数）→ 指标计算 → 信号引擎 → Telegram 推送 + Web 仪表盘。
"""
import logging
import logging.handlers
import time

import settings as cfg
import collector
import database as db
import indicators
import telegram_sender
import web_server
from signal_engine import SignalEngine

log = logging.getLogger("main")


def setup_logging():
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.LOG_LEVEL, logging.INFO))
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.handlers.RotatingFileHandler(
        cfg.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


def collect_snapshot(last_agg_id):
    """一轮完整采集 + 指标计算，返回 (snapshot, new_last_agg_id)。"""
    spot = collector.fetch_spot_5m()
    futures = collector.fetch_futures_5m()
    if not spot:
        return None, last_agg_id

    trades, last_agg_id = collector.fetch_large_trades(last_agg_id)
    db.save_large_trades(trades)
    whale = db.whale_stats()
    if whale["trade_count"] < 300:
        # 大额交易样本不足（刚启动的积累期）时，用 30 天日线 VWAP 兜底主力成本
        daily = collector.fetch_daily(cfg.WHALE_COST_WINDOW_DAYS)
        vol = sum(b["volume"] for b in daily)
        whale["cost"] = sum(b["quote_vol"] for b in daily) / vol if vol else whale["cost"]

    price = spot[-1]["close"]
    hourly = indicators.rolling_hourly_netflows(spot)
    top_ratios = collector.fetch_top_trader_ratio()
    fng = collector.fetch_fear_greed()
    account_ratio = collector.fetch_global_account_ratio()
    funding = collector.fetch_funding()
    oi = collector.fetch_open_interest_hist()
    oi_change = None
    if len(oi) >= 2 and oi[0]["oi_value"]:
        oi_change = (oi[-1]["oi_value"] - oi[0]["oi_value"]) / oi[0]["oi_value"] * 100

    snap = {
        "ready": True,
        "updated": int(time.time()),
        "price": price,
        "price_change_24h": indicators.price_change(spot, 288),
        "price_change_4h": indicators.price_change(spot, 48),
        "price_change_1h": indicators.price_change(spot, 12),
        "spot_netflow": indicators.netflow_windows(spot),
        "futures_netflow": indicators.netflow_windows(futures),
        "hourly_netflows": hourly,
        "vol_stats": indicators.volume_stats_5m(spot),
        "whale": whale,
        "large_trade_usd": cfg.LARGE_TRADE_USD,
        "top_trader_ratios": top_ratios,
        "top_trader_ratio": top_ratios[-1]["ratio"] if top_ratios else None,
        "fear_greed": fng,
        "sentiment": indicators.sentiment(fng, account_ratio),
        "funding_rate": funding["funding_rate"] if funding else None,
        "oi_change_24h": oi_change,
        "dense_areas": indicators.dense_areas(collector.fetch_hourly_7d(), price),
    }
    snap["price_market"] = indicators.price_market_type(snap)

    # 仪表盘用：逐小时净流入序列（现货+合约，近 72 小时）
    fut_hourly = indicators.rolling_hourly_netflows(futures)
    n = min(len(hourly), len(fut_hourly), 72)
    now_h = snap["updated"] // 3600 * 3600
    snap["hourly_series"] = [
        {"ts": now_h - (n - 1 - i) * 3600, "v": hourly[-n + i] + fut_hourly[-n + i]}
        for i in range(n)]
    return snap, last_agg_id


def main():
    setup_logging()
    db.init()
    port = web_server.start()
    log.info("=" * 50)
    log.info("BTC 多空信号系统启动 | 仪表盘: http://<服务器IP>:%d/", port)
    log.info("=" * 50)

    engine = SignalEngine()
    last_agg_id = db.kv_get("last_agg_id")

    while True:
        start = time.time()
        try:
            snap, last_agg_id = collect_snapshot(last_agg_id)
            if snap:
                db.kv_set("last_agg_id", last_agg_id)
                # 多空指标有效性追踪：翻转结算 + 每小时价格变化快照
                db.track_trend(snap["price_market"]["type"], snap["price"],
                               snap["updated"])
                snap["trend_stats"] = db.trend_stats(price_now=snap["price"])
                new_signals = engine.evaluate(snap)
                for sig in new_signals:
                    db.save_signal(sig)
                    telegram_sender.send_signal(sig)
                    log.info("信号: [%s] %s", sig["name"], sig["title"])
                snap["signals"] = db.recent_signals()
                # 精简不需要暴露的原始序列
                snap.pop("hourly_netflows", None)
                snap.pop("top_trader_ratios", None)
                snap.pop("vol_stats", None)
                web_server.update_state(snap)
                pm = snap["price_market"]
                log.info("BTC $%.0f | 趋势=%s(%d/6) | 现货24H净流入=%s | 大单24H=%s",
                         snap["price"], "上涨" if pm["type"] == 1 else "下跌", pm["score"],
                         f"{snap['spot_netflow']['24h'] / 1e6:.0f}M",
                         f"{snap['whale']['netflow_24h'] / 1e6:.0f}M")
        except Exception:
            log.exception("主循环异常")
        time.sleep(max(5, cfg.POLL_INTERVAL - (time.time() - start)))


if __name__ == "__main__":
    main()
