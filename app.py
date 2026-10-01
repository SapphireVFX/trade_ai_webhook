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

def get_formatted_symbol(symbol):
    """
    Динамічно знаходить точну назву символу в базі BingX (для XAU/GOLD та Crypto)
    """
    if not exchange:
        return symbol
    try:
        markets = exchange.load_markets()
        raw = symbol.replace('.P', '').replace('/', '').upper()
        
        # 1. Пошук для Золота (XAU / GOLD)
        if "XAU" in raw or "GOLD" in raw:
            for m_symbol in markets:
                if "XAU" in m_symbol or "GOLD" in m_symbol:
                    return m_symbol
            return "GOLD/USDT:USDT"

        # 2. Пошук для Криптовалют (BTC, ETH тощо)
        base_currency = raw.replace('USDT', '')
        expected_pattern = f"{base_currency}/USDT"
        
        for m_symbol in markets:
            if expected_pattern in m_symbol:
                return m_symbol
                
        return f"{base_currency}/USDT:USDT"
    except Exception:
        return "XAU/USDT:USDT" if ("XAU" in symbol or "GOLD" in symbol) else "BTC/USDT:USDT"
        
def close_opposite_positions(symbol, new_action):
    """Завжди закриває протилежні відкриті позиції при отриманні нового сигналу"""
    if not exchange:
        return ""
    try:
        formatted_symbol = get_formatted_symbol(symbol)
        positions = exchange.fetch_positions([formatted_symbol])
        opposite_side = 'short' if new_action.upper() == 'BUY' else 'long'
        closed_info = ""

        for pos in positions:
            if pos['symbol'] == formatted_symbol and pos['side'].lower() == opposite_side and float(pos['contracts']) > 0:
                amount = float(pos['contracts'])
                close_side = 'buy' if opposite_side == 'short' else 'sell'
                
                exchange.create_order(
                    symbol=formatted_symbol,
                    type='market',
                    side=close_side,
                    amount=amount,
                    params={'reduceOnly': True}
                )
                closed_info += f"\n🔄 **Попередню протилежну позицію ({opposite_side.upper()}) закрито по ринку!**"
        return closed_info
    except Exception as e:
        return f"\n⚠️ Помилка закриття попередньої позиції: {str(e)}"

def execute_bingx_trade(symbol, action, price, sl, tp1, tp2):
    """Автоматична торгівля на BingX"""
    if not exchange:
        return "⚠️ BingX API ключі не знайдені в Environment Variables."
    
    try:
        formatted_symbol = get_formatted_symbol(symbol)
        side = 'buy' if action.upper() == 'BUY' else 'sell'

        margin_usdt = float(os.environ.get("TRADE_MARGIN_USDT", 10))
        leverage = int(os.environ.get("TRADE_LEVERAGE", 10))

        # Встановлюємо плече
        try:
            exchange.set_leverage(leverage, formatted_symbol)
        except Exception:
            pass

        position_size_usdt = margin_usdt * leverage
        amount = position_size_usdt / price

        # Точне округлення об'єму для біржі
        try:
            amount = float(exchange.amount_to_precision(formatted_symbol, amount))
        except Exception:
            pass

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
        return (
            f"✅ **Угоду успішно відкрито на BingX!**\n"
            f"Точний інструмент: `{formatted_symbol}`\n"
            f"Об'єм: `${position_size_usdt}` (Маржа: `${margin_usdt}` x{leverage})\n"
            f"🎯 TP1 (33%): `{tp1}`\n"
            f"🎯 TP2 (33%): `{tp2}`\n"
            f"📈 Залишок (34%) утримується до зворотного сигналу.\n"
            f"ID Ордера: `{order['id']}`"
        )
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

    formatted_symbol = get_formatted_symbol(ticker)

    # 1. ЗАВЖДИ закриваємо протилежні позиції при надходженні сигналу (Варіант А)
    close_report = close_opposite_positions(formatted_symbol, action)

    # 2. Формуємо запит до OpenAI
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
            max_tokens=750
        )
        ai_verdict = response.choices[0].message.content
    except Exception as e:
        ai_verdict = f"Помилка ШІ: {str(e)}"

    # 3. Якщо ШІ ухвалив угоду [APPROVED], відкриваємо її на BingX
    trade_report = ""
    if "[APPROVED]" in ai_verdict:
        trade_report = "\n\n" + execute_bingx_trade(ticker, action, price, sl, tp1, tp2)

    msg = (
        f"⚡️ **НОВИЙ СИГНАЛ: {ticker} ({action})**{close_report}\n\n"
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
