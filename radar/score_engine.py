"""
JETT Radar — Quant Engine V2 (Ko'p o'lchovli Kvant Skoring va Klasterlash Moduli)
Asimmetrik momentum, institutsional hajm konsentratsiyasi (Smart Money),
likvidlik va ishonchlilik (Confidence) ko'rsatkichlari bilan boyitilgan.
"""

def is_corporate_bond(ticker: str) -> bool:
    """
    Aksiya va obligatsiyalarni aniq ajratish.
    4 harfli bank aksiyalari (IPTB, SQBN, HMKB) aksiya hisoblanadi.
    Obligatsiyalar: 6+ belgili va raqam qatnashgan yoki B1/B2/B3 bilan tugagan kodlar.
    """
    t = ticker.upper().strip()
    return (len(t) > 5 and any(c.isdigit() for c in t)) or t.endswith(('B1', 'B2', 'B3'))


def calculate_market_scores(active_assets):
    """
    Barcha aktivlarni tahlil qilib, 13 ta toifaga saralash uchun
    kvant, institutsional va xatti-harakat ko'rsatkichlarini tayyorlaydi.
    """
    if not active_assets:
        return []

    # Umumiy bozor aylanmasi
    total_market_turnover = sum(
        float(a.get("gross_trade_amount", 0) or a.get("trade_amount", 0) or 0) for a in active_assets
    ) or 1.0

    # 1. QAT'IY LIKVIDLIK VA ANOMALIYALAR FILTRI
    filtered_assets = []
    for a in active_assets:
        ticker = (a.get("ticker") or "").upper().strip()
        turnover = float(a.get("gross_trade_amount", 0) or a.get("trade_amount", 0) or 0)
        volume = float(a.get("volume_today", 0) or a.get("volume", 0) or 0)
        trades = int(a.get("trades_count_today", 0) or a.get("trades_today", 0) or a.get("deals_count", 0) or 0)
        
        is_bond = is_corporate_bond(ticker)
        
        # Obligatsiyalar uchun: kamida 500 ming UZS aylanma yoki 1 dona hajm
        if is_bond and (turnover >= 500_000 or volume >= 1):
            filtered_assets.append(a)
            continue
            
        # Aksiyalar uchun: kamida 5 mln UZS aylanma VA kamida 2 ta bitim
        if turnover >= 5_000_000 and trades >= 2 and volume >= 10:
            filtered_assets.append(a)
        elif turnover >= 20_000_000:  # Yirik yagona blokli bitimlar
            filtered_assets.append(a)

    scored_list = []
    for asset in filtered_assets:
        ticker = (asset.get("ticker") or "N/A").upper().strip()
        price = float(asset.get("last_price", 0) or asset.get("current_price", 0) or 0)
        turnover = float(asset.get("gross_trade_amount", 0) or asset.get("trade_amount", 0) or 0)
        volume = float(asset.get("volume_today", 0) or asset.get("volume", 0) or 0)
        change_pct = float(asset.get("growth_prcnt_today", 0) or asset.get("percent_change", 0) or 0)
        trades_count = int(asset.get("trades_count_today", 0) or asset.get("deals_count", 0) or 1)
        trades_count = max(trades_count, 1)

        market_share = (turnover / total_market_turnover) * 100.0
        avg_trade_size = turnover / trades_count
        is_bond = is_corporate_bond(ticker)

        # ----------------------------------------------------
        # 1. SMART MONEY V2 (Institutsional kapital konsentratsiyasi)
        # ----------------------------------------------------
        # Katta bitta bitim hajmi (Whale ticket) va bozor ulushi hisobga olinadi
        whale_factor = min(40, int((avg_trade_size / 15_000_000) * 10))  # Har 15 mln o'rtacha chekka ball
        volume_factor = min(35, int(market_share * 2.5))
        activity_factor = min(20, int(min(trades_count, 100) * 0.2))
        
        if turnover >= 50_000_000:
            smart_money_score = min(99, 40 + whale_factor + volume_factor + activity_factor)
        elif turnover >= 15_000_000:
            smart_money_score = min(80, 30 + whale_factor + volume_factor + activity_factor)
        else:
            smart_money_score = max(25, min(60, 20 + volume_factor + activity_factor))

        # ----------------------------------------------------
        # 2. ASIMMETRIK MOMENTUM (Yo'nalishli impuls)
        # ----------------------------------------------------
        # Pasayayotgan aktivlarga sun'iy yuqori ball berish xatosi tuzatildi
        if change_pct > 0:
            # O'sayotgan: o'sish foizi + bitimlar faolligi + aylanma tayanchi
            growth_weight = min(50, int(change_pct * 3.5))
            trade_weight = min(25, int(min(trades_count, 60) * 0.4))
            turnover_weight = min(20, int((turnover / 25_000_000) * 5))
            momentum_score = min(98, 25 + growth_weight + trade_weight + turnover_weight)
        elif abs(change_pct) <= 0.5:
            # Barqaror konsolidatsiya (baza qurilishi)
            momentum_score = 50 if turnover >= 20_000_000 else 40
        else:
            # Pasayayotgan (Dump / tuzatish): past impuls
            momentum_score = max(15, int(40 - min(25, abs(change_pct) * 2.0)))

        # ----------------------------------------------------
        # 3. LIKVIDLIK VA IJRO BALI (Liquidity & Execution)
        # ----------------------------------------------------
        # Treyder uchun spred va tez sotish/sotib olish qulayligi
        liq_turnover = min(50, int((turnover / 30_000_000) * 15))
        liq_trades = min(35, int((trades_count / 20) * 15))
        liq_volume = min(15, int((volume / 500) * 5))
        liquidity_score = min(98, max(20, liq_turnover + liq_trades + liq_volume + 10))

        # ----------------------------------------------------
        # 4. DEFENSIVE SCORE (Himoyalanganlik va past volatillik)
        # ----------------------------------------------------
        if is_bond:
            defensive_score = 94
        else:
            # Aksiya uchun: past tebranish va mustahkam likvidlik
            if abs(change_pct) <= 1.5 and turnover >= 20_000_000:
                defensive_score = 85
            elif abs(change_pct) <= 3.0 and turnover >= 10_000_000:
                defensive_score = 70
            elif abs(change_pct) > 10.0:
                defensive_score = 30  # Haddan tashqari yuqori o'zgaruvchanlik
            else:
                defensive_score = 50

        # ----------------------------------------------------
        # 5. HIGH RISK SCORE (Spekulyativ xatar)
        # ----------------------------------------------------
        if is_bond:
            high_risk_score = 15
        else:
            if (abs(change_pct) > 10.0 and trades_count < 8) or abs(change_pct) >= 15.0:
                high_risk_score = 92  # Manipulyativ sakrash
            elif abs(change_pct) >= 7.0 and turnover < 10_000_000:
                high_risk_score = 75
            else:
                high_risk_score = 35

        # ----------------------------------------------------
        # 6. CONFIDENCE SCORE (Statistik asoslilik darajasi)
        # ----------------------------------------------------
        # Faqat 2 ta bitim bilan tasodifan ball olgan aktivlarni ajratadi
        conf_trades = min(45, int(trades_count * 2.0))
        conf_turnover = min(35, int((turnover / 20_000_000) * 15))
        conf_base = 20
        confidence_score = min(99, conf_base + conf_trades + conf_turnover)

        # ----------------------------------------------------
        # 7. COMPOSITE JETT SCORE (Umumiy kvant reytingi)
        # ----------------------------------------------------
        if is_bond:
            # Obligatsiyalar uchun himoyalanganlik va aylanma asosiy o'rinda
            overall_score = int((defensive_score * 0.50) + (smart_money_score * 0.30) + (liquidity_score * 0.20))
        else:
            # Aksiyalar uchun: Momentum (30%), Smart Money (30%), Likvidlik (20%), Himoya (20%)
            overall_score = int(
                (momentum_score * 0.30) +
                (smart_money_score * 0.30) +
                (liquidity_score * 0.20) +
                (defensive_score * 0.20)
            )

        scored_list.append({
            "ticker": ticker,
            "raw_data": asset,
            "price": price,
            "turnover": turnover,
            "volume": volume,
            "change_pct": change_pct,
            "trades_count": trades_count,
            "market_share": market_share,
            "avg_trade_size": avg_trade_size,
            "is_bond": is_bond,
            "scores": {
                "smart_money": smart_money_score,
                "momentum": momentum_score,
                "defensive": defensive_score,
                "high_risk": high_risk_score,
                "liquidity": liquidity_score,
                "confidence": confidence_score,
                "overall": overall_score
            }
        })

    # Aylanma bo'yicha saralash
    scored_list.sort(key=lambda x: x["turnover"], reverse=True)
    return scored_list
