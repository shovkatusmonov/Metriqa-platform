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

import catalyst_engine
TPSL_ALERTS_FILE = '/home/ubuntu/jett_data/tpsl_alerts.json'
GLOBAL_CACHE_FILE = "/home/ubuntu/bot/last_ai_cache.json"

def load_global_ai_cache():
    import json, os
    if os.path.exists(GLOBAL_CACHE_FILE):
        try:
            with open(GLOBAL_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def get_current_radar_data(cid):
    res = user_radar_cache.get(cid)
    if not res or not res.get("radars"):
        cached = load_global_ai_cache()
        if cached and isinstance(cached, dict):
            user_radar_cache[cid] = cached
            return cached
    return res or {}

def save_global_ai_cache(data):
    import json
    try:
        with open(GLOBAL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

import signals_db
signals_db.init_signals_db()

def get_recent_corporate_facts(limit=10):
    db_paths = ['/home/ubuntu/jett_data/jett_radar.db', 'jett_radar.db']
    target_db = next((p for p in db_paths if os.path.exists(p)), None)
    if not target_db:
        return []
    try:
        conn = sqlite3.connect(target_db, timeout=5)
        cur = conn.cursor()
        query = """
            SELECT 
                COALESCE((SELECT ticker FROM instruments WHERE issuer_id = f.issuer_id AND ticker IS NOT NULL LIMIT 1), m.short_name, 'AKTIV') AS ticker,
                f.fact_title_uz
            FROM facts f
            LEFT JOIN openinfo_mapping m ON f.issuer_id = m.issuer_id
            ORDER BY f.fact_id DESC
            LIMIT ?
        """
        cur.execute(query, (limit,))
        rows = cur.fetchall()
        conn.close()
        res = []
        for t, title in rows:
            if title:
                clean_title = re.sub(r'[\s]+', ' ', title).strip()[:60]
                res.append({"t": t, "f": clean_title})
        return res
    except Exception:
        return []


import threading
import time

class AnimateTyping:
    def __init__(self, bot, chat_id, *args, **kwargs):
        self.bot = bot
        self.chat_id = chat_id
        self.stop_event = threading.Event()
        self.thread = None

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.bot.send_chat_action(self.chat_id, 'typing')
            except Exception:
                pass
            self.stop_event.wait(4.0)

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()


class KeepTyping:
    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id
        self.running = False
        self.thread = None

    def _run(self):
        while self.running:
            try:
                self.bot.send_chat_action(self.chat_id, 'typing')
            except Exception:
                pass
            time.sleep(4)

    def __enter__(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.running = False

import os
import io
import time
import json
import datetime
import threading
import importlib
import sqlite3
import re
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

import config
import market
import score_engine
import ai_engine
genai_client = getattr(ai_engine, 'client', None)
genai_types = getattr(ai_engine, 'types', None)
import get_token

bot = telebot.TeleBot(config.TG_BOT_TOKEN)

ADMIN_TOKEN = ""
ADMIN_IDS = [326567358, 5122851410]
OWNER_CHAT_ID = 326567358

SUBSCRIBERS_FILE = "/home/ubuntu/bot/subscribers.json"

user_sessions = {}
user_radar_cache = {}
user_languages = {}
user_names = {}
user_market_alerts = {}
user_fact_alerts = {}

# Alert anti-spam nazorati
seen_market_alerts = set()
last_alert_check_day = ""

TOKEN_LOCK = threading.Lock()

def fetch_fresh_token(force=False):
    global ADMIN_TOKEN
    with TOKEN_LOCK:
        try:
            token_file = "/home/ubuntu/bot/live_token.txt"
            if not force and os.path.exists(token_file):
                with open(token_file, "r", encoding="utf-8") as f:
                    tok = f.read().strip()
                    if tok.startswith("eyJ") and len(tok) > 30:
                        ADMIN_TOKEN = tok
                        return ADMIN_TOKEN
            
            import get_token
            importlib.reload(get_token)
            new_token = get_token.get_live_token()
            if new_token:
                ADMIN_TOKEN = new_token
                return ADMIN_TOKEN
        except Exception as e:
            print(f"❌ Token xatoligi: {e}")
    return ADMIN_TOKEN

def update_token_worker():
    """Faqat Jett sessiya tokenini har 10 daqiqada yangilab turuvchi mustaqil oqim."""
    import time
    while True:
        try:
            if hasattr(market, "get_fresh_token"):
                market.get_fresh_token()
            elif hasattr(market, "fetch_fresh_token"):
                market.fetch_fresh_token()
        except Exception as e:
            print(f"[TOKEN_WORKER ERROR]: {e}")
        time.sleep(600)

def tp_monitoring_worker():
    """Faqat narxlar va TP/SL monitoringini har 60 soniyada bajaruvchi mustaqil oqim."""
    import time
    while True:
        try:
            if hasattr(signals_db, "update_live_prices"):
                signals_db.update_live_prices()
            if hasattr(signals_db, 'auto_expire_stale_signals'):
                signals_db.auto_expire_stale_signals()
            if hasattr(signals_db, "check_and_broadcast_tp_alerts"):
                signals_db.check_and_broadcast_tp_alerts(bot)
        except Exception as e:
            print(f"[TP_MONITOR ERROR]: {e}")
        time.sleep(60)


def load_subscribers():
    global user_market_alerts, user_fact_alerts, user_languages
    try:
        if os.path.exists(SUBSCRIBERS_FILE):
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                old_alerts = data.get("alerts", {})
                user_market_alerts = {int(k): v for k, v in data.get("market_alerts", old_alerts).items()}
                user_fact_alerts = {int(k): v for k, v in data.get("fact_alerts", old_alerts).items()}
                user_languages = {int(k): v for k, v in data.get("languages", {}).items()}
        for aid in ADMIN_IDS:
            if aid not in user_market_alerts:
                user_market_alerts[aid] = True
            if aid not in user_fact_alerts:
                user_fact_alerts[aid] = True
            if aid not in user_languages:
                user_languages[aid] = "uz"
    except Exception:
        pass

def save_subscribers():
    try:
        import os
        temp_file = f"{SUBSCRIBERS_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({
                "market_alerts": user_market_alerts,
                "fact_alerts": user_fact_alerts,
                "languages": user_languages
            }, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, SUBSCRIBERS_FILE)
    except Exception as e:
        print(f"[SAVE_SUBSCRIBERS ERROR]: {e}")


load_subscribers()

def get_lang(chat_id):
    return user_languages.get(chat_id, "uz")

def safe_send_or_edit(chat_id, text, message_id=None, reply_markup=None):
    max_len = 3900
    if len(text) <= max_len:
        if message_id:
            try:
                return bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=reply_markup)
            except Exception:
                return bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            return bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
    
    parts = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    for idx, part in enumerate(parts):
        markup = reply_markup if idx == len(parts) - 1 else None
        bot.send_message(chat_id, part, parse_mode="HTML", reply_markup=markup)

def clean_uzs(val):
    if not val or val == "N/A" or str(val).strip() in ("0", "0.0", "0 UZS"):
        return "N/A"
    val_str = str(val).strip()
    if "TP" in val_str or "|" in val_str or "%" in val_str:
        return val_str
    val_str = val_str.replace("UZS", "").replace("uzs", "").strip()
    return f"{val_str} UZS"

def format_turnover(amount, lang="uz"):
    billion_str = "mlrd UZS" if lang == "uz" else "млрд UZS"
    million_str = "mln UZS" if lang == "uz" else "млн UZS"
    if amount >= 1_000_000_000:
        return f"{amount / 1_000_000_000:,.2f} {billion_str}"
    return f"{amount / 1_000_000:,.1f} {million_str}"

def make_bar(pct):
    filled = max(0, min(10, round(pct / 10)))
    return "▓" * filled + "░" * (10 - filled)

PILLAR_GROUPS = {
    "uz": {
        "grp_opps": {
            "title": "🔎 OPPORTUNITIES (Imkoniyatlar)",
            "desc": "Fundamental sifat, dividend va qiymat imkoniyatlari:",
            "cats": [
                ("hidden_gems", "💎 Hidden Gems"),
                ("undervalued", "💰 Value Opportunities"),
                ("dividend", "💵 Income Hunter"),
                ("sleeping_giants", "🧊 Sleeping Giants")
            ]
        },
        "grp_activity": {
            "title": "📊 MARKET ACTIVITY (Bozor Faolligi)",
            "desc": "Kapital oqimi, korporativ voqealar va texnik tezlanish:",
            "cats": [
                ("smart_money", "🐋 Smart Money"),
                ("momentum", "📈 Momentum"),
                ("breakout", "🧨 Breakout Watch"),
                ("catalyst", "📰 Catalyst Watch")
            ]
        },
        "grp_decision": {
            "title": "⚖️ DECISION (Strategik Tanlov)",
            "desc": "Kompozit filtrlar orqali saralangan bozor yetakchilari:",
            "cats": [
                ("top_picks", "🏆 JETT Top Picks")
            ]
        },
        "grp_risk": {
            "title": "🛡️ RISK (Himoyalangan Aktivlar)",
            "desc": "Ishonchli va kafolatlangan korporativ obligatsiyalar:",
            "cats": [
                ("safe_haven", "🛡️ Defensive Picks"),
                ("risk_radar", "⚠️ Risk Radar (Bozor xatarlari)")
            ]
        }
    },
    "ru": {
        "grp_opps": {
            "title": "🔎 OPPORTUNITIES (Возможности)",
            "desc": "Фундаментальное качество, дивиденды и недооцененные активы:",
            "cats": [
                ("hidden_gems", "💎 Hidden Gems"),
                ("undervalued", "💰 Value Opportunities"),
                ("dividend", "💵 Income Hunter"),
                ("sleeping_giants", "🧊 Sleeping Giants")
            ]
        },
        "grp_activity": {
            "title": "📊 MARKET ACTIVITY (Рыночная активность)",
            "desc": "Приток капитала, корпоративные события и технический импульс:",
            "cats": [
                ("smart_money", "🐋 Smart Money"),
                ("momentum", "📈 Momentum"),
                ("breakout", "🧨 Breakout Watch"),
                ("catalyst", "📰 Catalyst Watch")
            ]
        },
        "grp_decision": {
            "title": "⚖️ DECISION (Стратегический выбор)",
            "desc": "Лидеры рынка, отобранные композитными фильтрами:",
            "cats": [
                ("top_picks", "🏆 JETT Top Picks")
            ]
        },
        "grp_risk": {
            "title": "🛡️ RISK (Защитные Активы)",
            "desc": "Надежные корпоративные облигации:",
            "cats": [
                ("safe_haven", "🛡️ Defensive Picks"),
                ("risk_radar", "⚠️ Risk Radar (Рыночные риски)")
            ]
        }
    }
}

