"""
数据采集：Binance 现货/合约公开行情 + 恐惧贪婪指数。
对应 ValueScan 的三类数据源：
  - 交易所资金流（现货/合约 taker 主动买卖差 = 净流入）
  - 大额交易（aggTrades 中超过阈值的鲸鱼单，用于主力成本/主力行为）
  - 市场情绪（Fear & Greed + 合约多空人数比，替代其社媒情绪）
"""
import time
import logging

import requests

import settings as cfg

log = logging.getLogger("collector")

_session = requests.Session()
_session.headers["User-Agent"] = "btc-signal-monitor/1.0"


def _get(url, params=None, timeout=10):
    for attempt in range(3):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("GET %s 失败(第%d次): %s", url, attempt + 1, exc)
            time.sleep(1 + attempt)
    return None


def fetch_klines(base, path, interval, limit):
    """返回 K 线列表，每项含收盘价/成交额/taker买入额/净流入。"""
    raw = _get(f"{base}{path}", {"symbol": cfg.SYMBOL, "interval": interval, "limit": limit})
    if not raw:
        return []
    bars = []
    for k in raw:
        quote_vol = float(k[7])
        taker_buy_quote = float(k[10])
        bars.append({
            "open_time": k[0] // 1000,
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "quote_vol": quote_vol,
            # taker 主动买入 - 主动卖出 = 2*买入 - 总额
            "netflow": 2 * taker_buy_quote - quote_vol,
        })
    return bars


def fetch_spot_5m(limit=1000):
    return fetch_klines(cfg.SPOT_BASE, "/api/v3/klines", "5m", limit)


def fetch_futures_5m(limit=1000):
    return fetch_klines(cfg.FAPI_BASE, "/fapi/v1/klines", "5m", limit)


def fetch_hourly_7d():
    """7 天 1h K 线，用于压力/支撑位的成交量分布。"""
    return fetch_klines(cfg.SPOT_BASE, "/api/v3/klines", "1h", 168)


def fetch_daily(limit=30):
    return fetch_klines(cfg.SPOT_BASE, "/api/v3/klines", "1d", limit)


def fetch_large_trades(from_id=None, max_pages=10):
    """从 from_id 起分页拉取 aggTrades，按【秒内同方向】聚合成订单后
    过滤大额成交（聚合额 >= LARGE_TRADE_USD）。大单在撮合中会被拆成许多笔
    aggTrade，逐笔过滤几乎筛不到，聚合后才能还原主力订单。
    BTC 高峰期成交量大，每轮最多追 max_pages 页；追不上时跳到最新，避免永久滞后。"""
    raw_all, last_id = [], from_id
    for _ in range(max_pages):
        params = {"symbol": cfg.SYMBOL, "limit": 1000}
        if last_id:
            params["fromId"] = last_id + 1
        raw = _get(f"{cfg.SPOT_BASE}/api/v3/aggTrades", params)
        if not raw:
            break
        raw_all.extend(raw)
        last_id = raw[-1]["a"]
        if len(raw) < 1000:   # 已追到最新
            break
    else:
        # 翻满 max_pages 仍未追平：跳到最新一页对齐游标
        tail = _get(f"{cfg.SPOT_BASE}/api/v3/aggTrades", {"symbol": cfg.SYMBOL, "limit": 1})
        if tail:
            last_id = tail[-1]["a"]

    # (秒, 方向) 聚合
    groups = {}
    for t in raw_all:
        sec = t["T"] // 1000
        is_buy = 0 if t["m"] else 1
        g = groups.setdefault((sec, is_buy), {"quote": 0.0, "qty": 0.0, "agg_id": 0})
        price = float(t["p"])
        qty = float(t["q"])
        g["quote"] += price * qty
        g["qty"] += qty
        g["agg_id"] = max(g["agg_id"], t["a"])

    trades = [{
        "agg_id": g["agg_id"],           # 组内最大 aggTradeId 作主键去重
        "ts": sec,
        "price": g["quote"] / g["qty"],
        "qty": g["qty"],
        "quote": g["quote"],
        "is_buy": is_buy,
    } for (sec, is_buy), g in groups.items() if g["quote"] >= cfg.LARGE_TRADE_USD]
    return trades, last_id


def fetch_top_trader_ratio():
    """币安合约大户持仓多空比（真实的“主力”仓位数据）。"""
    raw = _get(f"{cfg.FAPI_BASE}/futures/data/topLongShortPositionRatio",
               {"symbol": cfg.SYMBOL, "period": "5m", "limit": 48})
    return [{"ts": int(x["timestamp"]) // 1000, "ratio": float(x["longShortRatio"])}
            for x in raw] if raw else []


def fetch_global_account_ratio():
    """全市场多空人数比，用于情绪。"""
    raw = _get(f"{cfg.FAPI_BASE}/futures/data/globalLongShortAccountRatio",
               {"symbol": cfg.SYMBOL, "period": "5m", "limit": 1})
    if not raw:
        return None
    x = raw[-1]
    return {"long": float(x["longAccount"]), "short": float(x["shortAccount"])}


def fetch_funding():
    raw = _get(f"{cfg.FAPI_BASE}/fapi/v1/premiumIndex", {"symbol": cfg.SYMBOL})
    if not raw:
        return None
    return {"funding_rate": float(raw["lastFundingRate"]),
            "mark_price": float(raw["markPrice"])}


def fetch_open_interest_hist():
    raw = _get(f"{cfg.FAPI_BASE}/futures/data/openInterestHist",
               {"symbol": cfg.SYMBOL, "period": "1h", "limit": 25})
    return [{"ts": int(x["timestamp"]) // 1000,
             "oi_value": float(x["sumOpenInterestValue"])} for x in raw] if raw else []


_fng_cache = {"ts": 0, "value": None}


def fetch_fear_greed():
    """恐惧贪婪指数（30 分钟缓存，与 ValueScan 情绪更新频率一致）。"""
    if time.time() - _fng_cache["ts"] < 1800 and _fng_cache["value"] is not None:
        return _fng_cache["value"]
    raw = _get(cfg.FNG_URL)
    if raw and raw.get("data"):
        _fng_cache["value"] = {
            "value": int(raw["data"][0]["value"]),
            "label": raw["data"][0]["value_classification"],
        }
        _fng_cache["ts"] = time.time()
    return _fng_cache["value"]
