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


def kv_get(key, default=None):
    with _lock:
        row = _conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_set(key, value):
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))
        _conn.commit()