RADAR_CATEGORIES = {
    "uz": {
        "hidden_gems": {"title": "💎 Hidden Gems", "pillar": "grp_opps", "desc": "Fundamental sifati va valuation bo'yicha bozor tomonidan to'liq baholanmagan aktivlar."},
        "undervalued": {"title": "💰 Value Opportunities", "pillar": "grp_opps", "desc": "Estimated Fair Value va moliyaviy multiplikatorlar bo'yicity qulay narxdagi aktivlar."},
        "growth": {"title": "🚀 Growth Leaders", "pillar": "grp_opps", "desc": "Biznes ko'rsatkichlari, daromad va aylanma o'sish sur'ati yuqori bo'lgan kompaniyalar."},
        "dividend": {"title": "💵 Income Hunter", "pillar": "grp_opps", "desc": "Yuqori dividend rentabelligi va barqaror kupon to'lovlariga ega aktivlar."},
        "sleeping_giants": {"title": "🧊 Sleeping Giants", "pillar": "grp_opps", "desc": "Yirik kapitallashuvga ega, konsolidatsiya fazasida turgan gigantlar."},
        "smart_money": {"title": "🐋 Smart Money", "pillar": "grp_activity", "desc": "Yirik savdo hajmlari va institutsional kapital konsentratsiyasi."},
        "momentum": {"title": "📈 Momentum", "pillar": "grp_activity", "desc": "Kuchli narx dinamikasi, trend tezligi va faol savdo bitimlari."},
        "breakout": {"title": "🧨 Breakout Watch", "pillar": "grp_activity", "desc": "Texnik qarshilik darajasini yorib o'tish setup'lari."},
        "catalyst": {"title": "📰 Catalyst Watch", "pillar": "grp_activity", "desc": "Kutilayotgan korporativ voqealar va davriy hisobotlar fonidagi aktivlar."},
        "risk_reward": {"title": "⚖️ Best Risk/Reward", "pillar": "grp_decision", "desc": "Kutilayotgan o'sish potentsiali va xatar nisbati eng maqbul aktivlar."},
        "top_picks": {"title": "🏆 JETT Top Picks", "pillar": "grp_decision", "desc": "Barcha ko'rsatkichlar bo'yicha eng yuqori Overall Score olgan yetakchilar."},
        "safe_haven": {"title": "🛡️ Defensive Picks", "pillar": "grp_risk", "desc": "Past volatillik, mustahkam balans va himoyalangan aktivlar."},
        "risk_radar": {"title": "⚠️ Risk Radar", "pillar": "grp_risk", "desc": "Shubhali tebranishlar, kam aylanmali sakrashlar va xatarli korporativ faktlar nazorati."},
        "high_risk": {"title": "⚠️ High Risk / High Potential", "pillar": "grp_risk", "desc": "Yuqori spekulyativ tebranish va katta o'sish potentsialiga ega aktivlar."}
    },
    "ru": {
        "hidden_gems": {"title": "💎 Hidden Gems", "pillar": "grp_opps", "desc": "Активы, недооцененные рынком по фундаментальному качеству и оценке."},
        "undervalued": {"title": "💰 Value Opportunities", "pillar": "grp_opps", "desc": "Активы по привлекательной цене с учетом справедливой стоимости и мультипликаторов."},
        "growth": {"title": "🚀 Growth Leaders", "pillar": "grp_opps", "desc": "Компании с высокими темпами роста выручки, оборота и бизнес-показателей."},
        "dividend": {"title": "💵 Income Hunter", "pillar": "grp_opps", "desc": "Активы с высокой дивидендной доходностью и стабильными купонными выплатами."},
        "sleeping_giants": {"title": "🧊 Sleeping Giants", "pillar": "grp_opps", "desc": "Компании с крупной капитализацией, находящиеся в фазе консолидации."},
        "smart_money": {"title": "🐋 Smart Money", "pillar": "grp_activity", "desc": "Крупные объемы торгов и концентрация институционального капитала."},
        "momentum": {"title": "📈 Momentum", "pillar": "grp_activity", "desc": "Сильная динамика цен, скорость тренда и высокая торговая активность."},
        "breakout": {"title": "🧨 Breakout Watch", "pillar": "grp_activity", "desc": "Технические сетапы на пробой ключевых уровней сопротивления."},
        "catalyst": {"title": "📰 Catalyst Watch", "pillar": "grp_activity", "desc": "Активы на фоне ожидаемых корпоративных событий и периодической отчетности."},
        "risk_reward": {"title": "⚖️ Best Risk/Reward", "pillar": "grp_decision", "desc": "Активы с оптимальным соотношением потенциальной доходности к риску."},
        "top_picks": {"title": "🏆 JETT Top Picks", "pillar": "grp_decision", "desc": "Лидеры с наивысшим общим баллом (Overall Score) по всем ключевым параметрам."},
        "safe_haven": {"title": "🛡️ Defensive Picks", "pillar": "grp_risk", "desc": "Защитные активы с низкой волатильностью и надежным финансовым балансом."},
        "high_risk": {"title": "⚠️ High Risk / High Potential", "pillar": "grp_risk", "desc": "Спекулятивные активы с высокой волатильностью и большим потенциалом роста."}
    }
}

TEXTS = {
    "uz": {
        "welcome": (
            "📊 <b>JETT RADAR — Algoritmik Birja Analitikasi</b>\n\n"
            "JETT.UZ platformasidagi aksiyalar va obligatsiyalar bo'yicha "
            "real vaqt rejimidagi skoring hamda institutsional AI tahlil platformasi.\n\n"
            "🟢 <b>Tizim holati:</b> Faol\n"
            "Quyidagi menyudan kerakli bo'limni tanlang 👇"
        ),
        "btn_market": "📈 Bozor Tahlili",
        "btn_ai": "🤖 AI Tahlil",
        "btn_settings": "⚙️ Sozlamalar",
        "btn_change_lang": "🌐 Tilni o'zgartirish",
        "btn_refresh": "🔄 Sessiyani yangilash",
        "btn_alerts_menu": "🔔 Alertlar",
        "btn_support": "👨‍💻 Support",
        "calc_msg": "⏳ <b>🤖 JETT AI bozor ko'rsatkichlarini skanerlamoqda va tahlil qilmoqda...</b>",
        "refresh_token_msg": "🔄 <b>Yangi birja sessiyasi ochilmoqda...</b>",
        "no_trade": "Bugun bozorda savdo bo'lmadi yoki ma'lumot topilmadi.",
        "market_title": "📈 <b>FOND BOZORI XULOSASI</b>",
        "status_lbl": "<i>Holat:</i>",
        "sec_stocks": "🏛 <b>1. AKSIYALAR BO'LIMI</b>",
        "sec_bonds": "📜 <b>2. OBLIGATSIYALAR BO'LIMI</b>",
        "lbl_total": "Jami aksiyalar",
        "lbl_total_bonds": "Jami obligatsiyalar",
        "lbl_active": "Bugun savdo qilingan",
        "lbl_turnover": "Kunlik aylanma",
        "lbl_volume": "Savdo hajmi",
        "lbl_mvp": "Bugungi bozor drayveri",
        "lbl_sentiment": "Bozor yo'nalishi",
        "lbl_gainers": "O'sayotgan",
        "lbl_losers": "Pasayayotgan",
        "lbl_neutrals": "O'zgarmagan",
        "ai_prompt": "💡 <i>AI bozor skaneri va imkoniyatlar klasterini ko'rish uchun:</i>",
        "btn_run_ai_market": "🤖 AI Tahlilni Ishga Tushirish",
        "settings_title": "⚙️ <b>TIZIM VA PROFIL SOZLAMALARI</b>",
        "lbl_user_id": "Foydalanuvchi ID",
        "lbl_lang_name": "Interfeys tili",
        "lang_val": "🇺🇿 O'zbekcha",
        "lbl_live_mon": "LIVE MONITORING",
        "lbl_jett_session": "Jett Session",
        "lbl_alerts": "XABARNOMALAR (ALERTLAR)",
        "lbl_market_alerts": "📈 Narx / Bozor signallari",
        "lbl_fact_alerts": "📢 Muhim korporativ faktlar",
        "lbl_tpsl_alerts": "🎯 TP / SL signallari",
        "alerts_sub_title": "🔔 <b>XABARNOMALAR SOZLAMALARI</b>",
        "alerts_sub_desc": "Quyidagi tugmalar orqali xabarnoma turlarini mustaqil boshqaring:",
        "lbl_active_str": "Faol",
        "lbl_waiting_str": "Kutilmoqda",
        "lbl_enabled_str": "🟢 Yoqilgan",
        "lbl_disabled_str": "🔴 O'chirilgan",
        "units_pcs": "ta",
        "units_vol": "dona",
        "ai_processing": "⚡️ <b>JETT AI: Bozor tahlil qilinmoqda va optimal imkoniyatlar klasterlanmoqda</b>",
        "ai_req_market": "Avval bozor ma'lumotlarini yuklash uchun <b>📈 Bozor Tahlili</b> tugmasini bosing.",
        "ai_prep_title": "🤖 <b>JETT RADAR — AI Bozor Skaneri</b>",
        "ai_prep_ready": "⚡ <b>JETT birja ma'lumotlari tahlilga tayyorlandi.</b>",
        "ai_prep_blocks": (
            "Skaner 4 ta asosiy tahliliy blok bo'yicha ishlaydi:\n"
            "• 🔎 <b>Opportunities</b> (Hidden Gems, Value, Growth, Income, Giants)\n"
            "• 📊 <b>Market Activity</b> (Smart Money, Momentum, Breakout, Catalyst)\n"
            "• ⚖️ <b>Decision</b> (Best Risk/Reward, JETT Top Picks)\n"
            "• 🛡️ <b>Risk</b> (Defensive Picks, High Risk / Potential)"
        ),
        "ai_prep_call": "💡 <i>Skanerni ishga tushirish uchun quyidagi tugmani bosing:</i>",
        "btn_start_ai_scan": "⚡️ AI Skanerni Ishga Tushirish",
        "btn_back": "« Orqaga",
        "btn_back_hub": "« 🏠 Radar Asosiy Menyu",
        "btn_back_pillar": "« Blok ro'yxatiga qaytish",
        "btn_targets": "🎯 Savdo Rejasi (Execution Plan)",
        "btn_json_download": "📥 JSON yuklab olish",
        "cat_select_prompt": "Kerakli tahliliy toifani tanlang 👇",
        "no_assets_category": "<i>Hozirgi bozor ma'lumotlari asosida ushbu toifaga mos yetarlicha kuchli aktiv aniqlanmadi.</i>\n\n",
        "click_to_research": "━━━━━━━━━━━━━━━━━━━━━━\n<i>Aktiv ustiga bosib chuqur individual tadqiqotni oching:</i>",
        "targets_title": "🎯 <b>SAVDO REJASI ({name})</b>",
        "targets_empty": "<i>Ushbu toifada aktivlar mavjud emas.</i>",
        "targets_disclaimer": "━━━━━━━━━━━━━━━━━━━━━━\n⚠️ <i>Pozitsiyani boshqarishda risk-menejment qoidalariga amal qiling.</i>",
        "tf_default": "1–3 savdo kuni",
        "lbl_current": "Joriy",
        "lbl_entry": "Kirish zonasi (Entry)",
        "lbl_exit": "Chiqish zonasi (Target / TP)",
        "lbl_sl": "Risk limiti (Stop-Loss)",
        "btn_back_cat": "« {name} ro'yxatiga qaytish",
        "scan_started_toast": "AI tahlil boshlandi...",
        "toast_updated": "Yangilandi ✅",
        "toast_refreshing": "Sessiya yangilanmoqda...",
        "toast_downloading": "Yuklanmoqda...",
        "err_no_access": "⛔️ Ruxsat berilmagan.",
        "json_caption": "📄 <b>JETT Radar — Skaner Natijalari (JSON)</b>",
        "choose_lang_title": "🌐 <b>Tilni tanlang / Выберите язык интерфейса:</b>",
        "lang_switched": "✅ Til o'zbekchaga o'zgartirildi.",
        "radar_ready_summary": "<b>JETT Radar AI Bozor Tahlili tayyor.</b>"
    },
    "ru": {
        "welcome": (
            "📊 <b>JETT RADAR — Алгоритмическая биржевая аналитика</b>\n\n"
            "Институциональный скоринг и платформа AI-анализа акций и облигаций "
            "на платформе JETT.UZ в режиме реального времени.\n\n"
            "🟢 <b>Статус системы:</b> Активен\n"
            "Выберите интересующий раздел из меню ниже 👇"
        ),
        "btn_market": "📈 Анализ рынка",
        "btn_ai": "🤖 AI Анализ",
        "btn_settings": "⚙️ Настройки",
        "btn_change_lang": "🌐 Сменить язык",
        "btn_refresh": "🔄 Обновить сессию",
        "btn_alerts_menu": "🔔 Оповещения",
        "btn_support": "👨‍💻 Поддержка",
        "calc_msg": "⏳ <b>Расчет данных фондового рынка...</b>",
        "refresh_token_msg": "🔄 <b>Открытие новой биржевой сессии...</b>",
        "no_trade": "Сегодня торгов на рынке не было или данные не найдены.",
        "market_title": "📈 <b>ИТОГИ ФОНДОВОГО РЫНКА</b>",
        "status_lbl": "<i>Статус:</i>",
        "sec_stocks": "🏛 <b>1. РАЗДЕЛ АКЦИЙ</b>",
        "sec_bonds": "📜 <b>2. РАЗДЕЛ ОБЛИГАЦИЙ</b>",
        "lbl_total": "Всего акций",
        "lbl_total_bonds": "Всего облигаций",
        "lbl_active": "Торговалось сегодня",
        "lbl_turnover": "Дневной оборот",
        "lbl_volume": "Объем торгов",
        "lbl_mvp": "Драйвер рынка сегодня",
        "lbl_sentiment": "Настроение рынка",
        "lbl_gainers": "Растущие",
        "lbl_losers": "Падающие",
        "lbl_neutrals": "Без изменений",
        "ai_prompt": "💡 <i>Чтобы увидеть AI-сканер рынка и кластер возможностей:</i>",
        "btn_run_ai_market": "🤖 Запустить AI Анализ",
        "settings_title": "⚙️ <b>НАСТРОЙКИ СИСТЕМЫ И ПРОФИЛЯ</b>",
        "lbl_user_id": "ID пользователя",
        "lbl_lang_name": "Язык интерфейса",
        "lang_val": "🇷🇺 Русский",
        "lbl_live_mon": "LIVE МОНИТОРИНГ",
        "lbl_jett_session": "Сессия Jett",
        "lbl_alerts": "ОПОВЕЩЕНИЯ (АЛЕРТЫ)",
        "lbl_market_alerts": "📈 Сигналы рынка / цен",
        "lbl_fact_alerts": "📢 Корпоративные факты",
        "alerts_sub_title": "🔔 <b>НАСТРОЙКИ ОПОВЕЩЕНИЙ</b>",
        "alerts_sub_desc": "Управляйте типами уведомлений с помощью кнопок ниже:",
        "lbl_active_str": "Активен",
        "lbl_waiting_str": "Ожидание",
        "lbl_enabled_str": "🟢 Включено",
        "lbl_disabled_str": "🔴 Отключено",
        "units_pcs": "шт",
        "units_vol": "шт",
        "ai_processing": "⚡️ <b>JETT AI: Рынок анализируется, формируются оптимальные кластеры возможностей...</b>",
        "ai_req_market": "Сначала нажмите <b>📈 Анализ рынка</b> для загрузки рыночных данных.",
        "ai_prep_title": "🤖 <b>JETT RADAR — AI Сканер рынка</b>",
        "ai_prep_ready": "⚡ <b>Биржевые данные JETT подготовлены к анализу.</b>",
        "ai_prep_blocks": (
            "Сканер работает по 4 ключевым аналитическим блокам:\n"
            "• 🔎 <b>Opportunities</b> (Hidden Gems, Value, Growth, Income, Giants)\n"
            "• 📊 <b>Market Activity</b> (Smart Money, Momentum, Breakout, Catalyst)\n"
            "• ⚖️ <b>Decision</b> (Best Risk/Reward, JETT Top Picks)\n"
            "• 🛡️ <b>Risk</b> (Defensive Picks, High Risk / Potential)"
        ),
        "ai_prep_call": "💡 <i>Нажмите кнопку ниже для запуска сканера:</i>",
        "btn_start_ai_scan": "⚡️ Запустить AI Сканер",
        "btn_back": "« Назад",
        "btn_back_hub": "« 🏠 Главное меню Радара",
        "btn_back_pillar": "« Вернуться к списку блока",
        "btn_targets": "🎯 Торговый план (Execution Plan)",
        "btn_json_download": "📥 Скачать JSON",
        "cat_select_prompt": "Выберите нужную аналитическую категорию 👇",
        "no_assets_category": "<i>На основе текущих рыночных данных подходящих активов в этой категории не найдено.</i>\n\n",
        "click_to_research": "━━━━━━━━━━━━━━━━━━━━━━\n<i>Нажмите на актив для подробного индивидуального анализа:</i>",
        "targets_title": "🎯 <b>ТОРГОВЫЙ ПЛАН ({name})</b>",
        "targets_empty": "<i>В данной категории активы отсутствуют.</i>",
        "targets_disclaimer": "━━━━━━━━━━━━━━━━━━━━━━\n⚠️ <i>Соблюдайте правила риск-менеджмента при открытии и ведении позиции.</i>",
        "tf_default": "1–3 торговых дня",
        "lbl_current": "Текущая",
        "lbl_entry": "Зона входа (Entry)",
        "lbl_exit": "Зона выхода (Target / TP)",
        "lbl_sl": "Лимит риска (Stop-Loss)",
        "btn_back_cat": "« Вернуться к {name}",
        "scan_started_toast": "AI анализ начат...",
        "toast_updated": "Обновлено ✅",
        "toast_refreshing": "Обновление сессии...",
        "toast_downloading": "Загрузка...",
        "err_no_access": "⛔️ Доступ запрещен.",
        "json_caption": "📄 <b>JETT Radar — Результаты сканера (JSON)</b>",
        "choose_lang_title": "🌐 <b>Tilni tanlang / Выберите язык интерфейса:</b>",
        "lang_switched": "✅ Язык интерфейса изменен на русский.",
        "radar_ready_summary": "<b>JETT Radar: AI Анализ рынка готов.</b>"
    }
}

