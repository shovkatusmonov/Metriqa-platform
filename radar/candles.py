# -*- coding: utf-8 -*-
"""
candles.py - kunlik shamlarni jett_radar.db dan FAQAT O'QISH rejimida olish.
tp_sl_engine.compute_quant_levels(candles=...) uchun kirish tayyorlaydi.
"""
import os
import sqlite3

DB_PATHS = [
    "/home/ubuntu/jett_data/jett_radar.db",
    "/home/ubuntu/bot/jett_radar.db",
    "jett_radar.db",
]


def fetch_candles_by_stock_id(cur, stock_id, n=120):
    """stock_id bo'yicha oxirgi n ta qator (eskidan yangiga)."""
    rows = cur.execute(
        "SELECT candle_time, open, high, low, close, volume FROM ohlcv "
        "WHERE stock_id = ? ORDER BY candle_time DESC LIMIT ?",
        (stock_id, int(n)),
    ).fetchall()
    keys = ("candle_time", "open", "high", "low", "close", "volume")
    out = [dict(zip(keys, tuple(r))) for r in rows]
    out.reverse()  # eskidan yangiga
    return out


def fetch_candles_from_cursor(cur, ticker, n=120):
    """Ticker bo'yicha (bir nechta bo'lsa, faol instrument tanlanadi)."""
    row = cur.execute(
        "SELECT stock_id FROM instruments WHERE ticker = ? COLLATE NOCASE "
        "ORDER BY active DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return fetch_candles_by_stock_id(cur, row[0], n) if row else []


def fetch_candles(ticker, n=120, db_path=None):
    """Mustaqil chaqiruv: bazani faqat o'qish rejimida ochadi."""
    paths = [db_path] if db_path else DB_PATHS
    for p in paths:
        if p and os.path.exists(p):
            conn = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=10)
            try:
                return fetch_candles_from_cursor(conn.cursor(), ticker, n)
            finally:
                conn.close()
    return []
