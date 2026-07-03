"""
指标计算，对应 ValueScan 的指标模块：
  - 主力行为指标 priceMarketType（1 上涨 / 2 下跌）
  - 主力成本与价格偏离度
  - 压力/支撑位（成交量分布密集区）
  - 情绪（bullish/neutral/bearish 比例）
"""
import statistics


def _sum_netflow(bars, n_bars):
    return sum(b["netflow"] for b in bars[-n_bars:]) if bars else 0.0


def netflow_windows(bars):
    """5m K 线的 1h/4h/24h 净流入。"""
    return {
        "1h": _sum_netflow(bars, 12),
        "4h": _sum_netflow(bars, 48),
        "24h": _sum_netflow(bars, 288),
    }


def rolling_hourly_netflows(bars):
    """不重叠的逐小时净流入序列（用于 z-score 与 Alpha 判断）。"""
    out = []
    closed = bars[:-1]  # 最后一根未收盘
    for i in range(len(closed) - 12, -1, -12):
        out.append(sum(b["netflow"] for b in closed[i:i + 12]))
    out.reverse()
    return out


def zscore(series, value):
    if len(series) < 8:
        return 0.0
    mean = statistics.fmean(series)
    stdev = statistics.pstdev(series)
    return (value - mean) / stdev if stdev > 0 else 0.0


def volume_stats_5m(bars):
    """最近一根已收盘 5m 的量能倍数与涨跌幅（FOMO 判断用）。"""
    if len(bars) < 60:
        return None
    last = bars[-2]  # 已收盘
    history = [b["quote_vol"] for b in bars[-290:-2]]
    avg = statistics.fmean(history) if history else 0
    prev_close = bars[-3]["close"]
    return {
        "vol_mult": last["quote_vol"] / avg if avg else 0,
        "price_change_5m": (last["close"] - prev_close) / prev_close * 100,
        "quote_vol": last["quote_vol"],
        "close": last["close"],
    }


def price_change(bars, n_bars):
    if len(bars) <= n_bars:
        return 0.0
    ref = bars[-1 - n_bars]["close"]
    return (bars[-1]["close"] - ref) / ref * 100


def dense_areas(hourly_bars, price, bins=60, top_n=3):
    """压力/支撑位：7 天 1h 成交额按价格分桶，取现价上下的高量密集区。
    对应 ValueScan denseArea：1=压力位，2=支撑位。"""
    if not hourly_bars:
        return []
    lo = min(b["low"] for b in hourly_bars)
    hi = max(b["high"] for b in hourly_bars)
    if hi <= lo:
        return []
    step = (hi - lo) / bins
    buckets = [0.0] * bins
    for b in hourly_bars:
        mid = (b["high"] + b["low"]) / 2
        idx = min(int((mid - lo) / step), bins - 1)
        buckets[idx] += b["quote_vol"]
    levels = [{"price": lo + (i + 0.5) * step, "weight": w}
              for i, w in enumerate(buckets) if w > 0]
    resist = sorted([l for l in levels if l["price"] > price],
                    key=lambda x: -x["weight"])[:top_n]
    support = sorted([l for l in levels if l["price"] < price],
                     key=lambda x: -x["weight"])[:top_n]
    out = [{"price": round(l["price"], 1), "dense_area": 1} for l in resist]
    out += [{"price": round(l["price"], 1), "dense_area": 2} for l in support]
    return sorted(out, key=lambda x: -x["price"])


def sentiment(fng, account_ratio):
    """情绪比例：恐惧贪婪指数 + 合约多空人数比 各占一半，留 20% 中性。
    （ValueScan 用社媒情感分析，此处以公开市场情绪数据替代。）"""
    if fng is None and account_ratio is None:
        return None
    parts = []
    if fng:
        parts.append(fng["value"] / 100)
    if account_ratio:
        parts.append(account_ratio["long"])
    bull_raw = sum(parts) / len(parts)
    return {
        "bullish": round(bull_raw * 0.8, 4),
        "bearish": round((1 - bull_raw) * 0.8, 4),
        "neutral": 0.2,
    }


def price_market_type(snapshot):
    """主力行为指标：六个多空分量投票，>=4 票看多 → 1（上涨），否则 2（下跌）。
    分量与 ValueScan 的数据维度对应：现货/合约资金流、大单净流入、
    大户持仓比变化、价格相对主力成本、价格动量。"""
    votes = {}
    nf_spot = snapshot["spot_netflow"]
    nf_fut = snapshot["futures_netflow"]
    whale = snapshot["whale"]
    votes["spot_flow"] = nf_spot["4h"] > 0
    votes["futures_flow"] = nf_fut["4h"] > 0
    votes["whale_flow"] = whale["netflow_24h"] > 0
    ratios = snapshot.get("top_trader_ratios") or []
    votes["top_trader"] = len(ratios) >= 2 and ratios[-1]["ratio"] >= ratios[0]["ratio"]
    votes["above_cost"] = bool(whale["cost"]) and snapshot["price"] > whale["cost"]
    votes["momentum"] = snapshot["price_change_4h"] > 0
    score = sum(votes.values())
    return {"type": 1 if score >= 4 else 2, "score": score, "votes": votes}