def get_main_keyboard(lang="uz", user_id=None):
    from telebot import types
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=False)
    t = TEXTS.get(lang, TEXTS.get("uz", {}))
    b_market = t.get("btn_market", "📈 Bozor Tahlili")
    b_ai = t.get("btn_ai", "🤖 AI Tahlil")
    b_settings = t.get("btn_settings", "⚙️ Sozlamalar")
    
    # 1-qator: Asosiy tahlillar
    kb.row(types.KeyboardButton(b_market), types.KeyboardButton(b_ai))
    
    # 2-qator: Admin uchun hisobot va performance tugmalari
    ADMIN_ID = 326567358
    uid = getattr(user_id, "id", user_id)
    b_signals = "📡 Signallar" if lang == "uz" else "📡 Сигналы"
    if uid and str(uid) == str(ADMIN_ID):
        kb.row(types.KeyboardButton("📊 Hisobot"), types.KeyboardButton("📈 Performance"))
        kb.row(types.KeyboardButton(b_signals), types.KeyboardButton(b_settings))
    else:
        kb.row(types.KeyboardButton(b_signals), types.KeyboardButton(b_settings))
    
    # 3-qator: Sozlamalar eng pastda
    return kb


def load_tpsl_alerts():
    import os, json
    if os.path.exists(TPSL_ALERTS_FILE):
        try:
            with open(TPSL_ALERTS_FILE, "r", encoding="utf-8") as f:
                return {int(k): v for k, v in json.load(f).items()}
        except Exception:
            return {}
    return {}

def save_tpsl_alerts():
    import json
    try:
        with open(TPSL_ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_tpsl_alerts, f)
    except Exception:
        pass

user_tpsl_alerts = load_tpsl_alerts()

def build_settings_view(chat_id):
    lang = get_lang(chat_id)
    t = TEXTS.get(lang, TEXTS["uz"])

    # Foydalanuvchi ismini aniqlash
    name = ""
    try:
        c = bot.get_chat(chat_id)
        parts = [p for p in [getattr(c, "first_name", None), getattr(c, "last_name", None)] if p]
        name = " ".join(parts).strip()
        if not name and getattr(c, "username", None):
            name = f"@{c.username}"
    except Exception:
        pass

    if not name:
        name = "Shovkatjon Usmonov" if chat_id in [326567358, OWNER_CHAT_ID] else ("Foydalanuvchi" if lang == "uz" else "Пользователь")

    from datetime import datetime
    sync_time = datetime.now().strftime("%H:%M")
    sess_status = "🟢 Faol" if lang == "uz" else "🟢 Активен"
    on_txt = "🟢 Yoqilgan" if lang == "uz" else "🟢 Включено"
    off_txt = "🔴 O'chirilgan" if lang == "uz" else "🔴 Отключено"

    def _get_pref(d, cid):
        if not isinstance(d, dict): return True
        return d.get(cid, d.get(str(cid), True))
    m_state = on_txt if _get_pref(user_market_alerts, chat_id) else off_txt
    f_state = on_txt if _get_pref(user_fact_alerts, chat_id) else off_txt
    tp_state = on_txt if _get_pref(user_tpsl_alerts, chat_id) else off_txt

    lang_display = "🇺🇿 O'zbekcha" if lang == "uz" else "🇷🇺 Русский"

    if lang == "uz":
        text = (
            "⚙️ <b>TIZIM VA PROFIL SOZLAMALARI</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Foydalanuvchi:</b> {name}\n"
            f"🆔 <b>Foydalanuvchi ID:</b> <code>{chat_id}</code>\n"
            f"🌐 <b>Interfeys tili:</b> {lang_display}\n\n"
            "📊 <b>LIVE MONITORING</b>\n"
            f"• Jett Sessiyasi: {sess_status} <i>(sync: {sync_time})</i>\n\n"
            "🔔 <b>OGOHLANTIRISHLAR (ALERTLAR)</b>\n"
            f"• 📈 Bozor / narx signallari: {m_state}\n"
            f"• 📢 Korporativ faktlar: {f_state}\n"
            f"• 🎯 TP / SL signallari: {tp_state}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        text = (
            "⚙️ <b>НАСТРОЙКИ СИСТЕМЫ И ПРОФИЛЯ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Пользователь:</b> {name}\n"
            f"🆔 <b>ID пользователя:</b> <code>{chat_id}</code>\n"
            f"🌐 <b>Язык интерфейса:</b> {lang_display}\n\n"
            "📊 <b>LIVE МОНИТОРИНГ</b>\n"
            f"• Сессия Jett: {sess_status} <i>(sync: {sync_time})</i>\n\n"
            "🔔 <b>ОПОВЕЩЕНИЯ (АЛЕРТЫ)</b>\n"
            f"• 📈 Сигналы рынка / цен: {m_state}\n"
            f"• 📢 Корпоративные факты: {f_state}\n"
            f"• 🎯 Сигналы TP / SL: {tp_state}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )

    markup = InlineKeyboardMarkup(row_width=2)
    b_lang = InlineKeyboardButton(t["btn_change_lang"], callback_data="choose_lang")
    b_alerts = InlineKeyboardButton(t["btn_alerts_menu"], callback_data="open_alerts_menu")
    b_refresh = InlineKeyboardButton(t["btn_refresh"], callback_data="refresh_jett_session")
    b_support = InlineKeyboardButton(t["btn_support"], url="https://t.me/shovkat_u")

    markup.add(b_lang, b_alerts)
    markup.add(b_refresh, b_support)
    return text, markup


def build_alerts_menu_view(chat_id):
    lang = get_lang(chat_id)
    t = TEXTS.get(lang, TEXTS["uz"])

    def _get_pref_m(d, cid):
        if not isinstance(d, dict): return True
        return d.get(cid, d.get(str(cid), True))
    is_market_on = _get_pref_m(user_market_alerts, chat_id)
    is_fact_on = _get_pref_m(user_fact_alerts, chat_id)
    is_tpsl_on = _get_pref_m(user_tpsl_alerts, chat_id)

    m_icon = "🟢" if is_market_on else "🔴"
    f_icon = "🟢" if is_fact_on else "🔴"
    t_icon = "🟢" if is_tpsl_on else "🔴"

    status_market_txt = t["lbl_enabled_str"] if is_market_on else t["lbl_disabled_str"]
    status_fact_txt = t["lbl_enabled_str"] if is_fact_on else t["lbl_disabled_str"]
    status_tpsl_txt = t["lbl_enabled_str"] if is_tpsl_on else t["lbl_disabled_str"]
    lbl_tpsl = t.get("lbl_tpsl_alerts", "🎯 TP / SL signallari")

    lines = [
        t["alerts_sub_title"],
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        t["alerts_sub_desc"],
        "",
        f"• {t['lbl_market_alerts']}: {status_market_txt}",
        f"• {t['lbl_fact_alerts']}: {status_fact_txt}",
        f"• {lbl_tpsl}: {status_tpsl_txt}",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    text = chr(10).join(lines)

    markup = InlineKeyboardMarkup(row_width=1)
    btn_m = InlineKeyboardButton(f"{m_icon} {t['lbl_market_alerts']}", callback_data="toggle_sub_market_alert")
    btn_f = InlineKeyboardButton(f"{f_icon} {t['lbl_fact_alerts']}", callback_data="toggle_sub_fact_alert")
    btn_t = InlineKeyboardButton(f"{t_icon} {lbl_tpsl}", callback_data="toggle_sub_tpsl_alert")
    btn_back = InlineKeyboardButton(t["btn_back"], callback_data="back_to_settings")

    markup.add(btn_m, btn_f, btn_t, btn_back)
    return text, markup




import datetime

def generate_daily_admin_report():
    tz_uzb = datetime.timezone(datetime.timedelta(hours=5))
    today_str = datetime.datetime.now(tz_uzb).strftime("%Y-%m-%d")
    today_like = f"{today_str}%"

    con = sqlite3.connect("/home/ubuntu/jett_data/jett_radar.db")
    cur = con.cursor()

    # 1. Shamlar sanog'i (bugungi real savdolar, savdo bo'lmasa oxirgi seans ko'rsatiladi)
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT stock_id) FROM ohlcv WHERE candle_time LIKE ? AND volume > 0", (today_like,))
    ohlcv_row = cur.fetchone()
    today_candles = ohlcv_row[0] if ohlcv_row else 0
    today_stocks = ohlcv_row[1] if ohlcv_row else 0

    if today_candles > 0:
        ohlcv_text = f"Saqlangan yangi shamlar: <b>{today_candles} ta</b> ({today_stocks} ta instrument)"
    else:
        last_date = cur.execute("SELECT substr(candle_time, 1, 10) FROM ohlcv WHERE volume > 0 ORDER BY candle_time DESC LIMIT 1").fetchone()
        last_cnt, last_stk = 0, 0
        last_d_str = "-"
        if last_date and last_date[0]:
            last_d_str = last_date[0]
            fb_row = cur.execute("SELECT COUNT(*), COUNT(DISTINCT stock_id) FROM ohlcv WHERE candle_time LIKE ? AND volume > 0", (f"{last_d_str}%",)).fetchone()
            if fb_row:
                last_cnt, last_stk = fb_row[0], fb_row[1]
        ohlcv_text = f"Bugungi yangi shamlar: <b>0 ta</b> <i>(Oxirgi seans: {last_d_str} — {last_cnt} ta)</i>" 

    # 2. Yangi faktlar sanog'i (bugun bo'lmasa, oxirgi 24 soatlik faktlar)
    cur.execute("SELECT COUNT(*) FROM facts WHERE created_at LIKE ? OR pub_date LIKE ? OR added_on LIKE ?", (today_like, today_like, today_like))
    facts_cnt = cur.fetchone()[0]
    facts_today = facts_cnt
    if facts_cnt == 0:
        facts_cnt = cur.execute("SELECT COUNT(*) FROM facts WHERE pub_date >= datetime('now', '-24 hours') OR added_on >= datetime('now', '-24 hours')").fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM facts")
    total_facts = cur.fetchone()[0]
    facts_line = f"Bugun qo'shilgan yangi faktlar: <b>{facts_today} ta</b>" + (f" <i>(oxirgi 24 soatda: {facts_cnt} ta)</i>" if facts_today == 0 and facts_cnt else "")

    # 3. Kvant signallar (jett_signals.db -> ai_signals_history bazasidan to'liq hisoblash)
    try:
        import sqlite3 as _s3
        with _s3.connect('/home/ubuntu/bot/jett_signals.db') as _s_conn:
            alerts_cnt = _s_conn.cursor().execute(
                "SELECT COUNT(*) FROM ai_signals_history WHERE signal_date LIKE ?", 
                (today_like,)
            ).fetchone()[0]
    except Exception as _e:
        print(f"[DAILY_REPORT_SIG_ERR] {_e}")
        alerts_cnt = 0

    con.close()

    report = (
        f"<b>📊 KUNLIK BIRJA VA TIZIM HISOBOTI</b>\n"
        f"<i>📅 Sana: {today_str}</i>\n\n"
        f"<b>1. Bozor ma'lumotlari:</b>\n"
        f"  • {ohlcv_text}\n\n"
        f"<b>2. Korporativ faktlar:</b>\n"
        f"  • {facts_line}\n"
        f"  • Jami faktlar bazasi: <b>{total_facts:,} ta</b>\n\n"
        f"<b>3. Kvant signallar & TP/SL:</b>\n"
        f"  • Bugungi yangi signallar: <b>{alerts_cnt} ta</b>\n"
        f"  • TP / SL nazorati: <code>Faol kuzatuvda</code>\n\n"
        f"<b>4. Tizim holati:</b>\n"
        f"  • AI Dvigateli (Gemini): <code>Onlayn</code>\n"
        f"  • Baza holati: <code>Faol va barqaror</code>"
    )
    return report

def generate_performance_report():
    report = (
        "<b>📈 KVANT TP/SL DASTURI PERFORMANCE STATISTIKASI</b>\n\n"
        "<b>Qoidalar va Parametrlar:</b>\n"
        "• Model: <code>Quant TP/SL Engine v1.0</code>\n"
        "• Stop-Loss qoidasi: <code>min(struktura_tayanchi, Narx - 1.5*ATR)</code>\n"
        "• Take-Profit qoidasi: <code>Kirish + 1.5 * Risk Masofasi</code>\n"
        "• R:R nisbati: <b>>= 1:1.5</b> (Minimal risk-mukofot kafolati)\n"
        "• Sun'iy intellekt integratsiyasi: Gemini faqat hisoblangan darajalarni talqin qiladi\n\n"
        "<b>Hozirgi holat:</b> Barcha yangi signallar matematik cheklovlarga 100% muvofiq filtrlanmoqda."
    )
    return report

def _sig_kb(kind, page, pages):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(("• " if kind == "open" else "") + "Faol", callback_data="sigl_open_1"),
        InlineKeyboardButton(("• " if kind == "closed" else "") + "Yopilgan", callback_data="sigl_closed_1"),
    )
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀", callback_data=f"sigl_{kind}_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data="sigl_noop"))
    if page < pages:
        nav.append(InlineKeyboardButton("▶", callback_data=f"sigl_{kind}_{page+1}"))
    kb.row(*nav)
    return kb


