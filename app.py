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
    tp2 = data.get("tp2", 0)
    tp3 = data.get("tp3", 0)
    timeframe = data.get("timeframe", "5m")

    # Формуємо запит до OpenAI для оцінки контексту ризику
    prompt = f"""
    Проаналізуй наступний торговий сигнал:
    - Інструмент: {ticker}
    - Напрямок: {action}
    - Таймфрейм: {timeframe}
    - Ціна входу: {price}
    - Stop Loss: {sl}
    - Take Profit 1 (Quick Scalp): {tp1}
    - Take Profit 2 (Main Target): {tp2}
    - Take Profit 3 (Trend Target): {tp3}

    Дай короткий аналіз ризику (Risk-to-Reward для кожної цілі), оціни доцільність угоди враховуючи волатильність і винеси вердикт (APPROVED або REJECTED).
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
        f"🎯 **TP2:** `{tp2}`\n\n"
        f"🎯 **TP3:** `{tp3}`\n\n"
        f"🤖 **Аналіз ШІ:**\n{ai_verdict}"
    )
    
    send_telegram(msg)
    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
