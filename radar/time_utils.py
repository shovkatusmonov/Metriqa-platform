from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

def now_utc_iso() -> str:
    """Baza uchun har doim UTC ISO saqlaydi."""
    return datetime.now(timezone.utc).isoformat()

def to_tashkent_display(dt_str_or_obj, fmt: str = "%H:%M %d.%m.%Y") -> str:
    """Foydalanuvchi ko'radigan barcha joyda Toshkent vaqtini qaytaradi."""
    if not dt_str_or_obj:
        return "-"
    if isinstance(dt_str_or_obj, str):
        # Naive bo'lsa UTC deb hisoblaymiz
        try:
            dt = datetime.fromisoformat(dt_str_or_obj)
        except ValueError:
            return dt_str_or_obj
    else:
        dt = dt_str_or_obj
        
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TASHKENT_TZ).strftime(fmt)