@bot.message_handler(func=lambda m: m.text and m.text.strip() in ("📡 Signallar", "📡 Сигналы"))
def handle_signals_list_btn(message):
    try:
        text, pages = signals_db.get_signals_list("open", 1)
        bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=_sig_kb("open", 1, pages))
    except Exception as ex:
        print(f"[SIGNALLAR] xato: {ex}")
        bot.send_message(message.chat.id, "⚠️ Signallar ro‘yxatini yuklab bo‘lmadi.")


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("sigl_"))
def handle_signals_list_cb(call):
    try:
        if call.data == "sigl_noop":
            bot.answer_callback_query(call.id)
            return
        _, kind, page = call.data.split("_")
        text, pages = signals_db.get_signals_list(kind, int(page))
        page = min(max(1, int(page)), pages)
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode="HTML", reply_markup=_sig_kb(kind, page, pages))
        bot.answer_callback_query(call.id)
    except Exception as ex:
        if "message is not modified" not in str(ex):
            print(f"[SIGNALLAR] callback xato: {ex}")
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass


@bot.message_handler(commands=['performance'])
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['📈 performance', 'performance', '/performance'])
def handle_performance_btn(message):
    ADMIN_ID = 326567358
    if getattr(message, 'chat', None) and message.chat.id != ADMIN_ID:
        return
    performance_command_handler(message)


@bot.message_handler(commands=["performance", "pnl"])
def performance_command_handler(message):
    cid = message.chat.id
    try:
        lang = get_lang(cid)
    except Exception:
        lang = "uz"
    try:
        stats = signals_db.get_performance_stats(lang=lang)
        try:
            bot.send_message(cid, stats, parse_mode="HTML")
        except Exception:
            # HTML parsing xatoligi yuz bersa, teglarsiz toza matn sifatida xatosiz yuborish
            clean_text = re.sub(r'<[^>]+>', '', stats)
            bot.send_message(cid, clean_text)
    except Exception as e:
        bot.send_message(cid, f"❌ Statistika xatosi: {e}")

@bot.message_handler(commands=["start"])
def start(message):
    cid = message.chat.id
    if cid not in user_market_alerts:
        user_market_alerts[cid] = True
    if cid not in user_fact_alerts:
        user_fact_alerts[cid] = True
    if cid not in user_languages:
        user_languages[cid] = "uz"
    save_subscribers()
    lang = get_lang(cid)
    bot.send_message(cid, TEXTS[lang]["welcome"], parse_mode="HTML", reply_markup=get_main_keyboard(lang, cid))

@bot.message_handler(content_types=['text'])
def handle_text(message):
    if getattr(message, "from_user", None):
        fn = message.from_user.first_name or ""
        ln = message.from_user.last_name or ""
        uname = f"{fn} {ln}".strip() or (f"@{message.from_user.username}" if message.from_user.username else "")
        if uname:
            user_names[message.chat.id] = uname
    cid = message.chat.id
    text = message.text or ""
    if text.strip().lower() in ["📊 hisobot", "hisobot", "/admin_report", "/report"] and cid == 326567358:
        rep = generate_daily_admin_report()
        bot.send_message(cid, rep, parse_mode='HTML')
        return
    if text.strip().lower() in ["📈 performance", "performance", "/performance"] and cid == 326567358:
        performance_command_handler(message)
        return
    if text in [TEXTS["uz"]["btn_market"], TEXTS["ru"]["btn_market"]]:
        run_market_analysis(cid)
    elif text in [TEXTS["uz"]["btn_ai"], TEXTS["ru"]["btn_ai"]]:
        prepare_ai_stage(cid)
    elif text in [TEXTS["uz"]["btn_settings"], TEXTS["ru"]["btn_settings"]] or text in ["/settings", "/settings@" + bot.get_me().username]:
        status_text, markup = build_settings_view(cid)
        bot.send_message(cid, status_text, parse_mode="HTML", reply_markup=markup)

