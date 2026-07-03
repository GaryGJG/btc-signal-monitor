"""SQLite 持久化：大额交易（主力成本用）、信号历史、KV 状态。"""
import json
import sqlite3
import threading
import time

import settings as cfg

_lock = threading.Lock()
_conn = None


def init():
    global _conn
    _conn = sqlite3.connect(cfg.DB_FILE, check_same_thread=False)
    _conn.execute("""CREATE TABLE IF NOT EXISTS large_trades (
        agg_id INTEGER PRIMARY KEY,
        ts INTEGER, price REAL, qty REAL, quote REAL, is_buy INTEGER)""")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_ts ON large_trades(ts)")
    _conn.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, code TEXT, name TEXT, direction TEXT,
        title TEXT, content TEXT)""")
    _conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
    _conn.execute("""CREATE TABLE IF NOT EXISTS trend_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trend INTEGER, start_ts INTEGER, start_price REAL,
        high REAL, low REAL, end_ts INTEGER, end_price REAL)""")
    _conn.execute("""CREATE TABLE IF NOT EXISTS trend_hourly (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id INTEGER, ts INTEGER, price REAL, change_pct REAL)""")
    _conn.commit()


def save_large_trades(trades):
    if not trades:
        return 0
    with _lock:
        cur = _conn.executemany(
            "INSERT OR IGNORE INTO large_trades VALUES (:agg_id,:ts,:price,:qty,:quote,:is_buy)",
            trades)
        _conn.commit()
        return cur.rowcount


def whale_stats(window_days=None):
    """大额交易加权成本（对应 ValueScan 主力成本）与各窗口净流入。"""
    window_days = window_days or cfg.WHALE_COST_WINDOW_DAYS
    now = int(time.time())
    with _lock:
        row = _conn.execute(
            "SELECT SUM(price*qty)/SUM(qty), COUNT(*) FROM large_trades WHERE ts>=?",
            (now - window_days * 86400,)).fetchone()
        cost, count = row[0], row[1]

        def netflow(seconds):
            r = _conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN is_buy=1 THEN quote ELSE -quote END),0) "
                "FROM large_trades WHERE ts>=?", (now - seconds,)).fetchone()
            return r[0]

        # 清理超窗口 2 倍的旧数据
        _conn.execute("DELETE FROM large_trades WHERE ts<?",
                      (now - window_days * 2 * 86400,))
        _conn.commit()
    return {
        "cost": cost, "trade_count": count,
        "netflow_1h": netflow(3600),
        "netflow_4h": netflow(14400),
        "netflow_24h": netflow(86400),
    }


def save_signal(sig):
    with _lock:
        _conn.execute(
            "INSERT INTO signals (ts,code,name,direction,title,content) VALUES (?,?,?,?,?,?)",
            (sig["ts"], sig["code"], sig["name"], sig["direction"],
             sig["title"], sig["content"]))
        _conn.commit()


def recent_signals(limit=50):
    with _lock:
        rows = _conn.execute(
            "SELECT ts,code,name,direction,title,content FROM signals "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ts": r[0], "code": r[1], "name": r[2], "direction": r[3],
             "title": r[4], "content": r[5]} for r in rows]


# ---------- 多空指标有效性统计 ----------

def track_trend(trend, price, ts=None):
    """每轮调用：指标翻转时结算旧周期并开新周期；周期内维护高低点，
    并自翻转起每小时快照一次价格变化幅度。"""
    ts = ts or int(time.time())
    with _lock:
        active = _conn.execute(
            "SELECT id, trend, start_ts, start_price, high, low FROM trend_periods "
            "WHERE end_ts IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        if active is None:
            _conn.execute(
                "INSERT INTO trend_periods (trend,start_ts,start_price,high,low) "
                "VALUES (?,?,?,?,?)", (trend, ts, price, price, price))
            _conn.commit()
            return
        pid, cur_trend, start_ts, start_price, high, low = active
        if cur_trend != trend:
            _conn.execute("UPDATE trend_periods SET end_ts=?, end_price=? WHERE id=?",
                          (ts, price, pid))
            _conn.execute(
                "INSERT INTO trend_periods (trend,start_ts,start_price,high,low) "
                "VALUES (?,?,?,?,?)", (trend, ts, price, price, price))
            _conn.commit()
            return
        if price > high or price < low:
            _conn.execute("UPDATE trend_periods SET high=MAX(high,?), low=MIN(low,?) WHERE id=?",
                          (price, price, pid))
        last = _conn.execute(
            "SELECT COALESCE(MAX(ts), ?) FROM trend_hourly WHERE period_id=?",
            (start_ts, pid)).fetchone()[0]
        if ts - last >= 3600:
            _conn.execute(
                "INSERT INTO trend_hourly (period_id,ts,price,change_pct) VALUES (?,?,?,?)",
                (pid, ts, price, (price - start_price) / start_price * 100))
        _conn.commit()


def _period_dict(row, price_now=None):
    pid, trend, start_ts, start_price, high, low, end_ts, end_price = row
    ref = end_price if end_price is not None else price_now
    sign = 1 if trend == 1 else -1
    d = {
        "trend": trend, "start_ts": start_ts, "start_price": start_price,
        "end_ts": end_ts,
        "duration_h": ((end_ts or int(time.time())) - start_ts) / 3600,
        "change_pct": (ref - start_price) / start_price * 100 if ref else None,
        # 顺方向最大有利/最大不利波动
        "max_fav": ((high if trend == 1 else low) - start_price) / start_price * 100 * sign,
        "max_adv": ((low if trend == 1 else high) - start_price) / start_price * 100 * sign,
    }
    if d["change_pct"] is not None:
        d["hit"] = d["change_pct"] * sign > 0
    return d


def trend_stats(price_now=None, history_n=10):
    """当前周期 + 每小时快照 + 历史周期与命中率汇总，供仪表盘展示。"""
    with _lock:
        cur = _conn.execute(
            "SELECT id,trend,start_ts,start_price,high,low,end_ts,end_price "
            "FROM trend_periods WHERE end_ts IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        hourly = []
        if cur:
            hourly = _conn.execute(
                "SELECT ts, change_pct FROM trend_hourly WHERE period_id=? "
                "ORDER BY ts DESC LIMIT 48", (cur[0],)).fetchall()
        closed = _conn.execute(
            "SELECT id,trend,start_ts,start_price,high,low,end_ts,end_price "
            "FROM trend_periods WHERE end_ts IS NOT NULL ORDER BY id DESC LIMIT ?",
            (history_n,)).fetchall()
        all_closed = _conn.execute(
            "SELECT trend, start_price, end_price FROM trend_periods "
            "WHERE end_ts IS NOT NULL").fetchall()

    hits, dir_changes = [], []
    for trend, sp, ep in all_closed:
        sign = 1 if trend == 1 else -1
        chg = (ep - sp) / sp * 100 * sign
        hits.append(chg > 0)
        dir_changes.append(chg)
    return {
        "current": _period_dict(cur, price_now) if cur else None,
        "hourly": [{"ts": t, "change_pct": c} for t, c in reversed(hourly)],
        "history": [_period_dict(r) for r in closed],
        "summary": {
            "periods": len(all_closed),
            "hit_rate": sum(hits) / len(hits) * 100 if hits else None,
            "avg_dir_change": sum(dir_changes) / len(dir_changes) if dir_changes else None,
        },
    }


def kv_get(key, default=None):
    with _lock:
        row = _conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_set(key, value):
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))
        _conn.commit()
