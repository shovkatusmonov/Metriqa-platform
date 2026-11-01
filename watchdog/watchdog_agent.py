import os
import sys
import time
import json
import psutil
import sqlite3
import subprocess
import logging
import requests
from datetime import datetime

# Konfiguratsiya yo'llari
DEV_DIR = "/home/ubuntu/jett-radar-dev"
PROD_DIR = "/home/ubuntu/bot"
DB_PATH = os.path.join(PROD_DIR, "jett_signals.db")
BOT_SERVICE = "jettbot.service"
CHECK_INTERVAL_SEC = 60
ALERT_COOLDOWN_SEC = 900  # 15 daqiqa

def read_env(file_path):
    env_vars = {}
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip().strip("'\"")
    return env_vars

dev_env = read_env(os.path.join(DEV_DIR, ".env"))
prod_env = read_env(os.path.join(PROD_DIR, ".env"))

ALERT_BOT_TOKEN = dev_env.get("ADMIN_ALERT_BOT_TOKEN") or prod_env.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = dev_env.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = prod_env.get("GEMINI_API_KEY") or dev_env.get("GEMINI_API_KEY")

last_alerts = {}

logging.basicConfig(
    filename="/home/ubuntu/watchdog.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def send_telegram_alert(alert_key: str, text: str, reply_markup=None):
    """Admin Telegramiga hisobot yuborish."""
    now = time.time()
    if alert_key in last_alerts and (now - last_alerts[alert_key]) < ALERT_COOLDOWN_SEC:
        return

    if not ALERT_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("Telegram credentials sozlanmagan!")
        return

    url = f"https://api.telegram.org/bot{ALERT_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            last_alerts[alert_key] = now
            logging.info(f"Telegram alert yuborildi: {alert_key}")
        else:
            logging.error(f"Telegram xatolik: {res.status_code} - {res.text}")
    except Exception as e:
        logging.error(f"Telegram ulanish xatosi: {e}")

def get_recent_error_logs():
    """Bot servisidan oxirgi xatolik va traceback loglarini ajratib olish."""
    try:
        cmd = ["journalctl", "-u", BOT_SERVICE, "-n", "80", "--no-pager"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = res.stdout.splitlines()
        error_lines = [l for l in lines if any(w in l for w in ["Traceback", "Error", "Exception", "CRITICAL"])]
        if error_lines:
            return "\n".join(lines[-40:])
    except Exception as e:
        logging.error(f"Log o'qishda xatolik: {e}")
    return None

def analyze_with_ai(error_log: str) -> str:
    """Gemini API orqali xatolikning Root Cause va tavsiya etilgan patchini aniqlash."""
    if not GEMINI_API_KEY:
        return "AI tahlili: GEMINI_API_KEY topilmadi."

    prompt = (
        "Sen Jett Radar loyihasining Senior DevOps va Code Review Agentisan.\n"
        "Quyidagi xatolik logini o'rganib chiq:\n"
        f"```\n{error_log}\n```\n\n"
        "Quyidagi formatda juda aniq, qisqa va o'zbek tilida xulosa ber:\n"
        "PROBLEM: (xato nimada)\n"
        "ROOT CAUSE: (kelib chiqish sababi)\n"
        "AFFECTED FILE: (qaysi faylda)\n"
        "PROPOSED FIX: (qanday minimal patch kerak)\n"
        "RISK: LOW / MEDIUM / HIGH"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800}
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            logging.error(f"Gemini API xatosi: {resp.status_code} - {resp.text}")
            return f"Gemini API javob bera olmadi (Status: {resp.status_code})"
    except Exception as e:
        logging.error(f"Gemini so'rovida xatolik: {e}")
        return f"Gemini ulanish xatosi: {str(e)}"

def check_resources():
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    if ram > 90:
        send_telegram_alert("ram_high", f"🚨 *HIGH RESOURCE ALERT*\nRAM bandligi: `{ram}%`")
    if disk > 90:
        send_telegram_alert("disk_high", f"🚨 *HIGH RESOURCE ALERT*\nDisk bandligi: `{disk}%`")

def check_services():
    cmd = ["systemctl", "is-active", "--quiet", BOT_SERVICE]
    status = subprocess.run(cmd).returncode
    if status != 0:
        error_logs = get_recent_error_logs()
        ai_report = "Loglarda traceback topilmadi."
        if error_logs:
            ai_report = analyze_with_ai(error_logs)

        msg = (
            f"🚨 *CRITICAL: BOT XIZMATI TO'XTAGAN!*\n\n"
            f"Xizmat: `{BOT_SERVICE}`\n"
            f"Holat: `INACTIVE / FAILED`\n\n"
            f"🤖 *AI DIAGNOSTIC REPORT:*\n{ai_report}\n\n"
            f"⚠️ *STATUS:* WAITING FOR USER APPROVAL\n"
            f"Production o'zgarishsiz qoldirildi."
        )
        send_telegram_alert("service_down", msg)
    else:
        if "service_down" in last_alerts:
            del last_alerts["service_down"]

def check_data_integrity():
    if not os.path.exists(DB_PATH):
        send_telegram_alert("db_missing", f"🚨 *CRITICAL*: SQLite fayli topilmadi: `{DB_PATH}`")
        return

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("PRAGMA quick_check;")
        res = cursor.fetchone()
        if res and res[0] != "ok":
            send_telegram_alert("db_corrupt", f"🚨 *CRITICAL*: SQLite buzilgan: `{res[0]}`")
        conn.close()
    except Exception as e:
        logging.error(f"DB integrity tekshirishda xatolik: {e}")

def run_loop():
    logging.info("Jett Radar 24/7 AI Watchdog to'liq ishga tushdi.")
    while True:
        try:
            check_resources()
            check_services()
            check_data_integrity()
        except Exception as e:
            logging.error(f"Watchdog siklida xatolik: {e}")
        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    run_loop()