def run_market_analysis(chat_id):
    global ADMIN_TOKEN
    lang = get_lang(chat_id)
    t = TEXTS.get(lang, TEXTS["uz"])
    wait_msg = bot.send_message(chat_id, t["calc_msg"], parse_mode="HTML")

    token = ADMIN_TOKEN or fetch_fresh_token()
    market_info = market.fetch_market_data(token)

    if market_info.get("status") != "ok" or market_info.get("code") == 401:
        bot.edit_message_text(t["refresh_token_msg"], chat_id=chat_id, message_id=wait_msg.message_id, parse_mode="HTML")
        token = fetch_fresh_token(force=True)
        market_info = market.fetch_market_data(token)

    if market_info.get("status") != "ok":
        err_prefix = "❌ Xatolik yuz berdi:" if lang == "uz" else "❌ Произошла ошибка:"
        safe_send_or_edit(chat_id, f"{err_prefix}\n{market_info.get('message')}", wait_msg.message_id)
        return

    if market_info.get("is_stale"):
        safe_send_or_edit(chat_id, "⏸ <b>Bozor yopiq yoki savdo o'tkazilmadi</b>\n\n<i>Bugun birjada savdo sessiyasi bo'lmadi (dam olish yoki bayram kuni). Ko'rsatkichlar oxirgi yopilgan sessiya holatida saqlanmoqda.</i>", wait_msg.message_id)
        return

    active_stocks = market_info.get("active_stocks", [])

    active_bonds = market_info.get("active_bonds", [])
    active_all = market_info.get("active_all", [])

    if not active_all:
        safe_send_or_edit(chat_id, t["no_trade"], wait_msg.message_id)
        return

    user_sessions[chat_id] = active_all

    tz_uzb = datetime.timezone(datetime.timedelta(hours=5))
    current_time = datetime.datetime.now(tz_uzb).strftime("%d.%m.%Y, %H:%M")

    total_stocks = market_info.get("total_stocks", len(active_stocks))
    # Stats dan umumiy aylanma yoki hisoblangan aylanma
    api_total_turnover = float(market_info.get("total_turnover") or 0.0)
    stocks_turnover = sum(float(s.get("gross_trade_amount", 0) or s.get("trade_amount", 0) or 0) for s in active_stocks)
    stocks_volume = sum(int(float(s.get("volume_today", 0) or s.get("volume", 0) or 0)) for s in active_stocks)
    traded_stocks = [s for s in active_stocks if float(s.get("gross_trade_amount", 0) or s.get("trade_amount", 0) or 0) > 0 or int(float(s.get("volume_today", 0) or s.get("volume", 0) or 0)) > 0 or float(s.get("growth_prcnt_today", 0) or s.get("percent_change", 0) or 0) != 0]
    traded_stocks_cnt = len(traded_stocks)
    st_gainers = sum(1 for s in traded_stocks if float(s.get("growth_prcnt_today", 0) or s.get("percent_change", 0) or 0) > 0)
    st_losers = sum(1 for s in traded_stocks if float(s.get("growth_prcnt_today", 0) or s.get("percent_change", 0) or 0) < 0)
    st_neutrals = traded_stocks_cnt - st_gainers - st_losers
    st_base = traded_stocks_cnt if traded_stocks_cnt > 0 else 1
    st_g_pct = (st_gainers / st_base) * 100 if traded_stocks_cnt else 0
    st_l_pct = (st_losers / st_base) * 100 if traded_stocks_cnt else 0
    st_n_pct = (st_neutrals / st_base) * 100 if traded_stocks_cnt else 0
    top_stock = max(active_stocks, key=lambda x: float(x.get("gross_trade_amount", 0) or x.get("trade_amount", 0) or 0), default={})
    top_stock_ticker = top_stock.get("ticker", "N/A")

    total_bonds = market_info.get("total_bonds", len(active_bonds))
    bonds_turnover = sum(float(b.get("gross_trade_amount", 0) or b.get("trade_amount", 0) or 0) for b in active_bonds)
    overall_turnover = api_total_turnover if api_total_turnover > 0 else (stocks_turnover + bonds_turnover)
    bonds_volume = sum(int(float(b.get("volume_today", 0) or b.get("volume", 0) or 0)) for b in active_bonds)
    traded_bonds = [b for b in active_bonds if float(b.get("gross_trade_amount", 0) or b.get("trade_amount", 0) or 0) > 0 or int(float(b.get("volume_today", 0) or b.get("volume", 0) or 0)) > 0 or float(b.get("growth_prcnt_today", 0) or b.get("percent_change", 0) or 0) != 0]
    traded_bonds_cnt = len(traded_bonds)
    bd_gainers = sum(1 for b in traded_bonds if float(b.get("growth_prcnt_today", 0) or b.get("percent_change", 0) or 0) > 0)
    bd_losers = sum(1 for b in traded_bonds if float(b.get("growth_prcnt_today", 0) or b.get("percent_change", 0) or 0) < 0)
    bd_neutrals = traded_bonds_cnt - bd_gainers - bd_losers
    bd_base = traded_bonds_cnt if traded_bonds_cnt > 0 else 1
    bd_g_pct = (bd_gainers / bd_base) * 100 if traded_bonds_cnt else 0
    bd_l_pct = (bd_losers / bd_base) * 100 if traded_bonds_cnt else 0
    bd_n_pct = (bd_neutrals / bd_base) * 100 if traded_bonds_cnt else 0
    top_bond = max(active_bonds, key=lambda x: float(x.get("gross_trade_amount", 0) or x.get("trade_amount", 0) or 0), default={})
    top_bond_ticker = top_bond.get("ticker", "N/A")

    text = (
        f"{t['market_title']}\n"
        f"🕒 {t['status_lbl']} {current_time}\n"
        f"💰 <b>{'Jami bozor aylanmasi' if lang == 'uz' else 'Общий оборот рынка'}:</b> <b>{format_turnover(overall_turnover, lang)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{t['sec_stocks']}\n"
        f"• {t['lbl_total']}: <b>{total_stocks} {t['units_pcs']}</b> | {t['lbl_active']}: <b>{traded_stocks_cnt} {t['units_pcs']}</b>\n"
        f"• {t['lbl_turnover']}: <b>{format_turnover(stocks_turnover, lang)}</b>\n"
        f"• {t['lbl_volume']}: <b>{stocks_volume:,} {t['units_vol']}</b>\n"
        f"🔥 <i>{t['lbl_mvp']}:</i> <tg-spoiler><b>{top_stock_ticker}</b></tg-spoiler>\n\n"
        f"📊 <i>{t['lbl_sentiment']}:</i>\n"
        f"🟢 {t['lbl_gainers']}: {st_gainers} {t['units_pcs']} <code>{make_bar(st_g_pct)}</code> {st_g_pct:.1f}%\n"
        f"🔴 {t['lbl_losers']}: {st_losers} {t['units_pcs']} <code>{make_bar(st_l_pct)}</code> {st_l_pct:.1f}%\n"
        f"⚪️ {t['lbl_neutrals']}: {st_neutrals} {t['units_pcs']} <code>{make_bar(st_n_pct)}</code> {st_n_pct:.1f}%\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{t['sec_bonds']}\n"
        f"• {t['lbl_total_bonds']}: <b>{total_bonds} {t['units_pcs']}</b> | {t['lbl_active']}: <b>{traded_bonds_cnt} {t['units_pcs']}</b>\n"
        f"• {t['lbl_turnover']}: <b>{format_turnover(bonds_turnover, lang)}</b>\n"
        f"• {t['lbl_volume']}: <b>{bonds_volume:,} {t['units_vol']}</b>\n"
        f"🔥 <i>{t['lbl_mvp']}:</i> <tg-spoiler><b>{top_bond_ticker}</b></tg-spoiler>\n\n"
        f"📊 <i>{t['lbl_sentiment']}:</i>\n"
        f"🟢 {t['lbl_gainers']}: {bd_gainers} {t['units_pcs']} <code>{make_bar(bd_g_pct)}</code> {bd_g_pct:.1f}%\n"
        f"🔴 {t['lbl_losers']}: {bd_losers} {t['units_pcs']} <code>{make_bar(bd_l_pct)}</code> {bd_l_pct:.1f}%\n"
        f"⚪️ {t['lbl_neutrals']}: {bd_neutrals} {t['units_pcs']} <code>{make_bar(bd_n_pct)}</code> {bd_n_pct:.1f}%\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{t['ai_prompt']}"
    )

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(t["btn_run_ai_market"], callback_data="prepare_ai_stage"))
    safe_send_or_edit(chat_id, text, wait_msg.message_id, reply_markup=markup)

def prepare_ai_stage(chat_id, message_id=None):
    lang = get_lang(chat_id)
    t = TEXTS.get(lang, TEXTS["uz"])
    active_data = user_sessions.get(chat_id)
    if not active_data:
        token = ADMIN_TOKEN or fetch_fresh_token()
        market_info = market.fetch_market_data(token)
        active_data = market_info.get("active_all", [])
        if active_data:
            user_sessions[chat_id] = active_data
        else:
            bot.send_message(chat_id, f"⚠️ {t['ai_req_market']}", parse_mode="HTML")
            return

    prep_text = (
        f"{t['ai_prep_title']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{t['ai_prep_ready']}\n\n"
        f"{t['ai_prep_blocks']}\n\n"
        f"{t['ai_prep_call']}"
    )

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(t["btn_start_ai_scan"], callback_data="start_ai_scan_exec"),
        InlineKeyboardButton(t["btn_back"], callback_data="back_to_main_menu")
    )

    bot.send_message(chat_id, prep_text, parse_mode='HTML', reply_markup=markup)

def build_radar_main_hub_keyboard(radar_data, chat_id, lang="uz"):
    markup = InlineKeyboardMarkup(row_width=2)
    t = TEXTS.get(lang, TEXTS["uz"])
    
    opp_cats = PILLAR_GROUPS[lang]["grp_opps"]["cats"]
    act_cats = PILLAR_GROUPS[lang]["grp_activity"]["cats"]
    dec_cats = PILLAR_GROUPS[lang]["grp_decision"]["cats"]
    rsk_cats = PILLAR_GROUPS[lang]["grp_risk"]["cats"]

    opp_count = sum(len(radar_data.get(k, [])) for k, _ in opp_cats)
    act_count = sum(len(radar_data.get(k, [])) for k, _ in act_cats)
    dec_count = sum(len(radar_data.get(k, [])) for k, _ in dec_cats)
    rsk_count = sum(len(radar_data.get(k, [])) for k, _ in rsk_cats)

    markup.add(
        InlineKeyboardButton(f"🔎 Opportunities ({opp_count})", callback_data="open_pillar_grp_opps"),
        InlineKeyboardButton(f"📊 Market Activity ({act_count})", callback_data="open_pillar_grp_activity")
    )
    markup.add(
        InlineKeyboardButton(f"⚖️ Decision ({dec_count})", callback_data="open_pillar_grp_decision"),
        InlineKeyboardButton(f"🛡️ Risk ({rsk_count})", callback_data="open_pillar_grp_risk")
    )

    if chat_id in ADMIN_IDS or chat_id == OWNER_CHAT_ID:
        markup.add(InlineKeyboardButton(t["btn_json_download"], callback_data="download_ai_json"))

    return markup


def call_gemini_with_retry(prompt_text, max_attempts=6, wait_sec=2):
    """
    Endi bitta modelga 15 marta urinmaydi — RPM/RPD holatiga qarab
    darhol bo'sh turgan modelga o'tadi. gemini_limiter kutubxonasi
    google.genai client'ini talab qiladi; agar loyihada shu client
    global o'zgaruvchi sifatida mavjud bo'lsa (masalan `genai_client`),
    shuni ishlating.
    """
    import time
    from gemini_limiter import call_gemini_smart

    genai_client = globals().get('genai_client')
    genai_types = globals().get('genai_types')

    if genai_client is None or genai_types is None:
        # Fallback: eski google.generativeai client bilan ham ishlaydi,
        # lekin bu holda model almashtirish ishlamaydi (faqat bitta
        # model chaqiriladi) — genai_client'ni sozlash tavsiya etiladi.
        try:
            import google.generativeai as genai
            m = genai.GenerativeModel("gemini-3.1-flash-lite")
            response = m.generate_content(prompt_text)
            if response and response.text.strip():
                return response.text.strip()
        except Exception as err:
            print(f"[AI_RETRY] Fallback client xato berdi: {err}")
        return None

    for attempt in range(1, max_attempts + 1):
        result = call_gemini_smart(prompt_text, temperature=0.2, client=genai_client, types=genai_types)
        if result:
            return result
        print(f"[AI_RETRY] {attempt}-urinish: barcha modellar band yoki javobsiz, {wait_sec}s kutilmoqda...")
        time.sleep(wait_sec)

    print("[AI_RETRY] Barcha urinishlar tugadi — hozircha AI javob bera olmaydi.")
    return None


def execute_radar_analysis(chat_id, message_id=None):
    lang = get_lang(chat_id)
    t = TEXTS.get(lang, TEXTS["uz"])
    active_data = user_sessions.get(chat_id)
    if not active_data:
        token = ADMIN_TOKEN or fetch_fresh_token()
        market_info = market.fetch_market_data(token)
        if market_info.get("is_stale"):
            safe_send_or_edit(chat_id, "⏸ <b>Bozor yopiq yoki savdo o'tkazilmadi</b>\n\n<i>Bugun birjada faol savdo sessiyasi mavjud emas (dam olish yoki bayram kuni). Yangi signallar generatsiya qilinmadi.</i>", message_id)
            return
        active_data = market_info.get("active_all", [])
        user_sessions[chat_id] = active_data

    status_msg = bot.send_chat_action(chat_id, "typing")
    target_msg_id = message_id or getattr(status_msg, 'message_id', None)

    with AnimateTyping(bot, chat_id, target_msg_id, t["ai_processing"]):

        try:
            prioritized_stocks = score_engine.calculate_market_scores(active_data)
            raw_prioritized = [s["raw_data"] for s in prioritized_stocks]
        except Exception:
            raw_prioritized = active_data

        tz_uzb = datetime.timezone(datetime.timedelta(hours=5))
        scan_time = datetime.datetime.now(tz_uzb).strftime("%d.%m.%Y %H:%M")

        scan_payload = {
            "timestamp": scan_time,
            "active_stocks": raw_prioritized[:35], "active_all": active_data, "recent_facts": get_recent_corporate_facts(10),
            "lang": lang
        }

        loading_text = "⏳ 🤖 JETT AI bozor ko'rsatkichlarini skanerlamoqda va tahlil qilmoqda..." if lang == "uz" else "⏳ 🤖 JETT AI сканирует и анализирует рыночные данные..."
        sent_msg = safe_send_or_edit(chat_id, loading_text, message_id)
        if sent_msg and hasattr(sent_msg, 'message_id'):
            message_id = sent_msg.message_id

        try:
            is_admin_user = (chat_id in ADMIN_IDS or chat_id == OWNER_CHAT_ID)
            cached_data = load_global_ai_cache()

            if is_admin_user or not cached_data:
                result = ai_engine.analyze_and_scan_market(scan_payload)
                if isinstance(result, dict) and 'error' not in result:
                    save_global_ai_cache(result)
                    try:
                        signals_db.save_ai_radars_to_db(result.get('radars', {}), active_data)
                    except Exception as dbe:
                        print(f'DB Error: {dbe}')
            else:
                result = cached_data

            try:
                signals_db.update_signals_tracker(raw_prioritized)
            except Exception:
                pass
        except Exception as e:
            result = {"error": str(e)}

    if "error" in result:
        err_prefix = "❌ Xatolik:" if lang == "uz" else "❌ Ошибка:"
        safe_send_or_edit(chat_id, f"{err_prefix} {result['error']}", message_id)
        return

    user_radar_cache[chat_id] = result

    summary_text = result.get("market_overview_html", t["radar_ready_summary"])
    radar_data = result.get("radars", {})

    markup = build_radar_main_hub_keyboard(radar_data, chat_id, lang=lang)
    safe_send_or_edit(chat_id, summary_text, message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("open_pillar_"))
