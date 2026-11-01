import sqlite3, os, re
from google import genai
from google.genai import types
import config

DB_PATHS = [
    "/home/ubuntu/jett_data/jett_radar.db",
    "/home/ubuntu/bot/jett_radar.db",
    "jett_radar.db"
]

def get_db():
    for p in DB_PATHS:
        if os.path.exists(p):
            return sqlite3.connect(p, timeout=15)
    return None

def get_gemini_client():
    if hasattr(config, "GEMINI_API_KEY") and config.GEMINI_API_KEY:
        clean_key = str(config.GEMINI_API_KEY).strip().replace('"', '').replace("'", "")
        if clean_key:
            try:
                return genai.Client(api_key=clean_key)
            except Exception:
                pass
    return None

def analyze_catalyst_fact(ticker, lang="uz"):
    conn = get_db()
    if not conn:
        return "❌ Ma'lumotlar bazasi topilmadi."

    cur = conn.cursor()
    query = """
        SELECT 
            f.fact_id, 
            f.fact_number, 
            COALESCE(f.fact_title_uz, f.fact_title_ru, f.fact_title) AS title,
            f.added_on,
            f.openinfo_id,
            COALESCE(m.short_name, m.full_name, 'Emitent') AS comp_name,
            t.extracted_text
        FROM instruments i
        JOIN facts f ON i.issuer_id = f.issuer_id
        LEFT JOIN openinfo_mapping m ON f.issuer_id = m.issuer_id
        LEFT JOIN fact_documents d ON f.fact_id = d.fact_id
        LEFT JOIN fact_document_text t ON d.id = t.fact_document_id
        WHERE i.ticker = ? COLLATE NOCASE
        ORDER BY f.fact_id DESC
        LIMIT 1
    """
    cur.execute(query, (ticker,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return (
            f"⚠️ <b>#{ticker}</b> bo'yicha yaqin orada e'lon qilingan korporativ fakt topilmadi."
            if lang == "uz" else
            f"⚠️ По <b>#{ticker}</b> не найдено недавно опубликованных корпоративных фактов."
        )

    fact_id, fact_number, title, added_on, openinfo_id, comp_name, extracted_text = row
    clean_title = re.sub(r'\s+', ' ', title or '').strip() or f"Fakt №{fact_number}"
    clean_comp = re.sub(r'[\s"“«”»]+', ' ', comp_name or '').strip()
    date_str = str(added_on).replace("T", " ")[:16] if added_on else "Yaqinda"

    # 1. Keshni tekshirish (fact_ai_analysis)
    try:
        cur.execute("SELECT ai_summary FROM fact_ai_analysis WHERE fact_id = ?", (fact_id,))
        cached = cur.fetchone()
        if cached and cached[0]:
            conn.close()
            return cached[0]
    except Exception:
        pass

    # 2. Gemini orqali tahlil generatsiya qilish
    client = get_gemini_client()
    if not client:
        conn.close()
        return "❌ Gemini AI mijozi faollashtirilmagan (API kalit xatosi)."

    text_snippet = (extracted_text[:1500] if extracted_text else clean_title)

    lang_inst = "O'zbek tilida" if lang == "uz" else "На русском языке"
    prompt = f"""
Sen O'zbekiston fond bozori (Toshkent Respublika Fond Birjasi - RFB, OpenInfo) bo'yicha institutsional darajadagi bosh moliyaviy tahlilchisan.
Quyidagi korporativ faktni emitent va aksiyadorlar manfaati nuqtai nazaridan chuqur tahlil qil.

Kompaniya: {clean_comp} (#{ticker})
Fakt raqami: №{fact_number}
Mavzusi: {clean_title}
Sana: {date_str}
Fakt matni/ma'lumotlari:
\"\"\"{text_snippet}\"\"\"

Tahlilni qat'iy ravishda {lang_inst}, Telegram HTML formatida (faqat <b>, <i>, <code> teglaridan foydalanib), quyidagi aniq strukturada yoz:

⚡️ <b>#{ticker} — KATALIZATOR / FAKT TAHLILI</b>
━━━━━━━━━━━━━━━━━━━━━━
🏛 <b>Emitent:</b> {clean_comp}
📋 <b>Fakt:</b> №{fact_number} — {clean_title}
🕒 <b>Sana:</b> {date_str}

🧭 <b>Bozorga ta'siri (Sentiment):</b> [🟢 Bullish / 🔴 Bearish / ⚪️ Neytral deb belgilab, 1 qator sababini yoz]
⚡️ <b>Katalizator kuchi:</b> [1 dan 10 gacha ball ber, masalan: 8/10]

📊 <b>Kutilayotgan narx va likvidlik reaksiyasi:</b>
[Ushbu voqea qisqa va o'rta muddatda aksiyaga qanday ta'sir qilishi haqida 2-3 ta ixcham jumla]

🔍 <b>Fundamental mohiyati:</b>
[Faktning kompaniya biznesi, daromadi, dividendlari yoki boshqaruviga amaliy ta'siri]

🎯 <b>Investor uchun strategik xulosa:</b>
[Aksiyadorlar va yangi kiruvchilar uchun aniq qadam: Masalan, ushlab turish (Hold), xarid qilish (Accumulate) yoki xatardan qochish]
━━━━━━━━━━━━━━━━━━━━━━
⚠️ <i>AI institutsional xulosasi. Investitsiya qarori emas.</i>
"""

    from gemini_limiter import call_gemini_smart, FALLBACK_ORDER_LITE
    report = call_gemini_smart(prompt, temperature=0.2, client=client, types=types, order=FALLBACK_ORDER_LITE)

    if not report:
        conn.close()
        return "❌ AI tahlilini shakllantirishda xatolik yuz berdi. Qaytadan urinib ko'ring."

    # 3. Keshga saqlash
    try:
        cur.execute(
            "INSERT OR REPLACE INTO fact_ai_analysis (fact_id, ai_summary, model_used, created_at) VALUES (?, ?, ?, datetime('now'))",
            (fact_id, report, mod)
        )
        conn.commit()
    except Exception:
        pass

    conn.close()
    return report
