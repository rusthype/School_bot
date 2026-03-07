from __future__ import annotations

ORDER_STATUS = {
    "pending": {
        "emoji": "⏳",
        "name": "Kutilmoqda",
        "description": "Buyurtma qabul qilindi, ko'rib chiqilmoqda",
        "next_status": ["processing", "confirmed", "rejected"],
    },
    "processing": {
        "emoji": "🔄",
        "name": "Jarayonda",
        "description": "Buyurtma tayyorlanmoqda",
        "next_status": ["confirmed", "delivered", "rejected"],
    },
    "confirmed": {
        "emoji": "✅",
        "name": "Tasdiqlangan",
        "description": "Buyurtma tasdiqlandi",
        "next_status": ["processing", "delivered", "rejected"],
    },
    "rejected": {
        "emoji": "❌",
        "name": "Rad etilgan",
        "description": "Buyurtma rad etildi",
        "next_status": [],
    },
    "delivered": {
        "emoji": "📦",
        "name": "Yetkazilgan",
        "description": "Kitoblar yetkazildi",
        "next_status": [],
    },
    "cancelled": {
        "emoji": "❌",
        "name": "Bekor qilingan",
        "description": "Buyurtma bekor qilindi",
        "next_status": [],
    },
}

ORDER_PRIORITY = {
    "normal": {"emoji": "🟢", "name": "Oddiy", "delivery_time": "7-10 kun"},
    "urgent": {"emoji": "🟡", "name": "Shoshilinch", "delivery_time": "3-5 kun"},
    "express": {"emoji": "🔴", "name": "Tezkor", "delivery_time": "1-2 kun"},
}


def get_status_text(status: str | None) -> str:
    if not status:
        return "Noma'lum"
    info = ORDER_STATUS.get(status, ORDER_STATUS["pending"])
    return f"{info['emoji']} {info['name']}"


def get_priority_text(priority: str | None) -> str:
    info = ORDER_PRIORITY.get(priority or "normal", ORDER_PRIORITY["normal"])
    return f"{info['emoji']} {info['name']} ({info['delivery_time']})"