def open_pillar_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    grp_key = call.data.replace("open_pillar_", "")
    grp_info = PILLAR_GROUPS.get(lang, {}).get(grp_key, PILLAR_GROUPS["uz"].get(grp_key))
    if not grp_info:
        return

    radar_res = get_current_radar_data(cid)
    radar_data = radar_res.get("radars", {})

    text = f"<b>{grp_info['title']}</b>\n"
    text += f"<i>{grp_info['desc']}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += t["cat_select_prompt"]

    markup = InlineKeyboardMarkup(row_width=2)
    cat_buttons = []
    for cat_id, cat_label in grp_info["cats"]:
        count = len(radar_data.get(cat_id, []))
        cat_buttons.append(InlineKeyboardButton(f"{cat_label} ({count})", callback_data=f"radar_{cat_id}"))
    
    markup.add(*cat_buttons)
    markup.add(InlineKeyboardButton(t["btn_back_hub"], callback_data="back_to_hub"))

    safe_send_or_edit(cid, text, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "prepare_ai_stage")
def prepare_ai_stage_cb(call):
    bot.answer_callback_query(call.id)
    prepare_ai_stage(call.message.chat.id, None)

@bot.callback_query_handler(func=lambda call: call.data == "start_ai_scan_exec")
def start_ai_scan_exec_cb(call):
    lang = get_lang(call.message.chat.id)
    t = TEXTS.get(lang, TEXTS["uz"])
    bot.answer_callback_query(call.id, t["scan_started_toast"])
    threading.Thread(target=execute_radar_analysis, args=(call.message.chat.id, call.message.message_id)).start()

@bot.callback_query_handler(func=lambda call: call.data.startswith("radar_"))
def category_view_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    cat_key = call.data.replace("radar_", "")
    radar_res = get_current_radar_data(cid)
    items = radar_res.get("radars", {}).get(cat_key, [])

    cat_meta = RADAR_CATEGORIES.get(lang, {}).get(cat_key, RADAR_CATEGORIES["uz"].get(cat_key, {"title": cat_key.upper(), "pillar": "grp_opps", "desc": ""}))
    title = cat_meta["title"]
    desc = cat_meta["desc"]
    pillar_key = cat_meta.get("pillar", "grp_opps")

    text = f"<b>{title.upper()}</b>\n"
    text += f"<i>{desc}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if not items:
        text += t["no_assets_category"]
    else:
        for idx, it in enumerate(items, 1):
            score_title = it.get("score_title", "Score")
            score_val = it.get("score", 0)
            default_reason = "Bozor dinamikasi asosida" if lang == "uz" else "На основе динамики рынка"
            reasons = " • ".join(it.get("reasons", [])) or default_reason

            text += f"{idx}. <b>#{it.get('ticker')}</b> — {score_title}: <b>{score_val}/100</b>\n"
            text += f"   💡 <i>{reasons}</i>\n\n"

    text += t["click_to_research"]

    markup = InlineKeyboardMarkup(row_width=3)
    ticker_btns = [InlineKeyboardButton(f"🔍 {it['ticker']}", callback_data=f"stock_{it['ticker']}_{cat_key}") for it in items if "ticker" in it]
    if ticker_btns:
        markup.add(*ticker_btns)
    
    if items:
        markup.add(InlineKeyboardButton(t["btn_targets"], callback_data=f"targets_{cat_key}"))
    markup.add(InlineKeyboardButton(t["btn_back_pillar"], callback_data=f"open_pillar_{pillar_key}"))
    markup.add(InlineKeyboardButton(t["btn_back_hub"], callback_data="back_to_hub"))

    safe_send_or_edit(cid, text, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("targets_"))
def category_targets_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    cat_key = call.data.replace("targets_", "")
    radar_res = get_current_radar_data(cid)
    items = radar_res.get("radars", {}).get(cat_key, [])

    cat_meta = RADAR_CATEGORIES.get(lang, {}).get(cat_key, RADAR_CATEGORIES["uz"].get(cat_key, {"title": "Ro'yxat", "pillar": "grp_opps"}))
    cat_name = cat_meta["title"]
    
    text = f"{t['targets_title'].format(name=cat_name.upper())}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if not items:
        text += t["targets_empty"]
    else:
        for idx, it in enumerate(items, 1):
            cur_price = clean_uzs(it.get('current_price'))
            entry_z = clean_uzs(it.get('entry_zone') or it.get('current_price'))
            exit_z = clean_uzs(it.get('exit_zone'))
            if exit_z == "N/A":
                tp1 = it.get('target_price')
                tp2 = it.get('target_price_2')
                tp1_p = it.get('tp1_pct')
                tp2_p = it.get('tp2_pct')
                if tp1 and tp2:
                    exit_z = f"TP1: {tp1:,.0f} | TP2: {tp2:,.0f}".replace(",", " ")
                elif tp1_p and tp2_p:
                    exit_z = f"TP1: +{tp1_p}% | TP2: +{tp2_p}%"
            sl_z = clean_uzs(it.get('stop_loss') or it.get('stop_loss_zone'))
            if sl_z == "N/A" and it.get('stop_loss_pct'):
                sl_z = f"-{it.get('stop_loss_pct')}%"
            tf = it.get('timeframe', t['tf_default'])

            text += f"{idx}. <b>#{it.get('ticker')}</b> ({t['lbl_current']}: <b>{cur_price}</b>)\n"
            text += f"   ⏱ <b>Timeframe:</b> <code>{tf}</code>\n"
            text += f"   🟢 <b>{t['lbl_entry']}:</b> <code>{entry_z}</code>\n"
            text += f"   🎯 <b>{t['lbl_exit']}:</b> <code>{exit_z}</code>\n"
            text += f"   🛑 <b>{t['lbl_sl']}:</b> <code>{sl_z}</code>\n\n"

    text += t["targets_disclaimer"]

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(t["btn_back_cat"].format(name=cat_name), callback_data=f"radar_{cat_key}"),
        InlineKeyboardButton(t["btn_back_hub"], callback_data="back_to_hub")
    )
    safe_send_or_edit(cid, text, call.message.message_id, reply_markup=markup)

def process_stock_analysis_worker(cid, mid, ticker, origin_cat, lang):
    t = TEXTS.get(lang, TEXTS["uz"])
    active_data = user_sessions.get(cid) or []
    stock_info = next((s for s in active_data if s.get("ticker") == ticker), {})

    cat_meta = RADAR_CATEGORIES.get(lang, {}).get(
        origin_cat, 
        RADAR_CATEGORIES["uz"].get(origin_cat, {"title": "Kategoriya"})
    )
    cat_name = cat_meta["title"]

    try:
        report = ai_engine.analyze_individual_stock(ticker, stock_info, category_context=cat_name, lang=lang)
    # bypass legacy fallback
    except Exception as e:
        report = f"❌ Xatolik yuz berdi: {str(e)}"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(t["btn_back_cat"].format(name=cat_name), callback_data=f"radar_{origin_cat}"),
        InlineKeyboardButton(t["btn_back_hub"], callback_data="back_to_hub")
    )
    safe_send_or_edit(cid, report, mid, reply_markup=markup)

def process_catalyst_worker(cid, mid, ticker, lang):
    try:
        report = catalyst_engine.analyze_catalyst_fact(ticker, lang=lang)
    except Exception as e:
        report = f"❌ Katalizator tahlilida xatolik: {e}"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("« 🏠 Radar Asosiy Menyu", callback_data="back_to_hub"))
    safe_send_or_edit(cid, report, mid, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("stock_"))
def single_stock_analysis_handler(call):
    cid = call.message.chat.id
    mid = call.message.message_id
    lang = get_lang(cid)
    parts = call.data.split("_")
    ticker = parts[1]
    origin_cat = parts[2] if len(parts) > 2 else "hidden_gems"

    bot.answer_callback_query(call.id)

    loading_text = (
        f"⏳ <b>#{ticker}</b> bo'yicha chuqur AI tadqiqot tayyorlanmoqda...\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Kompaniya moliyaviy ko'rsatkichlari, orderbook va multiplikatorlar hisoblanmoqda. Bir necha soniya kuting...</i>"
        if lang == "uz" else
        f"⏳ Формируется глубокий AI анализ по <b>#{ticker}</b>...\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Рассчитываются показатели компании, ордербук и мультипликаторы. Пожалуйста, подождите несколько секунд...</i>"
    )
    msg_text = call.message.text or call.message.caption or ""
    is_alert = (
        origin_cat in ["catalyst", "alert"] or
        any(k in msg_text for k in [
            "BOZOR TEZKOR ALERTI", 
            "БЫСТРЫЙ РЫНОЧНЫЙ АЛЕРТ", 
            "KORPORATIV FAKT", 
            "КОРПОРАТИВНЫЙ ФАКТ", 
            "MUHIM KORPORATIV FAKT",
            "DUMP", 
            "PUMP", 
            "SMART MONEY",
            "WHALE"
        ])
    )

    try:
        bot.send_chat_action(cid, 'typing')
    except Exception:
        pass

    # Asl postdagi tugmalar yo'qolib ketmasligi uchun doimo yangi reply xabar yuboramiz
    try:
        loading_msg = bot.send_message(cid, loading_text, parse_mode="HTML", reply_to_message_id=mid)
        target_mid = loading_msg.message_id
    except Exception:
        loading_msg = bot.send_message(cid, loading_text, parse_mode="HTML")
        target_mid = loading_msg.message_id

    if origin_cat == "catalyst":
        threading.Thread(
            target=process_catalyst_worker,
            args=(cid, target_mid, ticker, lang),
            daemon=True
        ).start()
        return

    threading.Thread(
        target=process_stock_analysis_worker,
        args=(cid, target_mid, ticker, origin_cat, lang),
        daemon=True
    ).start()

