import os
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# Ключі беруться з змінних середовища хостингу для безпеки
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return jsonify({"status": "no data"}), 400

    ticker = data.get("ticker", "XAUUSD")
    action = data.get("action", "BUY")
    price = data.get("price", 0)
    sl = data.get("sl", 0)
    tp1 = data.get("tp1", 0)
    timeframe = data.get("timeframe", "5m")

    # Формуємо запит до OpenAI для оцінки контексту ризику
    prompt = f"""
    Ви виступаєте в ролі ризик-менеджера для торгового бота на золото (XAU/USD).
    Надійшов новий сигнал:
    - Напрямок: {action}
    - Таймфрейм: {timeframe}
    - Ціна входу: {price}
    - Stop Loss: {sl}
    - Take Profit 1: {tp1}

    Коротко оцініть цей сигнал (до 3 речень):
    1. Наскільки адекватний співвідношення ризику (SL vs TP1).
    2. Чи варто входити, враховуючи високу волатильність золота.
    Дайте підсумковий вердикт: [CONFIRMED] або [REJECTED].
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150
        )
        ai_verdict = response.choices[0].message.content
    except Exception as e:
        ai_verdict = f"Помилка ШІ: {str(e)}"

    # Формуємо красиве повідомлення в Telegram
    msg = (
        f"⚡ **НОВИЙ СИГНАЛ: {ticker} ({action})**\n\n"
        f"📍 **Вхід:** `{price}`\n"
        f"🛑 **SL:** `{sl}`\n"
        f"🎯 **TP1:** `{tp1}`\n\n"
        f"🤖 **Аналіз ШІ:**\n{ai_verdict}"
    )
    
    send_telegram(msg)
    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))