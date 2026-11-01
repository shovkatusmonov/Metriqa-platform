"""
gemini_limiter.py — JETT Radar uchun Gemini RPM/RPD limitiga qarab
navbatdagi eng mos modelni tanlovchi modul.
"""

import sqlite3
import time
import threading
from pathlib import Path

MODEL_LIMITS = {
    "gemini-3.1-flash-lite":   (14,   450),
    "gemini-3.5-flash-lite":   (14,   450),
    "gemini-2.5-flash-lite":   (9,    18),
    "gemini-3.6-flash":        (4,    18),
    "gemini-3.7-flash":        (4,    18),
    "gemini-3.8-flash":        (4,    18),
}

# Ikkita alohida tartib — ishlatilish maqsadiga qarab:

# 1) SIFAT-BIRINCHI: kamdan-kam chaqiriladigan, lekin muhim
#    "umumiy bozor skaneri" uchun — har doim eng kuchli Flash
#    modeldan boshlanadi (yaxshiroq fikrlash). Jami sig'im 54/kun.
FALLBACK_ORDER_HEAVY = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
]

# 2) SIG'IM-BIRINCHI: tez-tez chaqiriladigan "bitta aktiv/fakt
#    tahlili" uchun — avval yuqori RPD'li flash-lite ishlatiladi,
#    shunda katta hajmdagi so'rovlar botni to'xtatib qo'ymaydi.
FALLBACK_ORDER_LITE = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

# Orqaga moslik: agar biror joyda hali FALLBACK_ORDER ishlatilsa
FALLBACK_ORDER = FALLBACK_ORDER_HEAVY

DB_PATH = Path(__file__).parent / "gemini_quota.db"
_lock = threading.Lock()


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            model TEXT NOT NULL,
            ts REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cooldown (
            model TEXT PRIMARY KEY,
            until_ts REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


_init_db()


def _count_recent(conn, model, window_sec):
    now = time.time()
    cur = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE model = ? AND ts > ?",
        (model, now - window_sec),
    )
    return cur.fetchone()[0]


def _is_cooling_down(conn, model):
    cur = conn.execute("SELECT until_ts FROM cooldown WHERE model = ?", (model,))
    row = cur.fetchone()
    if row and row[0] > time.time():
        return True
    return False


def _set_cooldown(conn, model, seconds):
    until = time.time() + seconds
    conn.execute(
        "INSERT INTO cooldown (model, until_ts) VALUES (?, ?) "
        "ON CONFLICT(model) DO UPDATE SET until_ts = excluded.until_ts",
        (model, until),
    )
    conn.commit()


def _record_request(conn, model):
    conn.execute("INSERT INTO requests (model, ts) VALUES (?, ?)", (model, time.time()))
    conn.commit()


def get_available_model(order=None):
    order = order or FALLBACK_ORDER_HEAVY
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            for model in order:
                rpm_limit, rpd_limit = MODEL_LIMITS[model]
                if _is_cooling_down(conn, model):
                    continue
                used_minute = _count_recent(conn, model, 60)
                used_day = _count_recent(conn, model, 86400)
                if used_minute < rpm_limit and used_day < rpd_limit:
                    return model
            return None
        finally:
            conn.close()


def _mark_used(model):
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            _record_request(conn, model)
        finally:
            conn.close()


def _mark_failed(model, is_rate_limit_error):
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            if is_rate_limit_error:
                rpm_limit, rpd_limit = MODEL_LIMITS.get(model, (4, 18))
                used_day = _count_recent(conn, model, 86400)
                if used_day >= rpd_limit:
                    _set_cooldown(conn, model, 6 * 3600)
                else:
                    # 503 yoki RPM to'lganda modelni kamida 60 soniyaga sovutish
                    _set_cooldown(conn, model, 60)
            else:
                # Boshqa jiddiy xatoliklarda ham 30 soniya boshqa modellarni sinash
                _set_cooldown(conn, model, 30)
        finally:
            conn.close()


def _looks_like_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(code in msg for code in ("429", "503", "resource_exhausted", "quota", "overloaded"))


def call_gemini_smart(prompt_text, temperature=0.2, max_models_to_try=None, client=None, types=None, order=None):
    order = order or FALLBACK_ORDER_HEAVY
    tried = 0
    limit = max_models_to_try or len(order)

    while tried < limit:
        model = get_available_model(order=order)
        if model is None:
            print("[gemini_limiter] Barcha modellar hozircha band (RPM/RPD limiti).")
            return None

        tried += 1
        try:
            _mark_used(model)
            resp = client.models.generate_content(
                model=model,
                contents=prompt_text,
                config=types.GenerateContentConfig(temperature=temperature),
            )
            if resp and resp.text:
                return resp.text.strip()
            continue
        except Exception as err:
            is_rl = _looks_like_rate_limit(err)
            print(f"[gemini_limiter] '{model}' xato berdi ({'rate-limit' if is_rl else 'boshqa'}): {err}")
            _mark_failed(model, is_rl)
            continue

    print("[gemini_limiter] Barcha urinishlar tugadi, natija yo'q.")
    return None
