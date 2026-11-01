"""JETT Radar - Signal Quality Gate (deterministik, faqat o'qish).
Aksiya tarixi (ohlcv) asosida likvidlik, volatillik va manipulyatsiya
belgilarini hisoblaydi. AI ga faqat shu tekshiruvdan o'tgan nomzodlar,
to'liq tarix bilan yuboriladi."""
import statistics

Q = {
    "min_act20": 8,            # oxirgi 20 seansda kamida shuncha kun savdo
    "max_gap": 5,              # oxirgi savdodan buyon o'tgan seanslar
    "min_med_turn": 5_000_000,
    "min_med_turn_bond": 2_000_000, # savdo kunlari medianasi (so'm)
    "min_distinct": 4,         # oxirgi 15 seansdagi xil yopilish narxlari
    "min_vol20": 0.4,          # kunlik tebranish (% stdev), faqat aksiya
    "min_range20": 3.0,        # 20 kunlik diapazon (%), faqat aksiya
    "spike_ratio": 8.0,        # oxirgi aylanma / median
    "thin_move_pct": 5.0,
    "thin_move_turn": 5_000_000,
    "jump_ret5": 25.0,
    "quiet_range": 5.0,
    "big_move_pct": 15.0,         # bir kunlik harakat (%)
    "big_move_r5": 30.0,          # 5 kunlik osish (%)
    "big_move_turn": 50_000_000,  # shundan kam aylanmada katta harakat shubhali
}


def _rows(conn, stock_id, before=None, limit=90):
    """Eng yangisi birinchi. before='YYYY-MM-DD' bo'lsa shu kun kiritilmaydi."""
    sql = ("SELECT substr(candle_time,1,10), open, high, low, close, volume "
           "FROM ohlcv WHERE stock_id=?")
    args = [stock_id]
    if before:
        sql += " AND substr(candle_time,1,10) < ?"
        args.append(before)
    sql += " ORDER BY candle_time DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def features(rows):
    if not rows or len(rows) < 5:
        return None
    v = lambda x: x[5] or 0
    c = lambda x: x[4] or 0
    r20 = rows[:20]
    traded = [x for x in rows if v(x) > 0 and c(x) > 0]
    act20 = sum(1 for x in r20 if v(x) > 0)
    act60 = sum(1 for x in rows[:60] if v(x) > 0)
    gap = next((i for i, x in enumerate(rows) if v(x) > 0), len(rows))
    rets = [(c(a) / c(b) - 1) * 100 for a, b in zip(traded[:21], traded[1:22])]
    vol20 = statistics.pstdev(rets) if len(rets) >= 5 else 0.0

    def ret_n(n):
        return (c(traded[0]) / c(traded[n]) - 1) * 100 if len(traded) > n else None

    t20 = [v(x) * c(x) for x in r20 if v(x) > 0]
    med_turn = statistics.median(t20) if t20 else 0.0
    last_turn = v(rows[0]) * c(rows[0]) if gap == 0 else 0.0
    if med_turn > 0:
        spike = last_turn / med_turn
    else:
        spike = 99.0 if last_turn > 0 else 0.0
    top_share = (max(t20) / sum(t20)) if t20 else 0.0
    hs = [x[2] for x in r20 if v(x) > 0 and x[2]]
    ls = [x[3] for x in r20 if v(x) > 0 and x[3]]
    range20 = (max(hs) / min(ls) - 1) * 100 if hs and ls and min(ls) > 0 else 0.0
    distinct15 = len({round(c(x), 2) for x in rows[:15] if c(x) > 0})
    older = traded[5:25]
    old_range = None
    if len(older) >= 8:
        lo = min(c(x) for x in older)
        if lo > 0:
            old_range = (max(c(x) for x in older) / lo - 1) * 100
    r1, r5 = ret_n(1), ret_n(5)
    thin_move = bool(r1 is not None and abs(r1) >= Q["thin_move_pct"]
                     and gap == 0 and last_turn < Q["thin_move_turn"])
    quiet_jump = bool(r5 is not None and r5 >= Q["jump_ret5"]
                      and old_range is not None and old_range < Q["quiet_range"])
    big_move = bool(gap == 0 and last_turn < Q["big_move_turn"] and (
        (r1 is not None and abs(r1) >= Q["big_move_pct"]) or
        (r5 is not None and r5 >= Q["big_move_r5"])))
    hi60 = max((c(x) for x in rows[:60]), default=0)
    dd60 = (c(traded[0]) / hi60 - 1) * 100 if traded and hi60 > 0 else 0.0
    return {"act20": act20, "act60": act60, "gap": gap, "vol20": vol20,
            "ret1": r1, "ret5": r5, "ret20": ret_n(20), "ret60": ret_n(60),
            "med_turn": med_turn, "last_turn": last_turn, "spike": spike,
            "top_share": top_share, "range20": range20, "distinct15": distinct15,
            "thin_move": thin_move, "quiet_jump": quiet_jump, "dd60": dd60,
            "big_move": big_move}


