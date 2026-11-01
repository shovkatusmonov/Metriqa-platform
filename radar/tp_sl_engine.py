# -*- coding: utf-8 -*-
"""
tp_sl_engine.py v2 - deterministik TP/SL dvigateli (faqat LONG).

Tamoyillar:
  * Barcha raqamlarni shu modul hisoblaydi; Gemini faqat tayyor natijani izohlaydi.
  * SL = struktura (tasdiqlangan support ostida) va ATR stopidan KENGROG'I (narx bo'yicha min).
    Risk qopqog'idan oshsa signal RAD ETILADI (stop sun'iy toraytirilmaydi).
  * TP = resistance klasterlari. R/R haqiqiy tekshiruv: xarajatlardan keyingi R/R past bo'lsa RAD.
  * Ma'lumot yetarli bo'lmasa, yangi bo'lmasa yoki hodisa bloklasa: RAD (sabab kodi bilan).
  * Pivotlar faqat TASDIQLANGAN (keyingi k ta sham bilan) - lookahead yo'q.

DIQQAT: commission_rate va spread_pct hozircha TASDIQLANMAGAN taxminiy qiymatlar.
        Haqiqiy tarifni JETT/broker'dan tekshirib, o'zgartiring.
"""
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional

ENGINE_VERSION = "2.0"

REASONS = {
    "INVALID_PRICE": "Noto'g'ri joriy narx",
    "INSUFFICIENT_HISTORY": "Tarixiy shamlar yetarli emas",
    "INSUFFICIENT_ACTIVE_HISTORY": "Savdo bo'lgan kunlar yetarli emas (siyrak savdo)",
    "STALE_DATA": "Narx ma'lumoti eskirgan",
    "PRICE_MISMATCH": "Joriy narx oxirgi sham narxidan juda farq qiladi (ma'lumot xatosi ehtimoli)",
    "ATR_UNAVAILABLE": "Tebranish (ATR) hisoblab bo'lmadi",
    "EVENT_BLOCK": "Korporativ hodisa signalni bloklaydi",
    "RISK_CAP_EXCEEDED": "Stop-loss uchun zarur risk ruxsat etilgan qopqoqdan yuqori",
    "SL_TOO_TIGHT": "Stop-loss juda tor (bozor shovqini bilan urilib ketadi)",
    "RR_TOO_LOW": "Xarajatlardan keyingi Risk/Mukofot nisbati yetarli emas",
    "NO_TP_LEVEL": "Yuqorida maqsad darajasi topilmadi",
    "BOND_NOT_SUPPORTED": "Obligatsiyalar uchun aksiyadorlik Stop-Loss qo'llanilmaydi (alohida Yield/Kredit modeli talab etiladi)",
    "OK": "Tasdiqlandi",
}

DEFAULT_PARAMS = dict(
    window=120,              # tahlil oynasi (oxirgi qatorlar soni)
    min_active_days=30,      # oynada kamida shuncha savdo bo'lgan kun
    atr_period=14,
    atr_mult=1.5,            # ATR stopi: kirish - atr_mult * ATR
    sl_buffer_atr=0.25,      # support ostidagi bufer (ATR ulushida)
    pivot_k=2,               # pivotni tasdiqlash uchun ikki tomondagi shamlar
    cluster_tol_atr=0.5,     # darajalarni klasterlash bag'ri (ATR ulushida)
    max_risk_cap=0.15,       # eng katta ruxsat etilgan risk (kirishga nisbatan)
    min_risk_pct=0.02,       # eng kichik ruxsat etilgan risk
    min_rr=1.5,              # xarajatlardan keyingi minimal R/R (backtestda tasdiqlanadi)
    commission_rate=0.006,   # bir tomonlama komissiya (TASDIQLANMAGAN)
    spread_pct=0.005,        # taxminiy spred (TASDIQLANMAGAN)
    allow_atr_projection=True,
    proj_mult=(3.0, 5.0),    # resistance bo'lmasa ATR proyeksiyasi (TP1, TP2)
    max_entry_deviation=0.25,
    max_stale_days=7,
    low_activity_ratio=0.5,
    portfolio_size=100000000.0,
    risk_per_trade_pct=0.01,
    ticker=None,
    is_bond=False,
)


def is_bond_instrument(ticker: Optional[str]) -> bool:
    if not ticker:
        return False
    t = str(ticker).strip().upper()
    if any(t.endswith(s) for s in ["1B", "2B", "3B", "4B", "5B", "B1", "B2", "B3", "B4"]):
        return True
    import re
    return bool(re.search(r'\d+B\d*$', t))

