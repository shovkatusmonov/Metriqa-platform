import os
import sqlite3
import re
import glob

PROD_DIR = "/home/ubuntu/bot"
DB_SIGNALS = os.path.join(PROD_DIR, "jett_signals.db")
DB_QUOTA = os.path.join(PROD_DIR, "gemini_quota.db")

print("="*60)
print("🔍 JETT RADAR UI ↔ BACKEND ↔ DATABASE AUDIT BOSHLANDI")
print("="*60)

# 1. Baza holatini tekshirish
print("\n[1] DATABASE JADVALLARI VA YOZUVLAR SONI:")
for db_path, name in [(DB_SIGNALS, "jett_signals.db"), (DB_QUOTA, "gemini_quota.db")]:
    if os.path.exists(db_path):
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cur.fetchall()]
        print(f"\n📂 {name}:")
        for tbl in tables:
            cur.execute(f"SELECT COUNT(*) FROM {tbl};")
            count = cur.fetchone()[0]
            print(f"  • {tbl}: {count} ta yozuv")
        conn.close()
    else:
        print(f"❌ {name} topilmadi!")

# 2. Python kodlaridagi Callback va SQL qidiruvi
print("\n" + "="*60)
print("[2] UI ELEMENTLARI VA HANDLERLAR AUDITI:")
py_files = glob.glob(f"{PROD_DIR}/**/*.py", recursive=True)

targets = {
    "Bozor Tahlili": ["market", "bozor", "quote", "stock"],
    "AI Tahlil": ["ai_analysis", "gemini", "ai_tahlil"],
    "Hisobot": ["report", "hisobot", "summary"],
    "Performance": ["performance", "pnl", "foyda", "winrate"],
    "Signallar": ["signals", "signal", "radar"],
    "Sozlamalar": ["settings", "sozlamalar", "config"]
}

for section, keywords in targets.items():
    print(f"\n📌 Bo'lim: {section}")
    found = False
    for fpath in py_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                for kw in keywords:
                    if kw in content.lower():
                        # Callback yoki handler qidirish
                        matches = re.findall(rf".*(?:callback_data|CallbackQuery|Command).*{kw}.*", content, re.IGNORECASE)
                        if matches:
                            print(f"  ✓ Fayl: {os.path.basename(fpath)} | Callback/Trigger: {matches[0].strip()[:70]}")
                            found = True
                            break
        except Exception:
            pass
    if not found:
        print(f"  ⚠️ Fayllar orasida to'g'ridan-to'g'ri callback bog'lanishi topilmadi (alohida tekshirish kerak)")

print("\n" + "="*60)
print("AUDIT YAKUNLANDI. Natijalar tahlilga tayyor.")
print("="*60)
