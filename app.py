import os
import requests
import threading
import ccxt
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# Ключі середовища
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
    """Функція автоматичного відкриття ордера на BingX"""
    if not exchange:
        return "⚠️ BingX API ключі не знайдені в Environment Variables."
    
    try:
        # Приводимо тикер до формату CCXT (наприклад, BTC/USDT:USDT)
        formatted_symbol = symbol.replace('.P', '').replace('USDT', '/USDT:USDT')
        side = 'buy' if action.upper() == 'BUY' else 'sell'

        # Встановлюємо плече (наприклад, 10x)
        try:
            exchange.set_leverage(10, formatted_symbol)
        except Exception:
            pass

        # Розрахунок об'єму (наприклад, фіксований ордер на $10)
        margin_usdt = 10 
        leverage = 10
        position_size_usdt = margin_usdt * leverage
        amount = position_size_usdt / price

        # Відкриваємо ринкову позицію з автоматичним SL та TP1
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
        return f"✅ **Угоду успішно відкрито на BingX!**\nID Ордера: `{order['id']}`"
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
3. **ФІНАЛЬНИЙ ВЕРДИКТ:** Напиши чітко **[APPROVED]** або **[REJECTED]** і дай коротку рекомендацію.
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

    # Якщо ШІ ухвалив угоду [APPROVED], відправляємо ордер на BingX
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
