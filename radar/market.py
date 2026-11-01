import requests
import time
import json
import os
import sqlite3

CACHE_FILE = "/home/ubuntu/bot/market_cache.json"
DB_PATH = "/home/ubuntu/bot/jett_signals.db"

def get_db_fallback_stocks():
    stocks = []
    if not os.path.exists(DB_PATH):
        return stocks
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT ticker, current_price, entry_price, status
            FROM ai_signals_history
            ORDER BY id DESC
        """).fetchall()
        seen = set()
        for t, cp, ep, st in rows:
            if t and t not in seen:
                seen.add(t)
                change = 0.0
                if ep and cp and ep > 0:
                    change = round(((cp - ep) / ep) * 100, 2)
                stocks.append({
                    "ticker": t,
                    "close_price": cp or ep or 0,
                    "gross_trade_amount": 0.0,
                    "trade_amount": 0.0,
                    "volume_today": 0,
                    "volume": 0,
                    "growth_prcnt_today": change,
                    "percent_change": change,
                    "percentage": change,
                    "is_active": False,
                    "market_type": "stock"
                })
        conn.close()
    except Exception as e:
        print(f"[FALLBACK_DB_ERROR] {e}")
    return stocks

def fetch_market_data(token=None):
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                c = json.load(f)
                if time.time() - c.get('timestamp', 0) < 300 and c.get('active_all'):
                    return c
        except Exception:
            pass

    clean_token = str(token or "").replace("Bearer ", "").strip()
    token_file = "/home/ubuntu/bot/live_token.txt"
    if not clean_token and os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as tf:
                clean_token = tf.read().strip()
        except Exception:
            pass

    headers = {
        'accept': 'application/json, text/plain, */*',
        'origin': 'https://app.jett.uz',
        'referer': 'https://app.jett.uz/',
        'token': clean_token or '',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    raw_items = []
    try:
        resp = requests.get(
            'https://api.jett.uz/stockv3',
            headers=headers,
            params={'offset': 0, 'limit': 1000, 'sort_by': 'gross_trade_amount', 'order_by': 'desc'},
            timeout=8
        )
        if resp.status_code in (401, 403) or 'TOKEN_EXPIRED' in resp.text:
            print("[MARKET] ⚠️ Token muddati tugagan (401/403). Avtomatik yangilanmoqda...")
            fresh_tok = get_fresh_token()
            if fresh_tok:
                headers['token'] = fresh_tok
                resp = requests.get(
                    'https://api.jett.uz/stockv3',
                    headers=headers,
                    params={'offset': 0, 'limit': 1000, 'sort_by': 'gross_trade_amount', 'order_by': 'desc'},
                    timeout=12
                )
        if resp.status_code == 200:
            js = resp.json()
            # Jett yangi formati: {"stocks": [...], "stats": {...}}
            if isinstance(js, dict):
                raw_items = js.get('stocks', []) or js.get('data', [])
                global_stats = js.get('stats', {})
            elif isinstance(js, list):
                raw_items = js
                global_stats = {}
    except Exception as e:
        print(f"[MARKET_API_ERROR] {e}")

    if not raw_items:
        # Agar API dan ma'lumot kelmasa, oxirgi keshni ishlatamiz (agar mavjud bo'lsa)
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as cf:
                    cached_data = json.load(cf)
                    if cached_data.get('active_all'):
                        return cached_data
            except Exception:
                pass
        raw_items = get_db_fallback_stocks()

    active_stocks = []
    active_bonds = []
    try:
        import sqlite3 as _sq
        _c = _sq.connect("file:/home/ubuntu/jett_data/jett_radar.db?mode=ro", uri=True, timeout=10)
        _BOND_SET = {r[0].upper() for r in _c.execute("SELECT ticker FROM instruments WHERE market_code='BND'") if r[0]}
        _c.close()
        _BOND_OK = len(_BOND_SET) > 0
    except Exception:
        _BOND_SET, _BOND_OK = set(), False

    for item in raw_items:
        ticker = item.get("ticker", "")
        is_bond = (ticker.upper() in _BOND_SET) if _BOND_OK else (item.get("market_type") == "bond" or len(ticker) > 5 or ticker.endswith("B"))

        entry = {
            "ticker": ticker,
            "close_price": float(item.get("close_price") or item.get("value") or item.get("price") or 0),
            "gross_trade_amount": float(item.get("gross_trade_amount") or item.get("trade_amount") or 0),
            "trade_amount": float(item.get("gross_trade_amount") or item.get("trade_amount") or 0),
            "volume_today": int(float(item.get("volume_today") or item.get("volume") or 0)),
            "volume": int(float(item.get("volume_today") or item.get("volume") or 0)),
            "growth_prcnt_today": float(item.get("growth_prcnt_today") or item.get("percent_change") or item.get("percentage") or 0.0),
            "percent_change": float(item.get("growth_prcnt_today") or item.get("percent_change") or item.get("percentage") or 0.0),
            "is_active": False
        }

        if is_bond:
            active_bonds.append(entry)
        else:
            active_stocks.append(entry)

    # --- BAZA BILAN BOYITISH (Jett API da yo'q UZUMS3B2 kabi aktivlarni qo'shish) ---
    try:
        import sqlite3 as _sq
        _c = _sq.connect("file:/home/ubuntu/jett_data/jett_radar.db?mode=ro", uri=True, timeout=5)
        _target_d = _c.execute("SELECT MAX(substr(candle_time, 1, 10)) FROM ohlcv WHERE volume > 0").fetchone()
        if _target_d and _target_d[0]:
            _t_date = _target_d[0]
            _cur_tks = {x.get("ticker", "").upper() for x in (active_stocks + active_bonds)}
            _db_rows = _c.execute('''
                SELECT i.ticker, COALESCE(i.market_code, '?'), o.close, o.turnover, o.volume, o.open
                FROM ohlcv o
                JOIN instruments i ON i.stock_id = o.stock_id
                WHERE substr(o.candle_time, 1, 10) = ? AND o.volume > 0
            ''', (_t_date,)).fetchall()
            for _tk, _mk, _c_px, _turnover, _vol, _o_px in _db_rows:
                _tk_u = str(_tk).upper()
                if _tk_u not in _cur_tks:
                    _chg = round(((_c_px - _o_px) / _o_px) * 100, 2) if (_o_px and _c_px and _o_px > 0) else 0.0
                    _ent = {
                        "ticker": _tk_u,
                        "close_price": float(_c_px or 0),
                        "gross_trade_amount": float(_turnover or 0),
                        "trade_amount": float(_turnover or 0),
                        "volume_today": int(_vol or 0),
                        "volume": int(_vol or 0),
                        "growth_prcnt_today": _chg,
                        "percent_change": _chg,
                        "is_active": True
                    }
                    if _mk == "BND":
                        active_bonds.append(_ent)
                    else:
                        active_stocks.append(_ent)
                    _cur_tks.add(_tk_u)
        _c.close()
    except Exception as _e:
        print(f"[DB_ENRICH_ERR] {_e}")

    active_all = active_stocks + active_bonds

    # Obshiy oborot: API va bazadagi barcha faol aktivlar aylanmasining to'liq yig'indisi
    calc_turnover = sum(float(x.get('gross_trade_amount') or x.get('trade_amount') or 0.0) for x in active_all)
    total_turnover_val = 0.0
    if 'global_stats' in locals() and isinstance(global_stats, dict):
        total_turnover_val = float(global_stats.get('total_turnover') or global_stats.get('turnover') or 0.0)
    total_turnover_val = max(total_turnover_val, calc_turnover)

    # Eslatma: API narxi/hajmini kunlik OHLCV bilan aynan
    # tengligiga qarab bozorni eskirgan deb belgilash ishonchli emas.
    # Haqiqiy savdo sessiyasi va ma'lumot yangiligi alohida tekshirilishi kerak.
    is_market_stale = False

    result = {
        "status": "ok",
        "active_stocks": active_stocks,
        "active_bonds": active_bonds,
        "active_all": active_all,
        "total_stocks": len(active_stocks),
        "total_bonds": len(active_bonds),
        "total_turnover": total_turnover_val,
        "is_stale": is_market_stale
    }

    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            save_payload = dict(result)
            save_payload['timestamp'] = time.time()
            json.dump(save_payload, f, ensure_ascii=False)
    except Exception:
        pass

    return result


def get_fresh_token():
    try:
        import get_token
        return get_token.get_live_token()
    except Exception as e:
        print(f"[MARKET_TOKEN_ERR] {e}")
        return None

fetch_fresh_token = get_fresh_token