def gate(f, is_bond=False):
    if not f:
        return "BLOCK", ["tarix yetarli emas"]
    block, watch = [], []
    if f["act20"] < Q["min_act20"]:
        block.append("20 seansda %d kun savdo" % f["act20"])
    if f["gap"] > Q["max_gap"]:
        block.append("oxirgi savdodan %d seans o'tgan" % f["gap"])
    if f["distinct15"] < Q["min_distinct"]:
        block.append("narx qotgan (%d xil narx)" % f["distinct15"])
    _mt = Q["min_med_turn_bond"] if is_bond else Q["min_med_turn"]
    if f["med_turn"] < _mt:
        block.append("median aylanma %.2f mln (kamida %.0f mln)" % (f["med_turn"] / 1e6, _mt / 1e6))
    if (not is_bond) and f["vol20"] < Q["min_vol20"] and f["range20"] < Q["min_range20"]:
        block.append("volatillik yo'q (%.2f%%)" % f["vol20"])
    if f["spike"] >= Q["spike_ratio"] and f["act20"] < 12:
        watch.append("blok bitim belgisi (%.0fx median)" % min(f["spike"], 99))
    if f["top_share"] >= 0.6 and f["act20"] < 12:
        watch.append("aylanma bitta kunda (%.0f%%)" % (f["top_share"] * 100))
    if f["thin_move"]:
        watch.append("yupqa bozorda katta harakat")
    if f["quiet_jump"]:
        watch.append("uzoq tinchlikdan keyin sakrash")
    if f["big_move"]:
        watch.append("kichik aylanmada keskin harakat (spekulyatsiya belgisi)")
    verdict = "BLOCK" if block else ("WATCH" if watch else "PASS")
    return verdict, block + watch


def assess(conn, stock_id, is_bond=False, before=None, limit=90):
    rows = _rows(conn, stock_id, before=before, limit=limit)
    f = features(rows)
    verdict, reasons = gate(f, is_bond)
    return {"rows": rows, "f": f, "verdict": verdict, "reasons": reasons}


def _p(x, fmt="%+.1f"):
    return "-" if x is None else (fmt % x) + "%"


def stats_line(f):
    if not f:
        return "stats: n/a"
    return ("ret1=%s ret5=%s ret20=%s ret60=%s vol20=%s range20=%s act20=%d/20 "
            "act60=%d/60 med_turn=%.1fmln spike=%.1fx dd60=%s" % (
                _p(f["ret1"]), _p(f["ret5"]), _p(f["ret20"]), _p(f["ret60"]),
                _p(f["vol20"], "%.2f"), _p(f["range20"], "%.1f"), f["act20"],
                f["act60"], f["med_turn"] / 1e6, min(f["spike"], 99), _p(f["dd60"])))


def history_block(ticker, rows, n=90):
    r = list(reversed(rows[:n]))
    if not r:
        return "%s: tarix yo'q" % ticker
    closes = ",".join("%g" % round(x[4] or 0, 2) for x in r)
    turns = ",".join("%.1f" % (((x[5] or 0) * (x[4] or 0)) / 1e6) for x in r)
    return "%s %s..%s (%d seans)\n  close: %s\n  turn_mln: %s" % (
        ticker, r[0][0], r[-1][0], len(r), closes, turns)
