import os
import requests
import threading
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

def process_signal(data):
    """Ця функція виконується у фоні, щоб TradingView не чекав відповіді від OpenAI"""
    ticker = data.get("ticker", "XAUUSD")
    action = data.get("action", "BUY")
    price = data.get("price", 0)
    sl = data.get("sl", 0)
    tp1 = data.get("tp1", 0)
    tp2 = data.get("tp2", 0)
    tp3 = data.get("tp3", 0)
    timeframe = data.get("timeframe", "5m")

    # Формуємо розширений запит до OpenAI
    prompt = f"""
Ти — експертний трейдер із Smart Money Concepts (SMC), FVG та алгоритмічного аналізу.
Проаналізуй торговий сигнал:
- Інструмент: {ticker}
- Напрямок: {action}
- Робочий таймфрейм: {timeframe}
- Ціна входу: {price}
- Stop Loss: {sl}
- Take Profit 1: {tp1}
- Take Profit 2: {tp2}
- Take Profit 3: {tp3}

Дай чіткий та лаконічний аналіз у 3 пунктах:
1. **Оцінка структури та Risk-to-Reward:** розрахуй співвідношення R:R для TP1, TP2 та TP3.
2. **Ймовірність відпрацювання (FVG / OB Контекст):** оціни закриття імбалансу, реакцію від зон попиту/пропозиції та специфіку волатильності для {ticker} на {timeframe}.
3. **ФІНАЛЬНИЙ ВЕРДИКТ:** Напиши чітко **[APPROVED]** або **[REJECTED]** і дай коротку рекомендацію (входити повним об'ємом, зменшеним чи пропустити).
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400
        )
        ai_verdict = response.choices[0].message.content
    except Exception as e:
        ai_verdict = f"Помилка ШІ: {str(e)}"

    # Формуємо красиво повідомлення в Telegram
    msg = (
        f"⚡️ **НОВИЙ СИГНАЛ: {ticker} ({action})**\n\n"
        f"📍 **Вхід:** `{price}`\n"
        f"🛑 **SL:** `{sl}`\n"
        f"🎯 **TP1:** `{tp1}`\n"
        f"🎯 **TP2:** `{tp2}`\n"
        f"🎯 **TP3:** `{tp3}`\n\n"
        f"🤖 **Аналіз ШІ:**\n{ai_verdict}"
    )

    send_telegram(msg)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return jsonify({"status": "no data"}), 400

    # Запускаємо обробку та відправку в Telegram у окремому фоновому потоці
    threading.Thread(target=process_signal, args=(data,)).start()

    # Миттєво відповідаємо TradingView 200 OK
    return jsonify({"status": "success"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
