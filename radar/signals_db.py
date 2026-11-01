
def extract_float_price(val):
    """Har qanday formatdagi narxni (dict, str, int, float) toza float songa o'tkazish."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        return float(val.get('price') or val.get('close') or val.get('last_price') or val.get('current_price') or 0.0)
    try:
        return float(str(val).replace(' ', '').replace(',', '.'))
    except Exception:
        return 0.0

import tp_sl_engine as eng
import candles as cd
import os
import sqlite3
import json
from datetime import datetime

DB_PATH = "/home/ubuntu/bot/jett_signals.db"

def is_market_open_now():
    import datetime
    tz_uzb = datetime.timezone(datetime.timedelta(hours=5))
    now = datetime.datetime.now(tz_uzb)
    if now.weekday() > 4:
        return False
    market_open = now.replace(hour=10, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=5, second=0, microsecond=0)
    return market_open <= now <= market_close

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def parse_price(val):
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = str(val).replace(" ", "").replace(",", "").replace("UZS", "").replace("uzs", "").strip()
        return float(cleaned)
    except Exception:
        return 0.0

def init_signals_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_signals_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_date TEXT,
                ticker TEXT,
                category TEXT,
                entry_price REAL,
                target_price REAL,
                target_price_2 REAL,
                stop_loss REAL,
                current_price REAL,
                current_max_pnl REAL,
                status TEXT DEFAULT 'ACTIVE',
                closed_date TEXT,
                closed_pnl REAL,
                entry_history TEXT,
                signal_count INTEGER DEFAULT 1,
                tp1_pct REAL DEFAULT 10.0,
                tp2_pct REAL DEFAULT 20.0,
                tp1_alerted INTEGER DEFAULT 0,
                tp2_alerted INTEGER DEFAULT 0
            )
        """)
        columns_to_add = [
            ("target_price_2", "REAL DEFAULT 0"),
            ("tp1_pct", "REAL DEFAULT 10.0"),
            ("tp2_pct", "REAL DEFAULT 20.0"),
            ("tp1_alerted", "INTEGER DEFAULT 0"),
            ("tp2_alerted", "INTEGER DEFAULT 0"),
            ("q_status", "TEXT"),
            ("q_reason", "TEXT"),
            ("q_sl", "REAL"),
            ("q_tp1", "REAL"),
            ("q_tp2", "REAL"),
            ("q_rr", "REAL"),
            ("q_version", "TEXT")
        ]
        for col_name, col_type in columns_to_add:
            try:
                conn.execute(f"ALTER TABLE ai_signals_history ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass
        conn.commit()

LIQ_MIN_TURNOVER_UZS = 0.0  # filtrni o'chirish uchun -1.0 qiling

# === SGATE_PATCH_20261030 ===
import os as _sg_os, re as _sg_re, json as _sg_json, time as _sg_time
import sqlite3 as _sg_sqlite, statistics as _sg_stat, datetime as _sg_dt

SG_COOLDOWN_SESSIONS = 5       # TP2/SL dan keyin necha savdo kuni yangi signal berilmaydi
SG_SPIKE_MAX_PCT = 7.0         # kirish oldingi close dan shuncha % dan ko'p yuqori bo'lsa rad
SG_VOL_LOOKBACK = 10           # tebranishni hisoblash uchun sessiyalar soni
SG_VOL_MAX_RATIO = 0.8         # median(high-low)/close > SL% * shu qiymat bo'lsa rad
SG_TRACKER_MAX_AGE_SEC = 20 * 60
SG_CANDLE_DB = "/home/ubuntu/jett_data/jett_radar.db"
SG_HEARTBEAT = _sg_os.path.join(_sg_os.path.dirname(_sg_os.path.abspath(__file__)), "tracker_heartbeat.json")


def _sg_day_of(s):
    if s is None:
        return None
    s = str(s).strip()
    m = _sg_re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(1) + "-" + m.group(2) + "-" + m.group(3)
    m = _sg_re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return m.group(3) + "-" + m.group(2) + "-" + m.group(1)
    if _sg_re.match(r"^\d{10,13}(\.\d+)?$", s):
        v = float(s)
        if v > 1e12:
            v /= 1000.0
        return (_sg_dt.datetime.utcfromtimestamp(v) + _sg_dt.timedelta(hours=5)).date().isoformat()
    return None


def _sg_fmt(x):
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def _sg_candles(ticker, today):
    """Bugundan oldingi, hajmi > 0 bo'lgan kunlik shamchalar: [(sana, high, low, close), ...]"""
    c = _sg_sqlite.connect("file:" + SG_CANDLE_DB + "?mode=ro", uri=True, timeout=5)
    try:
        sid = c.execute("SELECT stock_id FROM instruments WHERE UPPER(ticker)=? LIMIT 1", (ticker,)).fetchone()
        if not sid:
            return []
        rows = c.execute(
            "SELECT format_time, candle_time, high, low, close, volume FROM ohlcv "
            "WHERE stock_id=? ORDER BY candle_time DESC LIMIT 40", (sid[0],)).fetchall()
    finally:
        c.close()
    out = {}
    for r in rows:
        d = _sg_day_of(r[0]) or _sg_day_of(r[1])
        if not d or d >= today:
            continue
        try:
            h, l, cl, vol = float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0 or cl <= 0:
            continue
        out[d] = (h, l, cl)
    return [(d,) + out[d] for d in sorted(out)]


def _sg_entry_checks(ticker, entry, sl_pct):
    """None = o'tdi, aks holda rad etish sababi (matn). Xato bo'lsa bloklamaydi."""
    try:
        today = _sg_dt.date.today().isoformat()
        cs = _sg_candles(ticker, today)
        if not cs:
            return None
        prev_close = cs[-1][3]
        up = (entry / prev_close - 1.0) * 100.0
        if up > SG_SPIKE_MAX_PCT:
            return (f"SPIKE: kirish {_sg_fmt(entry)} oldingi close {_sg_fmt(prev_close)} dan "
                    f"+{up:.1f}% yuqori (chegara {SG_SPIKE_MAX_PCT:.0f}%)")
        last = cs[-SG_VOL_LOOKBACK:]
        if len(last) >= 5:
            med = _sg_stat.median([(h - l) / c * 100.0 for _, h, l, c in last])
            lim = float(sl_pct) * SG_VOL_MAX_RATIO
            if med > lim:
                return (f"TEBRANISH: oxirgi {len(last)} sessiya o'rtacha kunlik diapazon {med:.1f}% "
                        f"> SL {float(sl_pct):.0f}% ning {SG_VOL_MAX_RATIO * 100:.0f}% i ({lim:.1f}%)")
    except Exception as _e:
        print(f"[SGATE_ERR] {ticker}: {_e}", flush=True)
    return None


def _sg_in_cooldown(conn, ticker):
    try:
        row = conn.execute(
            "SELECT closed_date, status FROM ai_signals_history "
            "WHERE ticker = ? AND status IN ('TARGET_HIT', 'STOPPED') AND closed_date IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (ticker,)).fetchone()
        if not row or not row[0]:
            return False
        cd = _sg_day_of(row[0])
        if not cd:
            return False
        d = _sg_dt.date.fromisoformat(cd)
        today = _sg_dt.date.today()
        n = 0
        while d < today:
            d += _sg_dt.timedelta(days=1)
            if d.weekday() < 5:
                n += 1
        if n < SG_COOLDOWN_SESSIONS:
            print(f"[COOLDOWN] {ticker}: {row[1]} {cd} da yopilgan, {n}/{SG_COOLDOWN_SESSIONS} sessiya o'tdi, "
                  f"yangi signal ochilmadi", flush=True)
            return True
    except Exception as _e:
        print(f"[COOLDOWN_ERR] {ticker}: {_e}", flush=True)
    return False


def _sg_ensure_schema():
    need = {"tp1_alerted": "INTEGER DEFAULT 0", "tp2_alerted": "INTEGER DEFAULT 0",
            "sl_alerted": "INTEGER DEFAULT 0"}
    with get_conn() as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(ai_signals_history)")}
        for k, t in need.items():
            if k not in have:
                conn.execute(f"ALTER TABLE ai_signals_history ADD COLUMN {k} {t}")
                print(f"[SCHEMA] ai_signals_history ga {k} ustuni qo'shildi", flush=True)
        try:
            conn.commit()
        except Exception:
            pass


def _sg_hb_write(ok, err=None):
    try:
        try:
            with open(SG_HEARTBEAT, "r", encoding="utf-8") as f:
                data = _sg_json.load(f)
        except Exception:
            data = {}
        now = _sg_time.time()
        if ok:
            data["last_ok"] = now
            data["last_ok_iso"] = _sg_dt.datetime.now().isoformat(timespec="seconds")
        else:
            data["last_err"] = now
            data["last_err_msg"] = str(err)[:300]
        tmp = SG_HEARTBEAT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _sg_json.dump(data, f)
        _sg_os.replace(tmp, SG_HEARTBEAT)
    except Exception:
        pass


# === SGATE2_PATCH_20261030 ===
SG_LATE_MIN_MINUTES = 30       # tracker savdo vaqtida shuncha daqiqadan ko'p uzilgan bo'lsa xabar "kechikkan" deb belgilanadi


def _sg_hb_read():
    try:
        with open(SG_HEARTBEAT, "r", encoding="utf-8") as f:
            return _sg_json.load(f)
    except Exception:
        return {}


def _sg_hb_loop(state):
    """bot.py signals_tracker_worker har aylanishda chaqiradi: oqim tirik. state: ok / skip / err"""
    try:
        data = _sg_hb_read()
        data["last_loop"] = _sg_time.time()
        data["last_state"] = str(state)
        tmp = SG_HEARTBEAT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _sg_json.dump(data, f)
        _sg_os.replace(tmp, SG_HEARTBEAT)
    except Exception:
        pass


def _sg_trading_minutes(t0, t1):
    """Ikki epoch oralig'idagi savdo daqiqalari (Toshkent, Du-Ju 09:30-18:30)."""
    if t1 <= t0:
        return 0.0
    if t1 - t0 > 60 * 86400:
        return 99999.0
    tz5 = _sg_dt.timedelta(hours=5)
    a = _sg_dt.datetime.utcfromtimestamp(t0) + tz5
    b = _sg_dt.datetime.utcfromtimestamp(t1) + tz5
    total = 0.0
    day = a.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= b:
        if day.weekday() < 5:
            lo = max(a, day.replace(hour=9, minute=30))
            hi = min(b, day.replace(hour=18, minute=30))
            if hi > lo:
                total += (hi - lo).total_seconds() / 60.0
        day += _sg_dt.timedelta(days=1)
    return total


def _sg_tracker_fresh():
    """False = tracker ishlamay qolgan (yangi signal ochmaslik kerak). Heartbeat yo'q bo'lsa True."""
    try:
        hb = _sg_hb_read()
        last = hb.get("last_ok")
        if not last:
            return True
        now = _sg_time.time()
        if now - float(last) <= SG_TRACKER_MAX_AGE_SEC:
            return True
        loop = float(hb.get("last_loop") or 0)
        if str(hb.get("last_state", "")).startswith("skip") and now - loop <= SG_TRACKER_MAX_AGE_SEC:
            return True   # bozor ma'lumoti yangilanmagan (savdosiz kun): tracker ataylab o'tkazib yuborgan
        tk = _sg_dt.datetime.utcnow() + _sg_dt.timedelta(hours=5)
        in_window = tk.weekday() < 5 and (9 * 60 + 40) <= (tk.hour * 60 + tk.minute) <= (18 * 60 + 25)
        failing = float(hb.get("last_err") or 0) > float(last)
        return not (in_window or failing)
    except Exception:
        return True


def save_ai_signals(radar_data, price_map=None):
    from datetime import datetime
    try:
        import market as _mk
        if _mk.fetch_market_data(None).get("is_stale"):
            print("[SAVE_SIGNALS] Savdosiz kun (is_stale): signal ochilmadi", flush=True)
            return 0
    except Exception as _ge:
        print(f"[SAVE_SIGNALS_GUARD_ERR] {_ge}", flush=True)
    try:
        _sg_ensure_schema()
        if not _sg_tracker_fresh():
            print("[TRACKER_STALE] tracker 20 daqiqadan beri muvaffaqiyatli ishlamagan: yangi signal ochilmadi", flush=True)
            return 0
    except Exception as _sge:
        print(f"[SGATE_GUARD_ERR] {_sge}", flush=True)
    traded_map = {}
    if isinstance(price_map, list):
        pmap = {}
        for s in price_map:
            t = s.get("ticker") or s.get("symbol") or s.get("sec_code")
            p = s.get("close_price") or s.get("current_price") or s.get("price") or s.get("value") or s.get("last_price")
            if t and p is not None:
                parsed = extract_float_price(p)
                if parsed > 0:
                    pmap[str(t).upper()] = parsed
                    try:
                        _turn = float(s.get("gross_trade_amount", 0) or s.get("trade_amount", 0) or 0)
                        _vol = float(s.get("volume_today", 0) or s.get("volume", 0) or 0)
                    except (ValueError, TypeError):
                        _turn, _vol = 0.0, 0.0
                    traded_map[str(t).upper()] = _turn if _turn > 0 else (1.0 if _vol > 0 else 0.0)
        price_map = pmap
    elif not isinstance(price_map, dict):
        price_map = {}

    if not radar_data or not isinstance(radar_data, dict):
        return 0

    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    today_date = datetime.now().strftime("%Y-%m-%d")
    inserted_or_updated = 0

    with get_conn() as conn:
        for cat_name, items in radar_data.items():
            if not isinstance(items, list):
                continue

            for item in items:
                ticker = item.get("ticker")
                if not ticker:
                    continue
                # Agar ushbu aksiya hali TP olmagan bo'lsa, yangi signal qabul qilinmaydi (oldingisi qoladi)
                existing_active = conn.execute(
                    "SELECT id FROM ai_signals_history WHERE ticker = ? AND status IN ('ACTIVE', 'TARGET_1_HIT')",
                    (ticker,)
                ).fetchone()
                if existing_active:
                    continue
                ticker = str(ticker).strip().upper()
                # --- QAT'IY QOIDA: TP2 ga yetmagan aksiyaga yangi signal berilmaydi ---
                prev_sig = conn.execute('''
                    SELECT id, current_max_pnl, tp2_pct FROM ai_signals_history
                    WHERE ticker = ?
                      AND (
                          status IN ('ACTIVE', 'TARGET_1_HIT')
                          OR (status = 'TARGET_HIT' AND COALESCE(current_max_pnl, 0) < COALESCE(tp2_pct, 20.0))
                      )
                ''', (ticker,)).fetchone()
                if prev_sig:
                    continue
                if _sg_in_cooldown(conn, ticker):
                    continue


                try:
                    import quality as _qmod, sqlite3 as _sq3
                    _qgt = str(ticker).strip().upper()
                    _qgb = (len(_qgt) > 5 and any(ch.isdigit() for ch in _qgt)) or _qgt.endswith(("B1", "B2", "B3"))
                    _qgc = _sq3.connect("file:/home/ubuntu/jett_data/jett_radar.db?mode=ro", uri=True)
                    try:
                        _qgs = _qgc.execute("SELECT stock_id FROM instruments WHERE UPPER(ticker)=? LIMIT 1", (_qgt,)).fetchone()
                        _qga = _qmod.assess(_qgc, _qgs[0], _qgb) if _qgs else None
                    finally:
                        _qgc.close()
                    if _qga is None or _qga["verdict"] != "PASS":
                        _qgw = "; ".join(_qga["reasons"][:3]) if _qga else "instruments da yo'q"
                        print(f"[QGATE] {_qgt}: signal ochilmadi ({_qgw})", flush=True)
                        continue
                except Exception as _qge:
                    print(f"[QGATE_ERR] {ticker}: {_qge}", flush=True)
                if traded_map and not (traded_map.get(ticker, 1.0) > LIQ_MIN_TURNOVER_UZS):
                    print(f"[LIQ] {ticker}: bugun savdo yoq, signal ochilmadi")
                    continue
                mkt_val = price_map.get(ticker, 0.0) if price_map else 0.0
                real_mkt = extract_float_price(mkt_val)
                raw_val = item.get("entry_zone") or item.get("current_price") or item.get("price")
                raw_entry = extract_float_price(raw_val)

                # Real birja narxi mavjud bo'lsa, qat'iy ravishda u kirish narxi bo'ladi
                if real_mkt > 0:
                    entry = real_mkt
                else:
                    continue  # Bozor narxi yo'q bo'lsa xom narxda signal ochilmaydi

                if entry <= 0:
                    continue

                is_bond = (len(ticker) > 5 and any(ch.isdigit() for ch in ticker)) or str(cat_name).lower() == "safe_haven"
                default_tp1 = 5.0 if is_bond else 10.0
                default_tp2 = 10.0 if is_bond else 20.0
                default_sl_pct = 4.0 if is_bond else 10.0

                tp1_pct = float(item.get("tp1_pct") or default_tp1)
                tp2_pct = float(item.get("tp2_pct") or default_tp2)
                if tp2_pct <= tp1_pct:
                    tp2_pct = tp1_pct + default_tp1

                sl_pct_calc = float(item.get("stop_loss_pct") or default_sl_pct)
                if sl_pct_calc <= 0 or sl_pct_calc >= 50.0:
                    sl_pct_calc = default_sl_pct
                _sg_bad = _sg_entry_checks(ticker, entry, sl_pct_calc)
                if _sg_bad:
                    print(f"[SGATE] {ticker}: signal ochilmadi ({_sg_bad})", flush=True)
                    continue

                # 1.8: SL har doim haqiqiy kirish narxidan (entry) hisoblanadi
                sl_val = round(entry * (1.0 - sl_pct_calc / 100.0), 2)
                target_1 = round(entry * (1.0 + tp1_pct / 100.0), 4)
                target_2 = round(entry * (1.0 + tp2_pct / 100.0), 4)

                # Qat'iy invariant tekshiruvi: SL < entry < TP1 < TP2
                if not (0 < sl_val < entry < target_1 < target_2):
                    print(f"[SL_GUARD] {ticker}: noto'g'ri darajalar SL={sl_val}, E={entry}, TP1={target_1}, TP2={target_2} -> rad etildi", flush=True)
                    continue
                reason_txt = str(item.get("reason") or "AI Skaner")

                record = {
                    "signal_date": today_str,
                    "ticker": ticker,
                    "category": str(cat_name).capitalize(),
                    "entry_price": float(entry),
                    "target_price": float(target_1),
                    "stop_loss": float(sl_val),
                    "current_price": float(entry),
                    "current_max_pnl": 0.0,
                    "status": "ACTIVE",
                    "tp1_pct": tp1_pct,
                    "tp2_pct": tp2_pct,
                    "tp1_alerted": 0,
                    "tp2_alerted": 0,
                    "target_price_2": float(target_2),
                    "q_status": "ACTIVE",
                    "q_reason": reason_txt,
                    "q_sl": float(sl_val),
                    "q_tp1": float(target_1),
                    "q_tp2": float(target_2),
                    "q_rr": 2.0,
                    "q_version": "v2"
                }

                existing = conn.execute(
                    "SELECT id FROM ai_signals_history WHERE ticker = ? AND signal_date LIKE ?",
                    (ticker, f"{today_date}%")
                ).fetchone()

                if existing:
                    conn.execute(
                        "UPDATE ai_signals_history SET current_price = ?, q_reason = ? WHERE id = ?",
                        (float(entry), reason_txt, existing[0])
                    )
                else:
                    cols = ", ".join(record.keys())
                    placeholders = ", ".join(["?"] * len(record))
                    sql = f"INSERT INTO ai_signals_history ({cols}) VALUES ({placeholders})"
                    conn.execute(sql, tuple(record.values()))

                inserted_or_updated += 1
        conn.commit()

    return inserted_or_updated



def mark_signal_alerted(sig_id: int, flag_col: str):
    """Xabar kamida 1 ta qabul qiluvchiga ketgandan keyin bayroqni 1 qiladi."""
    allowed_flags = {"tp1_alerted", "tp2_alerted", "sl_alerted"}
    if flag_col not in allowed_flags:
        return
    with get_conn() as conn:
        if flag_col == "tp2_alerted":
            conn.execute("UPDATE ai_signals_history SET tp1_alerted = 1, tp2_alerted = 1 WHERE id = ?", (sig_id,))
        else:
            conn.execute(f"UPDATE ai_signals_history SET {flag_col} = 1 WHERE id = ?", (sig_id,))
        conn.commit()


def update_signals_tracker(current_stocks=None, force=False):
    price_map = {}
    if not current_stocks:
        try:
            import market, bot
            token = getattr(bot, "ADMIN_TOKEN", None)
            if not token:
                for p in ["/home/ubuntu/bot/live_token.txt", "/home/ubuntu/jett_data/live_token.txt"]:
                    if os.path.exists(p):
                        with open(p, "r", encoding="utf-8") as tf:
                            token = tf.read().strip()
                            if token:
                                break
            if token:
                mdata = market.fetch_market_data(token)
                if isinstance(mdata, dict):
                    current_stocks = (mdata.get("active_stocks", []) or []) + (mdata.get("active_bonds", []) or [])
        except Exception as e:
            print(f"[TRACKER_FETCH_ERR] {e}", flush=True)
            current_stocks = []

    if current_stocks and isinstance(current_stocks, list):
        for s in current_stocks:
            if not isinstance(s, dict):
                continue
            t = (s.get("ticker") or s.get("sec_code") or s.get("symbol") or "").upper()
            val = s.get("close_price") or s.get("value") or s.get("current_price") or s.get("last_price")
            if t and val is not None:
                try:
                    p = float(val)
                    vol = float(s.get("volume_today", 0) or s.get("volume", 0) or 0)
                    turnover = float(s.get("gross_trade_amount", 0) or s.get("trade_amount", 0) or 0)
                    if 0 < p < 1000000000:
                        price_map[t] = {"price": p, "volume": vol, "turnover": turnover}
                except (ValueError, TypeError):
                    continue

    if not price_map:
        return []

    import datetime as _dt
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    now_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    now_tashkent = now_utc.astimezone(_dt.timezone(_dt.timedelta(hours=5)))
    is_weekend = now_tashkent.weekday() in (5, 6)

    triggered_events = []
    checked_count = 0

    with get_conn() as conn:
        active_signals = conn.execute("""
            SELECT * FROM ai_signals_history 
            WHERE id IN (SELECT MAX(id) FROM ai_signals_history GROUP BY ticker)
              AND (
                  status IN ('ACTIVE', 'TARGET_1_HIT', 'RUNNER')
                  OR (status = 'TARGET_HIT' AND COALESCE(tp2_alerted, 0) = 0)
                  OR (status = 'STOPPED' AND COALESCE(sl_alerted, 0) = 0)
              )
        """).fetchall()

        for sig in active_signals:
            try:
                ticker = str(sig["ticker"]).upper()
                if ticker not in price_map:
                    continue

                raw_p = price_map[ticker]
                curr_p = float(raw_p["price"] if isinstance(raw_p, dict) else raw_p)
                _turn = float(raw_p.get("turnover", 0) or 0) if isinstance(raw_p, dict) else 0.0

                entry_p = float(sig["entry_price"] or 0.0)
                if entry_p <= 0 or curr_p <= 0:
                    continue

                # Narx himoyasi: oldingi narxdan 2 baravardan ko'p og'ish - ma'lumot xatosi
                _prev_p = float(sig["current_price"] or entry_p)
                if _prev_p > 0 and abs(curr_p - _prev_p) / _prev_p > 1.0:
                    print(f"[PRICE-GUARD] {ticker}: {_prev_p} -> {curr_p} rad etildi", flush=True)
                    continue

                current_pnl = round(((curr_p - entry_p) / entry_p) * 100.0, 2)
                max_pnl = float(sig["current_max_pnl"] or 0.0)
                new_max_pnl = max(max_pnl, current_pnl)

                # Joriy narxni har doim yangilash
                conn.execute("""
                    UPDATE ai_signals_history 
                    SET current_price = ?, current_max_pnl = ?
                    WHERE id = ?
                """, (curr_p, new_max_pnl, sig["id"]))

                # Dam olish kunlari yoki aylanma 5 mln dan kam bo'lganda (agar force=False bo'lsa) status o'zgarmaydi
                if not force:
                    if is_weekend:
                        continue
                    if 0 < _turn < 5000000.0:
                        continue

                checked_count += 1
                tp1 = float(sig["target_price"] or 0.0)
                tp2 = float(sig["target_price_2"] or 0.0)
                stop_p = float(sig["stop_loss"] or 0.0)
                tp1_al = int(sig["tp1_alerted"] or 0)
                tp2_al = int(sig["tp2_alerted"] or 0)
                sl_al = int(sig["sl_alerted"] or 0) if "sl_alerted" in sig.keys() else 0
                cat = sig["category"] or "Bozor"

                # 1. STOP-LOSS tekshiruvi (faqat narx darajasi bo'yicha)
                if stop_p > 0 and curr_p <= stop_p and not sl_al:
                    conn.execute("""
                        UPDATE ai_signals_history 
                        SET status = 'STOPPED', closed_pnl = ?, closed_date = COALESCE(closed_date, ?)
                        WHERE id = ?
                    """, (current_pnl, now_str, sig["id"]))
                    triggered_events.append({
                        "id": sig["id"], "ticker": ticker, "type": "SL_HIT",
                        "category": cat, "entry_price": entry_p, "exit_price": curr_p,
                        "target_price": stop_p, "pnl": current_pnl, "flag": "sl_alerted"
                    })
                    print(f"🛑 [STOP-LOSS] {ticker}: Kirish={entry_p}, SL={stop_p}, Joriy={curr_p} ({current_pnl}%)", flush=True)

                # 2. TP2 tekshiruvi (faqat narx darajasi bo'yicha)
                elif tp2 > 0 and curr_p >= tp2 and not tp2_al:
                    conn.execute("""
                        UPDATE ai_signals_history 
                        SET status = 'TARGET_HIT', closed_pnl = ?, closed_date = COALESCE(closed_date, ?)
                        WHERE id = ?
                    """, (current_pnl, now_str, sig["id"]))
                    triggered_events.append({
                        "id": sig["id"], "ticker": ticker, "type": "TP2_HIT",
                        "category": cat, "entry_price": entry_p, "exit_price": curr_p,
                        "target_price": tp2, "pnl": current_pnl, "flag": "tp2_alerted"
                    })
                    print(f"🚀 [TP2-HIT] {ticker}: Kirish={entry_p}, TP2={tp2}, Joriy={curr_p} (+{current_pnl}%)", flush=True)

                # 3. TP1 tekshiruvi (faqat narx darajasi bo'yicha)
                elif tp1 > 0 and curr_p >= tp1 and not tp1_al and not tp2_al:
                    conn.execute("""
                        UPDATE ai_signals_history 
                        SET status = 'TARGET_1_HIT', closed_pnl = ?
                        WHERE id = ?
                    """, (current_pnl, sig["id"]))
                    triggered_events.append({
                        "id": sig["id"], "ticker": ticker, "type": "TP1_HIT",
                        "category": cat, "entry_price": entry_p, "exit_price": curr_p,
                        "target_price": tp1, "pnl": current_pnl, "flag": "tp1_alerted"
                    })
                    print(f"🎯 [TP1-HIT] {ticker}: Kirish={entry_p}, TP1={tp1}, Joriy={curr_p} (+{current_pnl}%)", flush=True)
            except Exception as _sg_row_err:
                print(f"[TRACKER_ROW_ERR] #{sig['id']} {sig['ticker']}: {_sg_row_err}", flush=True)

        conn.commit()
    print(f"[TRACKER] {checked_count} ta signal tekshirildi, hodisalar: {len(triggered_events)}", flush=True)
    return triggered_events


def get_signals_conn():
    import sqlite3
    db_file = globals().get("SIGNALS_DB_PATH") or globals().get("DB_PATH") or "/home/ubuntu/bot/jett_signals.db"
    return sqlite3.connect(db_file)

def fmt_smart_price(val: float) -> str:
    """Aksiyalar narxini nominaliga qarab chiroyli formatlash."""
    if not val:
        return "0"
    if val < 10:
        return f"{val:.2f}"
    if val < 100:
        return f"{val:.1f}"
    return f"{val:,.0f}".replace(",", " ")

def fmt_smart_price(val: float) -> str:
    """Aksiyalar narxini nominaliga qarab chiroyli formatlash."""
    if not val:
        return "0"
    if val < 10:
        return f"{val:.2f}"
    if val < 100:
        return f"{val:.1f}"
    return f"{val:,.0f}".replace(",", " ")

def get_performance_stats(lang: str = 'uz', **kwargs) -> str:
    import sqlite3, statistics
    db_file = globals().get("DB_PATH", "/home/ubuntu/bot/jett_signals.db")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT id, ticker, status, entry_price, current_price, 
                   current_max_pnl, stop_loss, target_price, target_price_2, 
                   signal_date, closed_date, closed_pnl, tp1_pct, tp2_pct
            FROM ai_signals_history
            ORDER BY id ASC
        """).fetchall()
    except Exception as e:
        return f"Statistika yuklashda xatolik: {e}"
    finally:
        conn.close()

    if not rows:
        return "📊 <b>JETT RADAR — PORTFEL VA SIGNALLAR STATISTIKASI</b>\n\n<i>Hozircha signallar mavjud emas.</i>"

    def fmt_pr(v):
        v = float(v or 0)
        if v >= 100:
            return f"{v:,.0f}".replace(",", " ")
        elif v >= 1:
            return f"{v:.2f}"
        return f"{v:.4f}"

    SEP = "━━━━━━━━━━━━━━━━━━━━"
    open_pos = [r for r in rows if r["status"] in ("ACTIVE", "TARGET_1_HIT")]
    closed_pos = [r for r in rows if r["status"] not in ("ACTIVE", "TARGET_1_HIT")]

    lines = [
        "📊 <b>JETT RADAR — PORTFEL VA SIGNALLAR STATISTIKASI</b>",
        "🏛 <i>Toshkent fond birjasi (UZSE) • Algorithmic Analytics</i>",
        SEP,
        "",
        "📡 <b>OPEN POSITIONS | UNREALIZED PnL</b>",
        "",
        f"Jami monitoringda: <b>{len(open_pos)} ta aktiv</b>",
        "",
    ]

    if open_pos:
        pnls, n_profit, n_loss, n_be, n_tp1 = [], 0, 0, 0, 0
        for r in open_pos:
            ep = float(r["entry_price"] or 0)
            cp = float(r["current_price"] or ep)
            pnl = ((cp - ep) / ep * 100.0) if ep > 0 else 0.0
            pnls.append(pnl)
            
            if round(pnl, 2) > 0:
                n_profit += 1
            elif round(pnl, 2) < 0:
                n_loss += 1
            else:
                n_be += 1

            tp1_lim = float(r["tp1_pct"] or 10.0)
            if r["status"] == "TARGET_1_HIT" or pnl >= tp1_lim:
                n_tp1 += 1

        tot_op = len(open_pos)
        avg_pnl = sum(pnls) / tot_op if tot_op else 0.0
        med_pnl = statistics.median(pnls) if tot_op else 0.0

        lines += [
            f"🟢 Profit: <b>{n_profit}</b> ({n_profit/tot_op*100:.1f}%)",
            f"🔴 Loss: <b>{n_loss}</b> ({n_loss/tot_op*100:.1f}%)",
            f"⚪ Breakeven: <b>{n_be}</b> ({n_be/tot_op*100:.1f}%)",
            "",
            f"📈 Avg. PnL: <b>{avg_pnl:+.1f}%</b>",
            f"📊 Median PnL: <b>{med_pnl:+.1f}%</b>",
            f"🎯 TP1 Hit: <b>{n_tp1} ta</b>",
            "",
        ]

    lines += [
        SEP,
        "",
        "🎯 <b>CLOSED TRADES | REALIZED PnL</b>",
        "",
        f"📋 Yopilgan bitimlar: <b>{len(closed_pos)} ta</b>",
    ]

    if not closed_pos:
        lines += [
            "🏆 Win Rate: <b>N/A</b>",
            "⚖️ Risk/Reward (R): <b>N/A</b>",
            "💰 Realized PnL: <b>N/A</b>",
            "",
        ]
    else:
        n_tp2 = sum(1 for r in closed_pos if r["status"] in ("TARGET_2_HIT", "TARGET_HIT"))
        n_sl = sum(1 for r in closed_pos if r["status"] in ("STOPPED_OUT", "STOPPED"))
        wr = (n_tp2 / len(closed_pos) * 100) if len(closed_pos) else 0.0
        _wins = [float(r["closed_pnl"]) for r in closed_pos if r["status"] in ("TARGET_2_HIT", "TARGET_HIT") and r["closed_pnl"] is not None]
        _loss = [abs(float(r["closed_pnl"])) for r in closed_pos if r["status"] in ("STOPPED_OUT", "STOPPED") and r["closed_pnl"] is not None]
        _aw = sum(_wins) / len(_wins) if _wins else 0.0
        _al = sum(_loss) / len(_loss) if _loss else 0.0
        _rr_txt = f"{_aw / _al:.2f}x" if _al > 0 else "N/A"
        _all = [float(r["closed_pnl"]) for r in closed_pos if r["closed_pnl"] is not None]
        _real_txt = f"{sum(_all) / len(_all):+.1f}%" if _all else "N/A"
        lines += [
            f"🏆 Win Rate: <b>{wr:.1f}%</b> ({n_tp2} TP2 / {n_sl} SL)",
            f"⚖️ Risk/Reward (R): <b>{_rr_txt}</b>",
            f"💰 Realized PnL: <b>{_real_txt}</b> (o'rtacha bitimga)",
            "",
        ]

    peaks = []
    for r in rows:
        ep = float(r["entry_price"] or 0)
        if ep <= 0:
            continue
        max_p = float(r["current_max_pnl"]) if r["current_max_pnl"] is not None else (((float(r["current_price"] or ep) - ep) / ep) * 100.0)
        peak_pr = ep * (1.0 + max_p / 100.0)
        if max_p > 0:
            peaks.append((max_p, r["ticker"], ep, peak_pr))

    peaks.sort(key=lambda x: x[0], reverse=True)
    if peaks:
        lines += [SEP, "", "🚀 <b>TOP PERFORMERS | MAX GAIN</b>", ""]
        for i, (pct, tkr, ep, mp) in enumerate(peaks[:5], 1):
            lines.append(f"{i}. #{tkr}   <b>+{pct:.1f}%</b> ({fmt_pr(ep)} → {fmt_pr(mp)} UZS)")
        lines.append("")

    lines += [
        SEP,
        "",
        "⚙️ <b>RISK MANAGEMENT</b>",
        "",
        "🛡 Dynamic Stop-Loss",
        "💧 Liquidity Filter",
        "🎯 Take-Profit Targets",
        "📊 Performance Monitoring",
        "",
        SEP,
        "",
        "🤖 <b>JETT RADAR | MARKET INTELLIGENCE</b>",
        "<i>Algorithmic Signals • Market Analytics • Risk Control</i>",
        "",
        "⚠️ <i>MAX GAIN — maksimal kuzatilgan o‘sish, realizatsiya qilingan foyda emas. Joriy PnL va maksimal o‘sish farqlanishi mumkin.</i>",
        "",
        "⚠️ <i>O‘tgan natijalar kelajakdagi daromadni kafolatlamaydi. Ushbu hisobot individual investitsion tavsiya hisoblanmaydi.</i>"
    ]
    return "\n".join(lines)

def get_signals_list(kind="open", page=1, page_size=5):
    """Signallar ro'yxati (sahifalangan, to'liq TP/SL detallari bilan).
    kind: open | closed. Qaytaradi: (matn, jami_sahifalar)."""
    import sqlite3
    db_file = globals().get("SIGNALS_DB_PATH") or globals().get("DB_PATH") or "/home/ubuntu/bot/jett_signals.db"
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute("""
        SELECT id, ticker, category, status, signal_date, entry_price, current_price,
               target_price, target_price_2, stop_loss, tp1_pct, tp2_pct,
               current_max_pnl, closed_pnl, closed_date
        FROM ai_signals_history
        WHERE entry_price > 0
        ORDER BY signal_date DESC, id DESC
    """).fetchall()
    conn.close()

    if kind == "open":
        rows = [r for r in rows if r["status"] in ("ACTIVE", "TARGET_1_HIT")]
        title = "Faol signallar"
    elif kind == "closed":
        rows = [r for r in rows if r["status"] in ("TARGET_HIT", "STOPPED", "RUNNER_CLOSED", "EXPIRED")]
        title = "Yopilgan signallar"
    else:
        title = "Barcha signallar"

    def _px(v):
        v = float(v or 0)
        if v >= 100:
            return f"{v:,.0f}".replace(",", " ")
        if v >= 1:
            return f"{v:.2f}"
        return f"{v:.4f}"

    cat_map = {
        "undervalued": "Value", "dividend": "Dividend", "momentum": "Momentum",
        "breakout": "Breakout", "smart_money": "Smart Money", "catalyst": "Catalyst",
        "top_picks": "Top Pick", "safe_haven": "Defensive", "sleeping_giants": "Giants",
        "hidden_gems": "Hidden Gems"
    }

    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, int(page)), pages)
    chunk = rows[(page - 1) * page_size: page * page_size]

    lines = [
        "📡 <b>JETT RADAR — SIGNALLAR RO'YXATI</b>",
        f"Toifa: <b>{title}</b> │ Jami: <b>{total} ta</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]

    if not chunk:
        lines.append("<i>Signallar topilmadi.</i>")

    for r in chunk:
        tk = r["ticker"]
        cat_raw = str(r["category"] or "").lower()
        cat_title = cat_map.get(cat_raw, (r["category"] or "Aktiv").capitalize())

        ep = float(r["entry_price"])
        cp = float(r["current_price"] or ep)

        # PnL hisobi
        if kind == "closed" and r["closed_pnl"] is not None:
            pnl = float(r["closed_pnl"])
        else:
            pnl = ((cp - ep) / ep) * 100.0 if ep > 0 else 0.0

        # Sana formati (kirish)
        sd = str(r["signal_date"] or "")
        dt_str = ""
        if len(sd) >= 16:
            parts = sd[:10].split("-")
            dt_str = f"{parts[2]}.{parts[1]} {sd[11:16]}" if len(parts) == 3 else sd[:16]
        elif len(sd) >= 10:
            parts = sd[:10].split("-")
            dt_str = f"{parts[2]}.{parts[1]}" if len(parts) == 3 else sd[:10]
        else:
            dt_str = sd or "?"

        # Sana formati (yopilish)
        dt_close = ""
        if kind == "closed" and r["closed_date"]:
            cd = str(r["closed_date"])[:10].split("-")
            if len(cd) == 3:
                dt_close = f" ➔ {cd[2]}.{cd[1]}"

        # TP va SL hisoblari
        tp1_p = float(r["tp1_pct"] or 10.0)
        tp2_p = float(r["tp2_pct"] or 20.0)
        tp1_val = float(r["target_price"] or (ep * (1.0 + tp1_p / 100.0)))
        tp2_val = float(r["target_price_2"] or (ep * (1.0 + tp2_p / 100.0)))
        sl_val = float(r["stop_loss"] or (ep * 0.91))
        sl_p = ((sl_val - ep) / ep) * 100.0 if ep > 0 else -9.0

        # Status va belgi
        if r["status"] == "TARGET_HIT":
            icon = "🏆"
            status_note = " · (TP2 Urilgan)"
        elif r["status"] == "TARGET_1_HIT" or (kind == "open" and pnl >= tp1_p):
            icon = "🎯"
            status_note = " · (TP1 Urilgan)"
        elif r["status"] == "STOPPED":
            icon = "🛑"
            status_note = " · (Stop-Loss)"
        elif r["status"] == "EXPIRED":
            icon = "⏱️"
            status_note = " · (Muddati o'tgan)"
        elif round(pnl, 2) > 0:
            icon = "🟢"
            status_note = ""
        elif round(pnl, 2) < 0:
            icon = "🔴"
            status_note = ""
        else:
            icon = "⚪"
            status_note = ""

        pnl_sign = "+" if pnl > 0 else ""
        px_label = "Yopilish" if kind == "closed" else "Joriy"

        card = (
            f"{icon} <b>#{tk}</b> · <code>{dt_str}{dt_close}</code> · {cat_title}{status_note}\n"
            f"💵 Kirish: <code>{_px(ep)}</code> ➔ {px_label}: <b>{_px(cp)} UZS</b> (<b>{pnl_sign}{pnl:.1f}%</b>)\n"
            f"🎯 TP1: <code>{_px(tp1_val)}</code> <i>(+{tp1_p:.0f}%)</i> │ 🏆 TP2: <code>{_px(tp2_val)}</code> <i>(+{tp2_p:.0f}%)</i>\n"
            f"🛑 Stop-Loss: <code>{_px(sl_val)} UZS</code> <i>({sl_p:.1f}%)</i>\n"
        )
        lines.append(card)

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"<i>Sahifa {page}/{pages}</i>",
        "🎯 TP1 │ 🟢 Foydada │ 🔴 Zararda │ ⚪ Ochilishda"
    ]
    return "\n".join(lines), pages

def check_and_broadcast_tp_alerts(*args, **kwargs):
    import bot
    bot_obj = getattr(bot, "bot", None)
    admin_ids = getattr(bot, "ADMIN_IDS", [326567358])
    owner_id = getattr(bot, "OWNER_CHAT_ID", None)

    if not bot_obj:
        return 0

    recipients = set(admin_ids)
    if owner_id:
        recipients.add(owner_id)

    cat_names_uz = {
        "catalyst": "Katalizator",
        "breakout": "Texnik yorib o'tish",
        "high_risk": "Spekulyativ",
        "safe_haven": "Barqaror / Himoyalangan",
        "top_picks": "Asosiy tanlov",
        "dividend": "Dividendli",
        "value": "Fundamental"
    }

    sent_count = 0
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM ai_signals_history 
            WHERE status IN ('ACTIVE', 'TARGET_1_HIT', 'TARGET_HIT') AND entry_price > 0 AND (COALESCE(tp1_alerted, 0) = 0 OR COALESCE(tp2_alerted, 0) = 0)
        """).fetchall()

        for r in rows:
            ep = float(r["entry_price"] or 0)
            cp = float(r["current_price"] or ep)
            tp1 = float(r["target_price"] or 0)
            tp2 = float(r["target_price_2"] or 0)

            if ep <= 0 or cp <= 0:
                continue

            pnl = ((extract_float_price(cp) - extract_float_price(ep)) / ep) * 100.0

            if pnl > 100.0 or pnl < -50.0:
                continue

            ticker = r["ticker"].upper()
            cat_raw = str(r["category"] or "").lower()
            cat_uz = cat_names_uz.get(cat_raw, r["category"] or "Bozor")

            # TP2 Tekshiruvi
            if tp2 > ep and cp >= tp2 and not r["tp2_alerted"]:
                msg = (
                    "🚀 <b>JETT RADAR — TP2 ASOSIY MARRA ERISHILDI!</b>\n\n"
                    f"🎯 Aktiv: <b>#{ticker}</b> [<i>{cat_uz}</i>]\n"
                    f"🎯 Asosiy Marra (TP2): <b>+{((tp2-ep)/ep)*100:.1f}%</b>\n"
                    f"📈 Qayd etilgan o‘sish: <b>+{pnl:.2f}%</b>\n"
                    f"💵 Kirish narxi: <b>{ep:,.0f} so‘m</b>\n"
                    f"💰 Joriy narx: <b>{cp:,.0f} so‘m</b>\n\n"
                    "💡 <i>Aktiv belgilangan asosiy maqsadga to‘liq erishdi.\n"
                    "Hisob-kitoblar birjaning amaldagi savdo narxlariga muvofiq yuritiladi.</i>"
                )
                sent_any = False
                for r_cid in recipients:
                    try:
                        bot_obj.send_message(r_cid, msg, parse_mode="HTML")
                        sent_any = True
                    except Exception as ex:
                        print(f"Alert yuborishda xato ({r_cid}): {ex}", flush=True)
                if sent_any:
                    import datetime as _dt
                    closed_date_str = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                    conn.execute(
                        "UPDATE ai_signals_history SET tp2_alerted = 1, tp1_alerted = 1, status = 'TARGET_HIT', closed_date = ?, closed_pnl = ? WHERE id = ?",
                        (closed_date_str, pnl, r["id"])
                    )
                    sent_count += 1
                else:
                    print(f"[TP_ALERT_RETRY] #{ticker}: TP2 yuborilmadi; flag o'zgarmadi", flush=True)

            # TP1 Tekshiruvi
            elif tp1 > ep and cp >= tp1 and not r["tp1_alerted"] and not r["tp2_alerted"]:
                msg = (
                    "🎯 <b>JETT RADAR — TP1 ORALIQ MARRA ERISHILDI!</b>\n\n"
                    f"🎯 Aktiv: <b>#{ticker}</b> [<i>{cat_uz}</i>]\n"
                    f"🎯 Oraliq Marra (TP1): <b>+{((tp1-ep)/ep)*100:.1f}%</b>\n"
                    f"📈 Qayd etilgan o‘sish: <b>+{pnl:.2f}%</b>\n"
                    f"💵 Kirish narxi: <b>{ep:,.0f} so‘m</b>\n"
                    f"💰 Joriy narx: <b>{cp:,.0f} so‘m</b>\n\n"
                    "💡 <i>Aktiv birinchi belgilangan marraga yetdi.\n"
                    "Hisob-kitoblar birjaning amaldagi savdo narxlariga muvofiq yuritiladi.</i>"
                )
                sent_any = False
                for r_cid in recipients:
                    try:
                        bot_obj.send_message(r_cid, msg, parse_mode="HTML")
                        sent_any = True
                    except Exception as ex:
                        print(f"Alert yuborishda xato ({r_cid}): {ex}", flush=True)
                if sent_any:
                    conn.execute("UPDATE ai_signals_history SET tp1_alerted = 1 WHERE id = ?", (r["id"],))
                    sent_count += 1
                else:
                    print(f"[TP_ALERT_RETRY] #{ticker}: TP1 yuborilmadi; flag o'zgarmadi", flush=True)

        conn.commit()
    return sent_count