BLOCKING_CLASSES = {"HARD_BLOCK", "BLOCK_NEW"}


# ----------------------------------------------------------------- yordamchilar
def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN ni chiqarib tashlash


def clean_candles(candles: Any) -> List[Dict[str, Any]]:
    """Xom shamlarni tozalaydi (None/NaN/noto'g'ri qiymatlarga chidamli), eskidan yangiga saralaydi."""
    rows = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        close = _f(c.get("close"))
        if close is None or close <= 0:
            continue
        o, h, l = _f(c.get("open")), _f(c.get("high")), _f(c.get("low"))
        vals = [v for v in (o, h, l, close) if v is not None and v > 0]
        rows.append({
            "t": str(c.get("candle_time") or c.get("format_time") or ""),
            "o": o if o and o > 0 else close,
            "h": max(vals), "l": min(vals), "c": close,
            "v": _f(c.get("volume")),
        })
    rows.sort(key=lambda r: r["t"])
    return rows


def true_ranges(rows: List[Dict[str, Any]]) -> List[float]:
    out = []
    for i in range(1, len(rows)):
        pc = rows[i - 1]["c"]
        out.append(max(rows[i]["h"] - rows[i]["l"], abs(rows[i]["h"] - pc), abs(rows[i]["l"] - pc)))
    return out


def robust_atr(rows: List[Dict[str, Any]], period: int = 14) -> float:
    """Faqat savdo bo'lgan kunlar bo'yicha True Range mediani (o'tkir spike'larga chidamli)."""
    tr = true_ranges(rows)[-period:]
    if not tr:
        return 0.0
    m = median(tr)
    return m if m > 0 else sum(tr) / len(tr)


def find_pivots(rows: List[Dict[str, Any]], k: int = 2):
    """Tasdiqlangan pivotlar: oxirgi k ta sham HECH QACHON pivot bo'lmaydi (lookahead yo'q)."""
    lows, highs = [], []
    for i in range(k, len(rows) - k):
        wl = [r["l"] for r in rows[i - k:i + k + 1]]
        wh = [r["h"] for r in rows[i - k:i + k + 1]]
        if rows[i]["l"] == min(wl):
            lows.append(rows[i]["l"])
        if rows[i]["h"] == max(wh):
            highs.append(rows[i]["h"])
    return lows, highs


def cluster_levels(levels: List[float], tol: float):
    """Yaqin darajalarni birlashtiradi. Qaytadi: [(daraja=klaster minimumi, teginishlar soni), ...]"""
    out, cur = [], []
    for lv in sorted(levels):
        if cur and lv - (sum(cur) / len(cur)) > tol:
            out.append((min(cur), len(cur)))
            cur = []
        cur.append(lv)
    if cur:
        out.append((min(cur), len(cur)))
    return out


def _reject(code: str, **extra) -> Dict[str, Any]:
    d = {"status": "REJECTED", "reason_code": code, "reason": REASONS.get(code, code),
         "engine_version": ENGINE_VERSION, "flags": []}
    d.update(extra)
    return d


