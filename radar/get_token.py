import os
import sys
import json
import time
import base64
import requests
import subprocess
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
TOKEN_FILE = os.path.join(BASE_DIR, "live_token.txt")

def clean_old_processes():
    try:
        subprocess.run(["pkill", "-9", "-f", "chromium"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-9", "-f", "chrome"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def is_token_valid(token_str):
    """JWT tokenning yaroqlilik muddatini tekshiradi."""
    if not token_str or not isinstance(token_str, str) or len(token_str) < 30:
        return False
    try:
        parts = token_str.replace("Bearer ", "").strip().split(".")
        if len(parts) != 3:
            return False
        # Base64 padding to'ldirish
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
        exp = payload.get("exp", 0)
        # Muddati tugashiga kamida 30 soniya qolgan bo'lsa True
        return exp > (time.time() + 30)
    except Exception:
        return False

def get_live_token():
    # 0. Faylda mavjud tokenni tekshirish (agar yaroqli bo'lsa, ortiqcha so'rov yubormaslik)
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                cur_tok = f.read().strip()
                if is_token_valid(cur_tok):
                    return cur_tok
                else:
                    print("[TOKEN] Fayldagi mavjud tokenning muddati tugagan (EXPIRED).")
        except Exception as e:
            print(f"[TOKEN] Faylni o'qishda xatolik: {e}")

    captured_token = None

    # 1. Guest-Token API orqali olish
    print("[TOKEN] 1-bosqich: Guest-token API ga so'rov yuborilmoqda...")
    try:
        r = requests.post(
            "https://api.jett.uz/account/web/guest-token",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://app.jett.uz",
                "Referer": "https://app.jett.uz/"
            },
            timeout=6
        )
        if r.status_code == 200:
            data = r.json()
            tok = data.get("token") or data.get("data", {}).get("token")
            if tok and is_token_valid(tok):
                print("✅ [TOKEN] Guest-token muvaffaqiyatli olindi!")
                captured_token = tok
        else:
            print(f"⚠️ [TOKEN] Guest-token API javobi: HTTP {r.status_code}")
    except Exception as e:
        print(f"⚠️ [TOKEN] Guest-token API ulanish xatosi: {e}")

    # 2. Brauzer (Playwright) orqali olish
    if not captured_token:
        print("[TOKEN] 2-bosqich: Headless Chromium ishga tushirilmoqda...")
        clean_old_processes()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
                context = browser.new_context(
                    storage_state=STATE_FILE if os.path.exists(STATE_FILE) else None,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )
                page = context.new_page()

                def handle_req(req):
                    nonlocal captured_token
                    if captured_token:
                        return
                    tok = req.headers.get("token") or req.headers.get("authorization", "")
                    tok_clean = tok.replace("Bearer ", "").strip()
                    if is_token_valid(tok_clean):
                        captured_token = tok_clean

                page.on("request", handle_req)
                try:
                    page.goto("https://app.jett.uz", timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"⚠️ [TOKEN] Sahifani ochishda ogohlantirish: {e}")

                if not captured_token:
                    try:
                        storage = page.evaluate("() => JSON.stringify(localStorage)")
                        for val in json.loads(storage).values():
                            if isinstance(val, str) and ("eyJ" in val):
                                clean_val = val.replace("Bearer ", "").replace('"', '').strip()
                                if is_token_valid(clean_val):
                                    captured_token = clean_val
                                    break
                    except Exception as e:
                        print(f"⚠️ [TOKEN] localStorage o'qishda xatolik: {e}")

                context.close()
                browser.close()
                clean_old_processes()
        except Exception as e:
            print(f"❌ [TOKEN] Brauzer boshqaruvida xatolik: {e}")

    # 3. Yakuniy saqlash
    if captured_token:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(captured_token)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ Yangi token TOKEN_FILE ga saqlandi!")
        return captured_token

    print("❌ [TOKEN] Yangi token olib bo'lmadi.")
    return ""

if __name__ == "__main__":
    get_live_token()