def update_live_prices():
    """Faqat joriy narx va current_max_pnl ni yangilaydi. Statusni ovozsiz yopmaydi!"""
    try:
        token = None
        for path in ["/home/ubuntu/bot/live_token.txt", "/home/ubuntu/jett_data/live_token.txt"]:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as tf:
                    t = tf.read().strip()
                    if t:
                        token = t
                        break
        if not token:
            import bot
            token = getattr(bot, "ADMIN_TOKEN", None)
            if not token and hasattr(bot, "fetch_fresh_token"):
                token = bot.fetch_fresh_token()

        if not token:
            return 0

        import market
        mdata = market.fetch_market_data(token)
        if not isinstance(mdata, dict):
            return 0

        instruments = (mdata.get("active_stocks", []) or []) + (mdata.get("active_bonds", []) or [])
        if not instruments:
            return 0

        price_map = {}
        for s in instruments:
            if not isinstance(s, dict):
                continue
            ticker = (s.get("ticker") or s.get("sec_code") or "").upper()
            price = s.get("close_price") or s.get("value") or s.get("current_price") or s.get("last_price")
            if ticker and price is not None:
                try:
                    p_val = float(price)
                    if p_val > 0:
                        price_map[ticker] = p_val
                except (ValueError, TypeError):
                    continue

        if not price_map:
            return 0

        conn = sqlite3.connect("/home/ubuntu/bot/jett_signals.db")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        active_rows = cur.execute("""
            SELECT id, ticker, entry_price, current_price, current_max_pnl
            FROM ai_signals_history
            WHERE status IN ('ACTIVE', 'TARGET_1_HIT') AND entry_price > 0
        """).fetchall()

        updated = 0
        for r in active_rows:
            tk = str(r["ticker"]).upper()
            if tk not in price_map:
                continue

            curr_p = price_map[tk]
            ep = float(r["entry_price"])
            if ep <= 0:
                continue

            # Narx himoyasi (2 baravardan ko'p sakrashni rad etish)
            prev_p = float(r["current_price"] or ep)
            if prev_p > 0 and abs(curr_p - prev_p) / prev_p > 1.0:
                continue

            pnl = round(((curr_p - ep) / ep) * 100.0, 2)
            new_max_pnl = max(float(r["current_max_pnl"] or 0.0), pnl)

            cur.execute("""
                UPDATE ai_signals_history
                SET current_price = ?,
                    current_max_pnl = ?
                WHERE id = ?
            """, (curr_p, new_max_pnl, r["id"]))
            updated += 1

        conn.commit()
        conn.close()
        return updated
    except Exception as e:
        print(f"⚠️ update_live_prices xatosi: {e}")
        return 0