# -------------------------------------------------------------------- asosiy
def compute_quant_levels(
    current_price: float,
    candles: Optional[list] = None,
    event_flags: Optional[list] = None,
    as_of: Optional[str] = None,
    **overrides,
) -> Dict[str, Any]:
    """
    Qaytaradi: status = APPROVED | REJECTED va sabab kodi. Foizlar (risk_pct, tp1_pct, tp2_pct)
    protsent ko'rinishida (masalan 6.7). Kirish parametrlari o'zgartirilmaydi.
    event_flags: ["HARD_BLOCK:INSOLVENCY", {"class": "EVENT_RISK", "type": "LARGE_DEAL"}, ...]
    as_of: "YYYY-MM-DD" (berilsa, ma'lumot yangiligi tekshiriladi).
    """
    p = dict(DEFAULT_PARAMS)
    unknown = set(overrides) - set(p)
    if unknown:
        raise TypeError("Noma'lum parametr(lar): %s" % ", ".join(sorted(unknown)))
    p.update(overrides)

    # Obligatsiyalarni ajratish (aksiyalar modeli qo'llanilmaydi)
    target_ticker = p.get("ticker")
    if p.get("is_bond") or (target_ticker and is_bond_instrument(target_ticker)):
        return _reject("BOND_NOT_SUPPORTED", ticker=target_ticker)

    entry = _f(current_price)
    if entry is None or entry <= 0:
        return _reject("INVALID_PRICE")

    flags: List[str] = []

    # --- hodisa filtri
    blocks = []
    for ev in event_flags or []:
        if isinstance(ev, str):
            cls, _, typ = ev.partition(":")
        elif isinstance(ev, dict):
            cls, typ = str(ev.get("class", "")), str(ev.get("type", ""))
        else:
            continue
        if cls in BLOCKING_CLASSES:
            blocks.append("%s:%s" % (cls, typ))
        elif cls:
            flags.append("%s:%s" % (cls, typ))
    if blocks:
        return _reject("EVENT_BLOCK", blocked_by=blocks, flags=flags)

    # --- ma'lumotni tozalash va yetarlilik
    rows = clean_candles(candles)[-int(p["window"]):]
    has_volume = any(r["v"] is not None for r in rows)
    active = [r for r in rows if (r["v"] or 0) > 0] if has_volume else list(rows)
    if not has_volume:
        flags.append("NO_VOLUME_DATA")
    if len(rows) < 20:
        return _reject("INSUFFICIENT_HISTORY", candles_total=len(rows))
    if len(active) < p["min_active_days"]:
        return _reject("INSUFFICIENT_ACTIVE_HISTORY", candles_total=len(rows), active_days=len(active))

    last_close = active[-1]["c"]
    if as_of:
        try:
            age = (datetime.strptime(as_of[:10], "%Y-%m-%d") - datetime.strptime(rows[-1]["t"][:10], "%Y-%m-%d")).days
            if age > p["max_stale_days"]:
                return _reject("STALE_DATA", age_days=age)
        except ValueError:
            flags.append("AS_OF_UNPARSEABLE")
    if abs(entry - last_close) / last_close > p["max_entry_deviation"]:
        return _reject("PRICE_MISMATCH", last_close=round(last_close, 2))

    atr = robust_atr(active, p["atr_period"])
    if atr <= 0:
        return _reject("ATR_UNAVAILABLE")
    tol = atr * p["cluster_tol_atr"]

    ratio = len(active) / len(rows)
    if has_volume and ratio < p["low_activity_ratio"]:
        flags.append("LOW_ACTIVITY")
    turnovers = [r["v"] * r["c"] for r in active if r["v"]]
    median_turnover = round(median(turnovers), 0) if turnovers else None

    # --- pivotlar va klasterlar (faqat savdo bo'lgan kunlar)
    piv_lows, piv_highs = find_pivots(active, p["pivot_k"])
    supports = [lv for lv, _ in cluster_levels(piv_lows, tol) if lv < entry]
    support_touch = {lv: n for lv, n in cluster_levels(piv_lows, tol)}
    support_level = max(supports) if supports else None

    # --- VOLATILITY VA DINAMIK ATR MULTIPLIER
    atr_pct = (atr / entry) * 100.0
    if "atr_mult" in overrides:
        atr_mult = float(overrides["atr_mult"])
        vol_label, vol_class = ("Dynamic", "CUSTOM")
    else:
        if atr_pct < 3.0:
            atr_mult, vol_label, vol_class = 1.5, "🟢 Low (1.5x)", "LOW"
        elif atr_pct < 6.0:
            atr_mult, vol_label, vol_class = 2.0, "🟡 Medium (2.0x)", "MEDIUM"
        elif atr_pct < 10.0:
            atr_mult, vol_label, vol_class = 2.5, "🟠 High (2.5x)", "HIGH"
        else:
            atr_mult, vol_label, vol_class = 3.0, "🔴 Extreme (3.0x)", "EXTREME"

    # --- LIKVIDLIK FILTRI (UZSE aylanmasi bo'yicha)
    liq_label, liq_class = "🟢 Good", "GOOD"
    buffer_mult = p["sl_buffer_atr"]
    if median_turnover is not None:
        if median_turnover >= 50000000:
            liq_label, liq_class = "🟢 Good", "GOOD"
        elif median_turnover >= 10000000:
            liq_label, liq_class = "🟡 Moderate", "MODERATE"
        else:
            liq_label, liq_class = "🔴 Low", "LOW"
            flags.append("LOW_LIQUIDITY")
            buffer_mult = max(buffer_mult, 0.4) # Illikvid bozor uchun kengroq bufer

    # --- STOP-LOSS
    atr_sl = entry - atr_mult * atr
    if support_level is not None:
        struct_sl = support_level - buffer_mult * atr
        sl = min(struct_sl, atr_sl)
        sl_basis = "STRUCTURE" if struct_sl <= atr_sl else "ATR"
    else:
        sl, sl_basis = atr_sl, "ATR"
        flags.append("NO_SUPPORT_BELOW")
    if sl <= 0:
        return _reject("RISK_CAP_EXCEEDED", risk_pct=100.0)
    risk = (entry - sl) / entry
    if risk > p["max_risk_cap"]:
        return _reject("RISK_CAP_EXCEEDED", risk_pct=round(risk * 100, 2),
                       cap_pct=round(p["max_risk_cap"] * 100, 2), flags=flags)
    if risk < p["min_risk_pct"]:
        return _reject("SL_TOO_TIGHT", risk_pct=round(risk * 100, 2), flags=flags)

    cost = 2 * p["commission_rate"] + p["spread_pct"]

    # --- TAKE-PROFIT: resistance klasterlari
    res_clusters = [(lv, n) for lv, n in cluster_levels(piv_highs, tol) if lv > entry]
    window_high = max(r["h"] for r in active)
    cand = [lv for lv, _ in res_clusters]
    if window_high > entry and all(abs(window_high - lv) > tol for lv in cand):
        cand.append(window_high)
    cand = sorted(cand)

    if cand:
        tp_basis = "STRUCTURE"
        tp1 = cand[0]
        tp2 = next((lv for lv in cand[1:] if lv > tp1 + tol), None)
        if tp2 is None:
            flags.append("NO_TP2_STRUCTURE")
    elif p["allow_atr_projection"]:
        tp_basis = "ATR_PROJECTION"
        tp1, tp2 = entry + p["proj_mult"][0] * atr, entry + p["proj_mult"][1] * atr
        flags.append("AT_PERIOD_HIGH")
    else:
        return _reject("NO_TP_LEVEL", flags=flags)

    reward1 = (tp1 - entry) / entry
    rr1 = (reward1 - cost) / (risk + cost)
    if rr1 < p["min_rr"]:
        return _reject("RR_TOO_LOW", rr_net=round(rr1, 2), min_rr=p["min_rr"],
                       risk_pct=round(risk * 100, 2), tp1_pct=round(reward1 * 100, 2), flags=flags)
    rr2 = None
    if tp2 is not None:
        rr2 = ((tp2 - entry) / entry - cost) / (risk + cost)

    return {
        "status": "APPROVED",
        "reason_code": "OK",
        "reason": REASONS["OK"],
        "engine_version": ENGINE_VERSION,
        "entry_price": round(entry, 2),
        "stop_loss": round(sl, 2),
        "take_profit_1": round(tp1, 2),
        "take_profit_2": round(tp2, 2) if tp2 is not None else None,
        "risk_reward_ratio": round(rr1, 2),
        "rr2_net": round(rr2, 2) if rr2 is not None else None,
        "risk_pct": round(risk * 100, 2),
        "tp1_pct": round(reward1 * 100, 2),
        "tp2_pct": round((tp2 - entry) / entry * 100, 2) if tp2 is not None else None,
        "cost_pct": round(cost * 100, 2),
        "atr": round(atr, 2),
        "atr_pct": round(atr / entry * 100, 2),
        "support_level": round(support_level, 2) if support_level is not None else None,
        "swing_low": round(support_level, 2) if support_level is not None else 0.0,
        "sl_basis": sl_basis,
        "tp_basis": tp_basis,
        "support_touches": support_touch.get(support_level) if support_level is not None else None,
        "active_days": len(active),
        "active_ratio": round(ratio, 2) if has_volume else None,
        "median_turnover": median_turnover,
        "flags": flags,
        "volatility_label": vol_label,
        "volatility_class": vol_class,
        "atr_mult": atr_mult,
        "liquidity_label": liq_label,
        "liquidity_class": liq_class,
        "signal_action": "BUY" if rr1 >= 1.8 else "WAIT",
        "portfolio_size": float(p["portfolio_size"]),
        "portfolio_risk_pct": float(p["risk_per_trade_pct"]) * 100.0,
        "suggested_shares": int((float(p["portfolio_size"]) * float(p["risk_per_trade_pct"])) // (entry - sl)) if (entry - sl) > 0 else 0,
        "suggested_amount": round((int((float(p["portfolio_size"]) * float(p["risk_per_trade_pct"])) // (entry - sl)) if (entry - sl) > 0 else 0) * entry, 2),
    }