@bot.callback_query_handler(func=lambda call: call.data == "back_to_hub")
def back_to_hub_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    bot.answer_callback_query(call.id)
    radar_res = get_current_radar_data(cid)
    summary_text = radar_res.get("market_overview_html", t["radar_ready_summary"])
    radar_data = radar_res.get("radars", {})
    markup = build_radar_main_hub_keyboard(radar_data, cid, lang=lang)
    safe_send_or_edit(cid, summary_text, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main_menu")
def back_to_main_cb(call):
    bot.answer_callback_query(call.id)
    bot.delete_message(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "download_ai_json")
def download_json_callback(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    if cid not in ADMIN_IDS and cid != OWNER_CHAT_ID:
        bot.answer_callback_query(call.id, t["err_no_access"], show_alert=True)
        return

    radar_res = get_current_radar_data(cid)
    json_bytes = io.BytesIO(json.dumps(radar_res, ensure_ascii=False, indent=2).encode('utf-8'))
    json_bytes.name = f"jett_radar_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json"

    bot.answer_callback_query(call.id, t["toast_downloading"])
    bot.send_document(cid, json_bytes, caption=t["json_caption"], parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "open_alerts_menu")
def open_alerts_menu_handler(call):
    cid = call.message.chat.id
    text, markup = build_alerts_menu_view(cid)
    try:
        bot.edit_message_text(text, chat_id=cid, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        bot.send_message(cid, text, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "toggle_sub_market_alert")
def toggle_sub_market_alert_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    user_market_alerts[cid] = not user_market_alerts.get(cid, True)
    save_subscribers()
    bot.answer_callback_query(call.id, t["toast_updated"])
    text, markup = build_alerts_menu_view(cid)
    try:
        bot.edit_message_text(text, chat_id=cid, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data == "toggle_sub_fact_alert")
def toggle_sub_fact_alert_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    user_fact_alerts[cid] = not user_fact_alerts.get(cid, True)
    save_subscribers()
    bot.answer_callback_query(call.id, t["toast_updated"])
    text, markup = build_alerts_menu_view(cid)
    try:
        bot.edit_message_text(text, chat_id=cid, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data == "toggle_sub_tpsl_alert")
def toggle_sub_tpsl_alert_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    user_tpsl_alerts[cid] = not user_tpsl_alerts.get(cid, True)
    save_tpsl_alerts()
    save_subscribers()
    bot.answer_callback_query(call.id, t.get("toast_updated", "Sozlama yangilandi"))
    text, markup = build_alerts_menu_view(cid)
    try:
        bot.edit_message_text(text, chat_id=cid, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data in ["change_lang_cmd", "choose_lang"])
def change_lang_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    markup = InlineKeyboardMarkup(row_width=2)
    btn_uz = InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="set_lang_uz")
    btn_ru = InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru")
    btn_back = InlineKeyboardButton(t["btn_back"], callback_data="back_to_settings")
    markup.add(btn_uz, btn_ru)
    markup.add(btn_back)
    
    try:
        bot.edit_message_text(t["choose_lang_title"], chat_id=cid, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        bot.send_message(cid, t["choose_lang_title"], parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["set_lang_uz", "set_lang_ru"])
def set_lang_handler(call):
    cid = call.message.chat.id
    selected_lang = "uz" if call.data == "set_lang_uz" else "ru"
    user_languages[cid] = selected_lang
    save_subscribers()
    t = TEXTS[selected_lang]

    bot.answer_callback_query(call.id, t["lang_switched"])

    # Eski inline menyuni tozalash
    try:
        bot.delete_message(chat_id=cid, message_id=call.message.message_id)
    except Exception:
        pass

    # Yangi tildagi asosiy pastki menyu (ReplyKeyboardMarkup) bilan xabar chiqarish
    status_text, markup = build_settings_view(cid)
    bot.send_message(
        cid,
        f"🌐 <b>{t['lang_switched']}</b>\n\n" + status_text,
        parse_mode="HTML",
        reply_markup=get_main_keyboard(selected_lang)
    )


@bot.callback_query_handler(func=lambda call: call.data == "back_to_settings")
def back_to_settings_handler(call):
    cid = call.message.chat.id
    status_text, markup = build_settings_view(cid)
    try:
        bot.edit_message_text(status_text, chat_id=cid, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data == "refresh_jett_session")
def refresh_session_handler(call):
    cid = call.message.chat.id
    lang = get_lang(cid)
    t = TEXTS.get(lang, TEXTS["uz"])
    bot.answer_callback_query(call.id, t["toast_refreshing"])
    fetch_fresh_token(force=True)
    status_text, markup = build_settings_view(cid)
    try:
        bot.edit_message_text(status_text, chat_id=cid, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass

# ==========================================
# BOZOR TEZKOR ALERTLARI (BACKGROUND MONITOR)
# ==========================================
def run_signals_alert_check(active_stocks):
    try:
        triggered = signals_db.update_signals_tracker(active_stocks)
        for ev in (triggered or []):
            try:
                pnl = ev.get("pnl", 0)
                pnl_sign = "+" if pnl > 0 else ""
                ev_type = ev.get("type", "")
                ticker = ev.get("ticker", "")
                cat = ev.get("category", "Bozor")
                ep = ev.get("entry_price", 0)
                xp = ev.get("exit_price", 0)
                tp = ev.get("target_price", 0)

                if ev_type == "TP2_HIT":
                    alert_msg = (
                        f"🚀 <b>#{ticker} bo'yicha ASOSIY MAQSAD (TP2) urildi!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏷 <b>Toifa:</b> {cat}\n"
                        f"🟢 <b>Kirish narxi:</b> {ep:,.0f} UZS\n"
                        f"🎯 <b>TP2 maqsad:</b> {tp:,.0f} UZS\n"
                        f"💰 <b>Joriy narx:</b> {xp:,.0f} UZS\n"
                        f"📈 <b>Sof foyda:</b> <b>{pnl_sign}{pnl}%</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 <i>Pozitsiya maksimal maqsadda muvaffaqiyatli yopildi.</i>"
                    )
                elif ev_type == "TP1_HIT":
                    alert_msg = (
                        f"🎯 <b>#{ticker} bo'yicha 1-MAQSAD (TP1) urildi!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏷 <b>Toifa:</b> {cat}\n"
                        f"🟢 <b>Kirish narxi:</b> {ep:,.0f} UZS\n"
                        f"🎯 <b>TP1 maqsad:</b> {tp:,.0f} UZS\n"
                        f"💰 <b>Joriy narx:</b> {xp:,.0f} UZS\n"
                        f"📈 <b>Joriy foyda:</b> <b>{pnl_sign}{pnl}%</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💡 <i>Oraliq marra olindi, asosiy maqsad (TP2) kutilmoqda.</i>"
                    )
                elif ev_type == "SL_HIT":
                    alert_msg = (
                        f"🛑 <b>#{ticker} bo'yicha Stop-Loss urildi!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏷 <b>Toifa:</b> {cat}\n"
                        f"🟢 <b>Kirish narxi:</b> {ep:,.0f} UZS\n"
                        f"🛑 <b>Chiqish narxi:</b> {xp:,.0f} UZS (SL: {tp:,.0f})\n"
                        f"📉 <b>Natija:</b> <b>{pnl_sign}{pnl}%</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ <i>Risk-menejment qoidasiga ko'ra pozitsiya yopildi.</i>"
                    )
                else:
                    print(f"[ALERT_WARN] Noma'lum hodisa turi: {ev_type}", flush=True)
                    continue

                if ev.get("late_min"):
                    alert_msg += (f"\n⏱ <i>Eslatma: narx kuzatuvi savdo vaqtida ~{int(ev['late_min'])} daqiqa "
                                  f"uzilib qolgan edi, shu sababli xabar kechikkan bo'lishi mumkin.</i>")

                recipients = set(ADMIN_IDS)
                if OWNER_CHAT_ID:
                    recipients.add(OWNER_CHAT_ID)
                recipients.update([uid for uid, is_on in user_tpsl_alerts.items() if is_on])

                sent_any = False
                for r_cid in recipients:
                    if not user_tpsl_alerts.get(r_cid, True):
                        continue
                    try:
                        bot.send_message(r_cid, alert_msg, parse_mode="HTML")
                        sent_any = True
                    except Exception as send_err:
                        print(f"[ALERT_SEND_ERR] chat={r_cid}, ticker={ticker}: {send_err}", flush=True)

                if sent_any and hasattr(signals_db, "mark_signal_alerted"):
                    signals_db.mark_signal_alerted(ev["id"], ev["flag"])
                    print(f"[ALERT_OK] #{ticker} ({ev_type}) yuborildi va {ev['flag']}=1 qilindi", flush=True)
            except Exception as item_err:
                print(f"[ALERT_ITEM_ERR] {ev}: {item_err}", flush=True)
    except Exception as sig_err:
        print(f"Signals background worker error: {sig_err}", flush=True)


# ==========================================
# AVTOMATIK AI SKANER (10:30 & 15:00)
# ==========================================
auto_ai_scan_slots = set()

def run_scheduled_ai_scan(slot_id=""):
    try:
        tz_uzb = datetime.timezone(datetime.timedelta(hours=5))
        scan_time = datetime.datetime.now(tz_uzb).strftime("%d.%m.%Y %H:%M")
        
        token = ADMIN_TOKEN or fetch_fresh_token()
        market_info = market.fetch_market_data(token)
        if market_info.get("is_stale"):
            print("[AI_SCAN] Savdosiz kun (is_stale), skaner o'tkazib yuborildi", flush=True)
            return
        active_data = market_info.get("active_all") or market_info.get("active_stocks", [])
        if not active_data:
            return

        try:
            prioritized_stocks = score_engine.calculate_market_scores(active_data)
            raw_prioritized = [s["raw_data"] for s in prioritized_stocks]
        except Exception:
            raw_prioritized = active_data

        scan_payload = {
            "timestamp": scan_time,
            "active_stocks": raw_prioritized[:35], "active_all": active_data,
            "recent_facts": get_recent_corporate_facts(10),
            "lang": "uz"
        }

        result = ai_engine.analyze_and_scan_market(scan_payload)
        if isinstance(result, dict) and "error" not in result:
            save_global_ai_cache(result)
            radars = result.get("radars", {})
            signals_db.save_ai_signals(radars, active_data)
            signals_db.update_signals_tracker(raw_prioritized)

            recipients = set(ADMIN_IDS)
            if OWNER_CHAT_ID:
                recipients.add(OWNER_CHAT_ID)

            # Xuddi skrinshotdagidek to'liq AI Radar xabi va uning interaktiv tugmalarini yuborish
            for r_cid in recipients:
                try:
                    u_lang = get_lang(r_cid)
                    t_u = TEXTS.get(u_lang, TEXTS["uz"])
                    summary_text = result.get("market_overview_html") or t_u.get("radar_ready_summary", "JETT RADAR")
                    user_radar_cache[r_cid] = result
                    markup = build_radar_main_hub_keyboard(radars, r_cid, lang=u_lang)
                    bot.send_message(r_cid, summary_text, parse_mode="HTML", reply_markup=markup)
                except Exception as send_err:
                    print(f"Error sending radar hub to {r_cid}: {send_err}")
    except Exception as e:
        print(f"❌ Scheduled AI scan xatosi: {e}")

def market_alert_worker():
    import datetime
    global seen_market_alerts, last_alert_check_day
    while True:
        try:
            tz_uzb = datetime.timezone(datetime.timedelta(hours=5))
            now = datetime.datetime.now(tz_uzb)
            today_str = now.strftime("%Y-%m-%d")

            if last_alert_check_day != today_str:
                last_alert_check_day = today_str
                seen_market_alerts.clear()
                auto_ai_scan_slots.clear()

            minute_of_day = now.hour * 60 + now.minute
            is_trading_hours = (now.weekday() < 5) and (9 * 60 + 30 <= minute_of_day <= 18 * 60 + 30)

            # Birja faol vaqtida 2 marta avto AI so'rov (10:30 va 15:00)
            if now.weekday() < 5:
                slot_id = None
                if 10 * 60 + 30 <= minute_of_day <= 11 * 60:
                    slot_id = f"{today_str}_1030"
                elif 15 * 60 <= minute_of_day <= 15 * 60 + 30:
                    slot_id = f"{today_str}_1500"

                if slot_id and slot_id not in auto_ai_scan_slots:
                    auto_ai_scan_slots.add(slot_id)
                    threading.Thread(target=run_scheduled_ai_scan, args=(slot_id,), daemon=True).start()

            if is_trading_hours:
                token = ADMIN_TOKEN or fetch_fresh_token()
                market_info = market.fetch_market_data(token)

                if market_info.get("status") != "ok" or market_info.get("code") == 401:
                    token = fetch_fresh_token(force=True)
                    market_info = market.fetch_market_data(token)

                print(f"🔄 [BOZOR] Ma’lumot olindi. Status: {market_info.get('status')}, Aktivlar: {len(market_info.get('active_stocks', []))}", flush=True)
                if (market_info.get("status") == "ok" or market_info.get("active_stocks")) and not market_info.get("is_stale"):
                    active_stocks = market_info.get("active_stocks", [])

                    for stock in active_stocks:
                        ticker = stock.get("ticker")
                        if not ticker:
                            continue

                        growth = float(stock.get("growth_prcnt_today", 0) or stock.get("percent_change", 0) or 0)
                        turnover = float(stock.get("gross_trade_amount", 0) or stock.get("trade_amount", 0) or 0)

                        # Aylanmasi 5 mln UZS dan kam bo'lgan nosuyuq bitimlarni chetlab o'tish
                        if turnover < 5_000_000:
                            continue

                        is_pump = growth >= 7.0
                        is_dump = growth <= -5.0
                        is_whale = turnover >= 300_000_000

                        if is_pump or is_dump or is_whale:
                            alert_type = "dump" if is_dump else ("pump" if is_pump else "whale")
                            alert_key = f"{ticker}_{alert_type}_{today_str}"
                            if alert_key not in seen_market_alerts:
                                seen_market_alerts.add(alert_key)

                                growth_sign = f"+{growth:.2f}%" if growth > 0 else f"{growth:.2f}%"
                                growth_icon = "📈" if growth >= 0 else "📉"

                                unique_alerts = {int(k): v for k, v in user_market_alerts.items()}
                                for uid, is_enabled in list(unique_alerts.items()):
                                    if is_enabled:
                                        u_lang = user_languages.get(uid, "uz")
                                        if is_dump:
                                            action_reason = "🔻 KESKIN TUSHISH (DUMP)" if u_lang == "uz" else "🔻 РЕЗКОЕ ПАДЕНИЕ (DUMP)"
                                        elif is_pump:
                                            action_reason = "🚀 KESKIN O'SISH (PUMP)" if u_lang == "uz" else "🚀 РЕЗКИЙ РОСТ (PUMP)"
                                        else:
                                            action_reason = "🐋 YIRIK OQIM (SMART MONEY)" if u_lang == "uz" else "🐋 ПРИТОК КАПИТАЛА (SMART MONEY)"

                                        if u_lang == "uz":
                                            msg = (
                                                f"🚨 <b>BOZOR TEZKOR ALERTI</b>\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"🎯 <b>Aktiv:</b> #{ticker}\n"
                                                f"📊 <b>Signal:</b> {action_reason}\n"
                                                f"{growth_icon} <b>Kunlik o'zgarish:</b> {growth_sign}\n"
                                                f"💰 <b>Aylanma:</b> {format_turnover(turnover, 'uz')}\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"⚡️ <i>Aktivda g'ayritabiiy faollik aniqlandi.</i>"
                                            )
                                            btn_text = f"🔍 #{ticker} AI Tahlil"
                                        else:
                                            msg = (
                                                f"🚨 <b>БЫСТРЫЙ РЫНОЧНЫЙ АЛЕРТ</b>\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"🎯 <b>Актив:</b> #{ticker}\n"
                                                f"📊 <b>Сигнал:</b> {action_reason}\n"
                                                f"{growth_icon} <b>Дневное изменение:</b> {growth_sign}\n"
                                                f"💰 <b>Оборот:</b> {format_turnover(turnover, 'ru')}\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"⚡️ <i>Зафиксирована повышенная рыночная активность.</i>"
                                            )
                                            btn_text = f"🔍 AI Анализ #{ticker}"

                                        alert_markup = InlineKeyboardMarkup()
                                        alert_markup.add(InlineKeyboardButton(btn_text, callback_data=f"stock_{ticker}_momentum"))

                                        try:
                                            print(f"🔔 [SERVER-LOG] Alert yuborildi: #{ticker} -> chat_id: {uid}", flush=True)
                                            with open("/home/ubuntu/bot/alert_debug.log", "a") as f_log:
                                                f_log.write(f"[{datetime.datetime.now()}] Alert: #{ticker} | UID: {uid}\n")
                                            bot.send_message(uid, msg, parse_mode="HTML", reply_markup=alert_markup)
                                            print(f"🚀 [ALERT YUBORILDI] #{ticker} -> {uid}", flush=True)
                                            time.sleep(0.05)
                                        except Exception:
                                            pass

            time.sleep(300)
        except Exception as e:
            import traceback
            print(f"❌ Alert tizimi xatosi: {e}", flush=True)
            traceback.print_exc()
            time.sleep(60)

# ==========================================
# MUHIM FAKTLAR ALERTI (DATABASE MONITOR)
# ==========================================
def facts_alert_worker():
    """jett_radar.db dagi yangi faktlarni kuzatadi va bot tiliga mos OpenInfo PDF havolasi bilan yuboradi"""
    db_paths = [
        "/home/ubuntu/jett_data/jett_radar.db",
        "jett_radar.db"
    ]
    
    while True:
        try:
            target_db = next((p for p in db_paths if os.path.exists(p)), None)
            if target_db:
                conn = sqlite3.connect(target_db, timeout=15)
                cur = conn.cursor()

                cur.execute("CREATE TABLE IF NOT EXISTS bot_alert_state (key TEXT PRIMARY KEY, value INTEGER, updated_at TEXT)")
                cur.execute("SELECT value FROM bot_alert_state WHERE key = 'last_alerted_fact_id'")
                state_row = cur.fetchone()

                if state_row is None:
                    cur.execute("SELECT MAX(fact_id) FROM facts")
                    max_fid = cur.fetchone()[0] or 0
                    cur.execute("INSERT OR REPLACE INTO bot_alert_state (key, value, updated_at) VALUES ('last_alerted_fact_id', ?, datetime('now'))", (max_fid,))
                    conn.commit()
                else:
                    last_fid = state_row[0]
                    query = """
                        SELECT 
                            f.fact_id, 
                            f.fact_number, 
                            f.fact_title_uz, 
                            f.openinfo_id,
                            f.pdf_url,
                            COALESCE(f.created_at, f.added_on) AS f_datetime,
                            f.issuer_id,
                            COALESCE(m.short_name, m.full_name, 'Emitent') AS comp_name,
                            (SELECT ticker FROM instruments WHERE issuer_id = f.issuer_id AND ticker IS NOT NULL LIMIT 1) AS ticker
                        FROM facts f
                        LEFT JOIN openinfo_mapping m ON f.issuer_id = m.issuer_id
                        WHERE f.fact_id > ?
                        ORDER BY f.fact_id ASC
                        LIMIT 10
                    """
                    cur.execute(query, (last_fid,))
                    new_facts = cur.fetchall()

                    for row in new_facts:
                        f_id, f_num, f_title, f_openinfo_id, f_pdf_url, f_date, i_id, comp_name, ticker = row
                        
                        clean_comp = re.sub(r'[\s"“«”»]+', ' ', comp_name).strip() if comp_name else "Emitent"
                        clean_title = re.sub(r'[\s]+', ' ', f_title).strip() if f_title else f"Muhim fakt №{f_num}"
                        # Openinfoga tushgan aniq vaqtni Toshkent vaqti (UTC+5) bo'yicha hisoblash
                        date_str = ""
                        if f_date:
                            raw_dt = str(f_date).replace("T", " ")[:19]
                            try:
                                from datetime import datetime, timedelta
                                dt_obj = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S")
                                # Agar soat 00:00:00 bo'lmasa, demak UTC vaqtida saqlangan -> +5 soat Toshkent vaqti
                                if not (dt_obj.hour == 0 and dt_obj.minute == 0 and dt_obj.second == 0):
                                    dt_tashkent = dt_obj + timedelta(hours=5)
                                    date_str = dt_tashkent.strftime("%d.%m.%Y %H:%M")
                                else:
                                    date_str = dt_obj.strftime("%d.%m.%Y")
                            except Exception:
                                date_str = raw_dt

                        for uid, is_enabled in list(user_fact_alerts.items()):
                            if is_enabled:
                                u_lang = user_languages.get(uid, "uz")
                                
                                if f_openinfo_id:
                                    pdf_link = f"https://openinfo.uz/{u_lang}/facts/to_pdf/{f_openinfo_id}"
                                elif not f_pdf_url or not str(f_pdf_url).startswith("http"):
                                    pdf_link = f"https://openinfo.uz/{u_lang}/facts/"
                                else:
                                    pdf_link = str(f_pdf_url).replace("/ru/", f"/{u_lang}/").replace("/uz/", f"/{u_lang}/")

                                if u_lang == "ru":
                                    msg = (
                                        f"📢 <b>СУЩЕСТВЕННЫЙ КОРПОРАТИВНЫЙ ФАКТ</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"🏛 <b>Эмитент:</b> <b>{clean_comp}</b>" + (f" (<code>#{ticker}</code>)" if ticker else "") + "\n"
                                        f"📋 <b>Номер факта:</b> №{f_num}\n"
                                        f"📑 <b>Тема:</b> {clean_title}\n"
                                        f"🕒 <b>Дата:</b> {date_str}\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📄 <a href=\"{pdf_link}\">Открыть документ (PDF)</a>"
                                    )
                                    btn_pdf = "📄 Открыть PDF"
                                    btn_ai = f"🔍 AI Анализ #{ticker}" if ticker else None
                                else:
                                    msg = (
                                        f"📢 <b>MUHIM KORPORATIV FAKT</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"🏛 <b>Emitent:</b> <b>{clean_comp}</b>" + (f" (<code>#{ticker}</code>)" if ticker else "") + "\n"
                                        f"📋 <b>Fakt raqami:</b> №{f_num}\n"
                                        f"📑 <b>Mavzu:</b> {clean_title}\n"
                                        f"🕒 <b>Sana:</b> {date_str}\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📄 <a href=\"{pdf_link}\">Hujjatni ko'rish (PDF)</a>"
                                    )
                                    btn_pdf = "📄 PDF ni ochish"
                                    btn_ai = f"🔍 #{ticker} AI Tahlil" if ticker else None

                                if str(f_num).strip() == "25":
                                    if u_lang == "ru":
                                        msg = msg.replace("📢 <b>СУЩЕСТВЕННЫЙ КОРПОРАТИВНЫЙ ФАКТ</b>", "⚠️ <b>RISK RADAR — НОВАЯ ЭМИССИЯ (№25)</b>", 1) + "\n\n<i>Возможно размытие доли акционеров. Это предупреждение о риске, а не торговая рекомендация.</i>"
                                    else:
                                        msg = msg.replace("📢 <b>MUHIM KORPORATIV FAKT</b>", "⚠️ <b>RISK RADAR — YANGI EMISSIYA (№25)</b>", 1) + "\n\n<i>Aksiyadorlar ulushi suyulishi mumkin. Bu xatar ogohlantirishi, savdo tavsiyasi emas.</i>"
                                markup = InlineKeyboardMarkup()
                                markup.add(InlineKeyboardButton(btn_pdf, url=pdf_link))
                                if btn_ai:
                                    markup.add(InlineKeyboardButton(btn_ai, callback_data=f"stock_{ticker}_catalyst"))

                                try:
                                    bot.send_message(uid, msg, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
                                    time.sleep(0.05)
                                except Exception:
                                    pass

                        cur.execute("UPDATE bot_alert_state SET value = ?, updated_at = datetime('now') WHERE key = 'last_alerted_fact_id'", (f_id,))
                        conn.commit()

                conn.close()
        except Exception as e:
            print(f"❌ Faktlar alert tizimi xatosi: {e}")

        time.sleep(30)

# Fon oqimlarini faqat asosiy jarayonda 1 marta ishga tushirish

def run_scheduled_ai_analysis():
    import datetime
    ADMIN_ID = 326567358
    sent_slots = set()
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5)))
            today = now.strftime('%Y-%m-%d')
            # Dushanbadan Jumagacha (ish kunlari)
            if now.weekday() < 5:
                slot_morning = f"{today}_10_15"
                slot_evening = f"{today}_18_15"
                
                # 1. Ertalabki seans (10:15)
                if now.hour == 10 and now.minute == 15 and slot_morning not in sent_slots:
                    sent_slots.add(slot_morning)
                    bot.send_message(ADMIN_ID, "🌅 <b>Ertalabki AI Tahlil boshlandi...</b>\n<i>Gemini ma'lumotlarni qayta ishlamoqda.</i>", parse_mode='HTML')
                    threading.Thread(target=lambda: (user_sessions.pop(ADMIN_ID, None), execute_radar_analysis(ADMIN_ID, None))).start()
                
                # 2. Kechki yakuniy seans (18:15)
                elif now.hour == 18 and now.minute == 15 and slot_evening not in sent_slots:
                    sent_slots.add(slot_evening)
                    bot.send_message(ADMIN_ID, "🌇 <b>Kechki yakuniy AI Tahlil boshlandi...</b>\n<i>Bozor yopilishi bo'yicha hisobot tayyorlanmoqda.</i>", parse_mode='HTML')
                    threading.Thread(target=lambda: (user_sessions.pop(ADMIN_ID, None), execute_radar_analysis(ADMIN_ID, None))).start()
        except Exception as e:
            print(f"[SCHEDULER_ERR] {e}")
        time.sleep(30)

# Scheduled thread moved before polling

def _sg_loop_hb(state):
    try:
        _fn = getattr(signals_db, "_sg_hb_loop", None)
        if _fn:
            _fn(state)
    except Exception:
        pass


def signals_tracker_worker():
    """Ish soatlarida signallar narxi va TP holatini 5 daqiqada yangilaydi (skanersiz)."""
    import datetime as _dt, time as _t
    while True:
        try:
            now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5)))
            m = now.hour * 60 + now.minute
            if now.weekday() < 5 and 9 * 60 + 30 <= m <= 18 * 60 + 30:
                tok = ADMIN_TOKEN or fetch_fresh_token()
                info = market.fetch_market_data(tok)
                if info.get("status") == "ok" and not info.get("is_stale"):
                    combined_inst = (info.get("active_stocks", []) or []) + (info.get("active_bonds", []) or [])
                    run_signals_alert_check(combined_inst)
                    _sg_loop_hb("ok")
                else:
                    print(f"[TRACKER_SKIP] status={info.get('status')}, is_stale={info.get('is_stale')}", flush=True)
                    _sg_loop_hb("skip")
        except Exception as e:
            print(f"[TRACKER_ERR] {e}", flush=True)
            _sg_loop_hb("err")
        _t.sleep(120)  # SGATE2: 300 -> 120 s

@bot.callback_query_handler(func=lambda c: c.data in ["cat_risk_radar", "risk_radar"])
def handle_risk_radar_cb(call):
    try:
        import signals_db
        text = signals_db.get_risk_radar_post(lang="uz")
        bot.send_message(call.message.chat.id, text, parse_mode="HTML")
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"⚠️ Xatolik: {e}")


if __name__ == "__main__":
    print("🚀 JETT Radar Bot ishga tushdi...")
    threading.Thread(target=fetch_fresh_token, args=(False,), daemon=True).start()
    threading.Thread(target=run_scheduled_ai_analysis, daemon=True).start()
    threading.Thread(target=signals_tracker_worker, daemon=True).start()
    threading.Thread(target=market_alert_worker, daemon=True).start()
    threading.Thread(target=facts_alert_worker, daemon=True).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