def save_ai_radars_to_db(radar_data, raw_prioritized=None):
    if 'save_ai_signals' in globals():
        return save_ai_signals(radar_data, price_map=raw_prioritized)
    return False



def get_active_signals_detailed_post(lang="uz"):
    """Barcha faol signallarni TP1, TP2, SL va vaqti bilan bitta vizual postda chiqarish."""
    import sqlite3
    db_file = globals().get("SIGNALS_DB_PATH") or globals().get("DB_PATH") or "/home/ubuntu/bot/jett_signals.db"
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    
    query = """
        SELECT ticker, category, signal_date, entry_price, current_price,
               target_price, target_price_2, stop_loss, tp1_pct, tp2_pct, status
        FROM ai_signals_history
        WHERE status IN ('ACTIVE', 'TARGET_1_HIT') AND entry_price > 0
        ORDER BY id DESC
    """
    rows = conn.execute(query).fetchall()
    conn.close()

    if not rows:
        return (
            "📡 <b>JETT RADAR — FAOL SIGNALLAR</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Hozirda faol monitoringdagi signallar mavjud emas.</i>"
        )

    def _fmt(val):
        val = float(val or 0.0)
        if val >= 100:
            return f"{val:,.0f}".replace(",", " ")
        if val >= 1:
            return f"{val:.2f}"
        return f"{val:.4f}"

    lines = [
        "📡 <b>JETT RADAR — FAOL SIGNALLAR MONITORINGI</b>",
        "<i>Toshkent Fond Birjasi (UZSE) · Jonli nazorat</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        ""
    ]

    total_cnt = len(rows)
    shown_cnt = 0

    cat_map = {
        "undervalued": "Value", "dividend": "Dividend", "momentum": "Momentum",
        "breakout": "Breakout", "smart_money": "Smart Money", "catalyst": "Catalyst",
        "top_picks": "Top Pick", "safe_haven": "Defensive", "sleeping_giants": "Giants"
    }

    for r in rows:
        tk = r["ticker"]
        cat_raw = str(r["category"] or "").lower()
        cat_title = cat_map.get(cat_raw, (r["category"] or "Aktiv").capitalize())
        
        ep = float(r["entry_price"])
        cp = float(r["current_price"] or ep)
        pnl = ((cp - ep) / ep) * 100.0 if ep > 0 else 0.0
        
        # Vaqt formati: "16.09 10:30" yoki "16.09"
        sd = str(r["signal_date"] or "")
        dt_str = ""
        if len(sd) >= 16:
            parts = sd[:10].split("-")
            time_part = sd[11:16]
            dt_str = f"{parts[2]}.{parts[1]} {time_part}" if len(parts) == 3 else sd[:16]
        elif len(sd) >= 10:
            parts = sd[:10].split("-")
            dt_str = f"{parts[2]}.{parts[1]}" if len(parts) == 3 else sd[:10]

        # TP / SL hisobi
        tp1_p = float(r["tp1_pct"] or 10.0)
        tp2_p = float(r["tp2_pct"] or 20.0)
        tp1_val = float(r["target_price"] or (ep * (1.0 + tp1_p / 100.0)))
        tp2_val = float(r["target_price_2"] or (ep * (1.0 + tp2_p / 100.0)))
        
        sl_val = float(r["stop_loss"] or (ep * 0.91))
        sl_p = ((sl_val - ep) / ep) * 100.0 if ep > 0 else -9.0

        # Status ikonkalari
        if r["status"] == "TARGET_1_HIT" or pnl >= tp1_p:
            icon = "🎯"
            status_note = " · (TP1 Urilgan)"
        elif round(pnl, 2) > 0:
            icon = "🟢"
            status_note = ""
        elif round(pnl, 2) < 0:
            icon = "🔴"
            status_note = ""
        else:
            icon = "⚪"
            status_note = ""

        pnl_sign = "+" if pnl > 0 else ""

        card = (
            f"{icon} <b>#{tk}</b> · <code>{dt_str}</code> · {cat_title}{status_note}\n"
            f"💵 Kirish: <code>{_fmt(ep)}</code> ➔ Joriy: <b>{_fmt(cp)} UZS</b> (<b>{pnl_sign}{pnl:.1f}%</b>)\n"
            f"🎯 TP1: <code>{_fmt(tp1_val)}</code> <i>(+{tp1_p:.0f}%)</i> │ 🏆 TP2: <code>{_fmt(tp2_val)}</code> <i>(+{tp2_p:.0f}%)</i>\n"
            f"🛑 Stop-Loss: <code>{_fmt(sl_val)} UZS</code> <i>({sl_p:.1f}%)</i>\n"
        )

        # Telegram xabari hajmi nazorati (maks 3900 belgi)
        if sum(len(x) + 1 for x in lines) + len(card) + 150 > 3900:
            rem = total_cnt - shown_cnt
            lines.append(f"<i>... va yana {rem} ta faol signal kuzatuvda.</i>\n")
            break

        lines.append(card)
        shown_cnt += 1

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📌 Jami faol kuzatuvda: <b>{total_cnt} ta aktiv</b>")
    lines.append("💡 <i>Kotirovkalar birja savdolari asosida yangilanadi.</i>")

    return "\n".join(lines)


