import os
import requests
import threading
import ccxt
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# Ключі середовища з Render
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
BINGX_API_KEY = os.environ.get("BINGX_API_KEY")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Ініціалізація BingX через CCXT
exchange = None
if BINGX_API_KEY and BINGX_SECRET_KEY:
    exchange = ccxt.bingx({
        'apiKey': BINGX_API_KEY,
        'secret': BINGX_SECRET_KEY,
        'options': {'defaultType': 'swap'}  # Безстрокові ф'ючерси
    })

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def execute_bingx_trade(symbol, action, price, sl, tp1):
    """Автоматична торгівля на BingX з налаштуваннями з Render Environment Variables"""
    if not exchange:
        return "⚠️ BingX API ключі не знайдені в Environment Variables."
    
    try:
        # Приводимо тикер до формату CCXT (наприклад, BTC/USDT:USDT)
        formatted_symbol = symbol.replace('.P', '').replace('USDT', '/USDT:USDT')
        side = 'buy' if action.upper() == 'BUY' else 'sell'

        # Отримуємо налаштування маржі та плеча зі змінних середовища Render
        margin_usdt = float(os.environ.get("TRADE_MARGIN_USDT", 10))
        leverage = int(os.environ.get("TRADE_LEVERAGE", 10))

        # Встановлюємо плече на BingX
        try:
            exchange.set_leverage(leverage, formatted_symbol)
        except Exception:
            pass

        # Розрахунок об'єму позиції
        position_size_usdt = margin_usdt * leverage
        amount = position_size_usdt / price

        # Виставляємо ринковий ордер із прив'язаними SL та TP1
        params = {
            'stopLoss': {'triggerPrice': float(sl)},
            'takeProfit': {'triggerPrice': float(tp1)}
        }
        
        order = exchange.create_order(
            symbol=formatted_symbol,
            type='market',
            side=side,
            amount=amount,
            params=params
        )
        return f"✅ **Угоду успішно відкрито на BingX!**\nОб'єм: `${position_size_usdt}` (Маржа: `${margin_usdt}` x{leverage})\nID Ордера: `{order['id']}`"
    except Exception as e:
        return f"❌ **Помилка відкриття угоди на BingX:** {str(e)}"

def process_signal(data):
    ticker = data.get("ticker", "XAUUSD")
    action = data.get("action", "BUY")
    price = float(data.get("price", 0))
    sl = float(data.get("sl", 0))
    tp1 = float(data.get("tp1", 0))
    tp2 = float(data.get("tp2", 0))
    tp3 = float(data.get("tp3", 0))
    timeframe = data.get("timeframe", "5m")

    # Формуємо чіткий запит до OpenAI з вердиктом НА ПОЧАТКУ
    prompt = f"""
Ти — експертний трейдер із Smart Money Concepts (SMC), FVG та алгоритмічного аналізу.
Проаналізуй торговий сигнал для {ticker} ({action}):
- Робочий таймфрейм: {timeframe}
- Ціна входу: {price}
- Stop Loss: {sl}
- Take Profit 1: {tp1}
- Take Profit 2: {tp2}
- Take Profit 3: {tp3}

Будь ласка, надай відповідь ЧІТКО за такою структурою:

1. **ФІНАЛЬНИЙ ВЕРДИКТ:** Напиши **[APPROVED]** або **[REJECTED]** у першому ж рядку та короткий підсумок (входити чи ні).
2. **Оцінка Risk-to-Reward:** розрахуй співвідношення R:R для TP1, TP2 та TP3.
3. **Контекст SMC/FVG:** коротко про закриття імбалансу, зони попиту/пропозиції та волатильність.

Не використовуй символи ### для заголовків, пиши простим текстом із жирним виділенням **текст**.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=750  # Збільшуємо запас токенів, щоб аналіз ніколи не обривався!
        )
        ai_verdict = response.choices[0].message.content
    except Exception as e:
        ai_verdict = f"Помилка ШІ: {str(e)}"

    # Якщо ШІ ухвалив угоду [APPROVED], виконуємо її на BingX
    trade_report = ""
    if "[APPROVED]" in ai_verdict:
        trade_report = "\n\n" + execute_bingx_trade(ticker, action, price, sl, tp1)

    msg = (
        f"⚡️ **НОВИЙ СИГНАЛ: {ticker} ({action})**\n\n"
        f"📍 **Вхід:** `{price}`\n"
        f"🛑 **SL:** `{sl}`\n"
        f"🎯 **TP1:** `{tp1}`\n"
        f"🎯 **TP2:** `{tp2}`\n"
        f"🎯 **TP3:** `{tp3}`\n\n"
        f"🤖 **Аналіз ШІ:**\n{ai_verdict}"
        f"{trade_report}"
    )

    send_telegram(msg)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return jsonify({"status": "no data"}), 400

    threading.Thread(target=process_signal, args=(data,)).start()
    return jsonify({"status": "success"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