def auto_expire_stale_signals():
    """20 seans davomida harakat qilmagan, TP1 ga yetmagan turg'un signallarni EXPIRED qilish."""
    import sqlite3
    from datetime import datetime, timedelta

    db_file = globals().get("SIGNALS_DB_PATH") or globals().get("DB_PATH") or "/home/ubuntu/bot/jett_signals.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    # Kamida 25 kalendar kuni oldin ochilgan va hali TP1 ga ham yetmagan ACTIVE signallar
    cur.execute("""
        SELECT id, ticker, signal_date, entry_price, current_price
        FROM ai_signals_history
        WHERE status = 'ACTIVE'
          AND signal_date <= datetime('now', '-25 days')
          AND entry_price > 0
    """)
    stale_rows = cur.fetchall()

    expired_cnt = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for sid, tk, sdate, ep, cp in stale_rows:
        curr_p = float(cp or ep)
        pnl = round(((curr_p - ep) / ep) * 100.0, 2)
        cur.execute("""
            UPDATE ai_signals_history
            SET status = 'EXPIRED',
                closed_pnl = ?,
                closed_date = ?,
                q_reason = 'Avtomatik EXPIRED: 20+ seans harakatsizlik'
            WHERE id = ?
        """, (pnl, now_str, sid))
        expired_cnt += 1
        print(f"⏱️ [AUTO EXPIRED] #{tk} (ID {sid}): Ochilgan: {sdate[:10]}, PnL: {pnl}% yopildi")

    conn.commit()
    conn.close()
    return expired_cnt

def get_risk_radar_post(lang="uz"):
    """Bozor xatarlari, Quality Gate WATCH ro'yxati va xatarli korporativ faktlar monitoringi."""
    import sqlite3
    try:
        import quality
    except ImportError:
        quality = None

    radar_db = "/home/ubuntu/jett_data/jett_radar.db"
    conn = sqlite3.connect(f"file:{radar_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # 1. Quality Gate WATCH aktivlari (oxirgi 14 kunda savdo bo'lganlar)
    watch_items = []
    if quality and hasattr(quality, "assess"):
        insts = conn.execute("SELECT stock_id, ticker, market_code FROM instruments WHERE ticker IS NOT NULL").fetchall()
        for it in insts:
            sid = it["stock_id"]
            tk = it["ticker"].upper()
            is_bond = it["market_code"] == "BND" or "B" in tk[-2:]
            try:
                res = quality.assess(conn, sid, is_bond=is_bond)
                if isinstance(res, dict) and res.get("verdict") == "WATCH":
                    c = conn.execute("""
                        SELECT close, volume, (close * volume) as turn, candle_time 
                        FROM ohlcv 
                        WHERE stock_id = ? AND volume > 0 AND candle_time >= date('now', '-14 days')
                        ORDER BY candle_time DESC LIMIT 1
                    """, (sid,)).fetchone()
                    if c:
                        watch_items.append({
                            "ticker": tk,
                            "close": c["close"],
                            "turn": c["turn"],
                            "date": c["candle_time"][:10],
                            "reasons": res.get("reasons", [])
                        })
            except Exception:
                continue

    # 2. Oxirgi 30 kunlik №15 va №25 faktlar
    facts_q = """
        SELECT f.fact_number, 
               COALESCE(f.pub_date, f.added_on, f.created_at) as f_date,
               COALESCE(f.fact_title_uz, f.fact_title, f.fact_title_ru) as f_title,
               i.ticker
        FROM facts f
        JOIN instruments i ON f.issuer_id = i.issuer_id
        WHERE f.fact_number IN ('15', '25', 15, 25)
          AND COALESCE(f.pub_date, f.added_on, f.created_at) >= date('now', '-30 days')
        ORDER BY f_date DESC
        LIMIT 6
    """
    fact_rows = conn.execute(facts_q).fetchall()
    conn.close()

    def _fmt(val):
        val = float(val or 0)
        return f"{val:,.0f}".replace(",", " ") if val >= 100 else f"{val:.2f}"

    lines = [
        "⚠️ <b>JETT RADAR — BOZOR XATARLARI MONITORINGI</b>",
        "<i>Shubhali narx harakatlari va xatarli korporativ voqealar</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🚨 <b>ANOMAL FAOLLIK VA TUZOQ XATARI (WATCH):</b>",
        "<i>Likvidlik yetarli bo'lmagan, narx sun'iy ko'tarilgan bo'lishi mumkin bo'lgan aktivlar:</i>",
        ""
    ]

    if not watch_items:
        lines.append("✅ <i>Hozirda anomal manipulyatsiya belgilari aniqlanmadi.</i>")
        lines.append("")
    else:
        for w in watch_items:
            reasons_str = "; ".join(w["reasons"]) if w["reasons"] else "Kichik hajmda keskin tebranish"
            lines.append(f"🔴 <b>#{w['ticker']}</b> · Sana: <code>{w['date']}</code>")
            lines.append(f"💵 Oxirgi narx: <b>{_fmt(w['close'])} UZS</b>")
            lines.append(f"📊 Savdo aylanmasi: <code>{_fmt(w['turn'])} UZS</code>")
            lines.append(f"⚠️ Xatar: <i>{reasons_str}</i>")
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📰 <b>KORPORATIV VOQEALAR XATARI:</b>")
    lines.append("<i>№25 (Emissiya / Ulush suyulishi) va №15 (Yirik affillangan bitimlar):</i>")
    lines.append("")

    if not fact_rows:
        lines.append("<i>Oxirgi 30 kunda xatarli korporativ faktlar qayd etilmadi.</i>")
        lines.append("")
    else:
        for f in fact_rows:
            f_num = str(f["fact_number"])
            f_type = "Ulush suyulishi (Emissiya)" if f_num == "25" else "Affillangan bitim"
            f_date = str(f["f_date"] or "")[:10]
            lines.append(f"⚠️ <b>#{f['ticker']}</b> · Fakt №{f_num} · <code>{f_date}</code>")
            lines.append(f"📌 Toifa: <b>{f_type}</b>")
            lines.append(f"📄 Mazmuni: <i>{str(f['f_title'])[:55]}...</i>")
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Ushbu aktivlar savdo tavsiyasi hisoblanmaydi. Ular xavfni boshqarish uchun ogohlantirish tarzida taqdim etiladi.</i>")

    return chr(10).join(lines)


# === SGATE_PATCH_20261030: tracker yurak urishi ===
_sg_orig_update_signals_tracker = update_signals_tracker


def update_signals_tracker(*args, **kwargs):
    try:
        _sg_ensure_schema()
    except Exception as _e:
        print(f"[SCHEMA_ERR] {_e}", flush=True)
    _late = 0.0
    try:
        _hb = _sg_hb_read()
        _prev = float(_hb.get("last_ok") or 0)
        if str(_hb.get("last_state", "")).startswith("skip"):
            _prev = max(_prev, float(_hb.get("last_loop") or 0))
        if _prev > 0:
            _late = _sg_trading_minutes(_prev, _sg_time.time())
    except Exception:
        _late = 0.0
    try:
        _res = _sg_orig_update_signals_tracker(*args, **kwargs)
    except Exception as _e:
        _sg_hb_write(False, _e)
        raise
    _sg_hb_write(True)
    if _late > SG_LATE_MIN_MINUTES and isinstance(_res, list):
        for _ev in _res:
            if isinstance(_ev, dict):
                _ev["late_min"] = int(round(_late))
        print(f"[TRACKER_LATE] kuzatuv savdo vaqtida ~{int(round(_late))} daqiqa uzilgan edi; xabarlar kechikkan deb belgilandi", flush=True)
    return _res


