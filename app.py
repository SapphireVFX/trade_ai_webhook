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

# Глобальний перемикач торгівлі (за замовчуванням УВІМКНЕНО)
TRADING_ENABLED = True

# Ініціалізація BingX через CCXT
exchange = None
if BINGX_API_KEY and BINGX_SECRET_KEY:
    exchange = ccxt.bingx({
        'apiKey': BINGX_API_KEY.strip(),
        'secret': BINGX_SECRET_KEY.strip(),
        'options': {
            'defaultType': 'swap',
            'recvWindow': 10000
        },
        'enableRateLimit': True
    })

def send_telegram(message, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def get_main_keyboard():
    """Створення інтерактивних кнопок у Telegram"""
    status_text = "🟢 Автоторгівля УВІМКНЕНА" if TRADING_ENABLED else "🔴 Автоторгівля ВИМКНЕНА"
    return {
        "keyboard": [
            [{"text": "🟢 Увімкнути торгівлю"}, {"text": "🔴 Вимкнути торгівлю"}],
            [{"text": "📊 Стан системи"}]
        ],
        "resize_keyboard": True
    }

def get_formatted_symbol(symbol):
    """
    Знаходить точну назву ф'ючерсного символу BingX (Perpetual Swap)
    """
    if not exchange:
        return symbol
    try:
        # Завантажуємо тільки swap (ф'ючерси)
        markets = exchange.load_markets(params={'type': 'swap'})
        raw = symbol.replace('.P', '').replace('/', '').upper()
        
        # 1. Пошук для Золота (XAU / GOLD)
        if "XAU" in raw or "GOLD" in raw:
            for m_symbol, market in markets.items():
                if market.get('swap', False) and ("XAU" in m_symbol or "GOLD" in m_symbol):
                    return m_symbol
            return "GOLD/USDT:USDT"

        # 2. Пошук для Криптовалют (BTC, ETH тощо)
        base_currency = raw.replace('USDT', '')
        expected_pattern = f"{base_currency}/USDT"
        
        for m_symbol, market in markets.items():
            if market.get('swap', False) and expected_pattern in m_symbol:
                return m_symbol
                
        return f"{base_currency}/USDT:USDT"
    except Exception as e:
        print(f"Market Lookup Error: {str(e)}")
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
    """Автоматична торгівля на ф'ючерсах BingX у Hedge Mode з виставленням SL/TP"""
    if not exchange:
        return "⚠️️ BingX API ключі не знайдені в Environment Variables."
    
    try:
        formatted_symbol = get_formatted_symbol(symbol)
        is_buy = action.upper() == 'BUY'
        side = 'buy' if is_buy else 'sell'
        sl_side = 'sell' if is_buy else 'buy'
        position_side = 'LONG' if is_buy else 'SHORT'

        margin_usdt = float(os.environ.get("TRADE_MARGIN_USDT", 10))
        leverage = int(os.environ.get("TRADE_LEVERAGE", 10))

        # 1. Встановлюємо плече
        try:
            exchange.set_leverage(leverage, formatted_symbol)
        except Exception:
            pass

        position_size_usdt = margin_usdt * leverage
        
        # 2. Розрахунок кількості контрактів та округлення цін
        market_info = exchange.market(formatted_symbol)
        contract_size = float(market_info.get('contractSize', 1.0))
        
        amount = (position_size_usdt / price) / contract_size

        try:
            amount = float(exchange.amount_to_precision(formatted_symbol, amount))
            sl_price_formatted = float(exchange.price_to_precision(formatted_symbol, sl))
            tp1_price_formatted = float(exchange.price_to_precision(formatted_symbol, tp1))
        except Exception:
            sl_price_formatted = float(sl)
            tp1_price_formatted = float(tp1)

        # 3. Вхід у ринкову позицію
        order = exchange.create_order(
            symbol=formatted_symbol,
            type='market',
            side=side,
            amount=amount,
            params={'positionSide': position_side}
        )

        # 4. Виставляємо окремий Stop Loss
        sl_report = ""
        try:
            exchange.create_order(
                symbol=formatted_symbol,
                type='STOP_MARKET',
                side=sl_side,
                amount=amount,
                params={
                    'stopPrice': sl_price_formatted,
                    'positionSide': position_side
                }
            )
            sl_report = f"🛑 **SL зафіксовано:** `{sl_price_formatted}`\n"
        except Exception as sl_err:
            sl_report = f"⚠️ **Помилка виставлення SL:** {str(sl_err)}\n"

        # 5. Виставляємо окремий Take Profit 1 (33% об'єму)
        tp_report = ""
        try:
            tp1_amount = float(exchange.amount_to_precision(formatted_symbol, amount * 0.33))
            exchange.create_order(
                symbol=formatted_symbol,
                type='TAKE_PROFIT_MARKET',
                side=sl_side,
                amount=tp1_amount,
                params={
                    'stopPrice': tp1_price_formatted,
                    'positionSide': position_side
                }
            )
            tp_report = f"🎯 **TP1 зафіксовано (33%):** `{tp1_price_formatted}`\n"
        except Exception as tp_err:
            tp_report = f"⚠️ **Помилка виставлення TP1:** {str(tp_err)}\n"

        return (
            f"✅ **Угоду успішно відкрито на BingX Futures!**\n"
            f"Інструмент: `{formatted_symbol}` ({position_side})\n"
            f"Об'єм: `${position_size_usdt}` (Маржа: `${margin_usdt}` x{leverage})\n"
            f"{sl_report}"
            f"{tp_report}"
            f"📈 Залишок (67%) утримується за стратегією.\n"
            f"ID Ордера: `{order['id']}`"
        )
    except Exception as e:
        return f"❌ **Помилка відкриття угоди на BingX:** {str(e)}"

def process_signal(data):
    global TRADING_ENABLED
    ticker = data.get("ticker", "XAUUSD")
    action = data.get("action", "BUY")
    price = float(data.get("price", 0))
    sl = float(data.get("sl", 0))
    tp1 = float(data.get("tp1", 0))
    tp2 = float(data.get("tp2", 0))
    tp3 = float(data.get("tp3", 0))
    timeframe = data.get("timeframe", "5m")

    formatted_symbol = get_formatted_symbol(ticker)

    # 1. Якщо торгівлю увімкнено — закриваємо протилежні позиції (Варіант А)
    close_report = ""
    if TRADING_ENABLED:
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

    # 3. Використовуємо перемикач торгівлі
    trade_report = ""
    if "[APPROVED]" in ai_verdict:
        if TRADING_ENABLED:
            trade_report = "\n\n" + execute_bingx_trade(ticker, action, price, sl, tp1, tp2)
        else:
            trade_report = "\n\n⏸ **Автоторгівлю вимкнено через Telegram.** Сигнал проаналізовано, але угоду на BingX не створено."

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

    send_telegram(msg, get_main_keyboard())

@app.route('/webhook', methods=['POST'])
def webhook():
    global TRADING_ENABLED
    data = request.json
    if not data:
        return jsonify({"status": "no data"}), 400

    # Перевіряємо, чи це повідомлення від команди/кнопки з Telegram
    if "message" in data and "text" in data["message"]:
        text = data["message"]["text"]
        if text == "🟢 Увімкнути торгівлю":
            TRADING_ENABLED = True
            send_telegram("✅ **Автоторгівлю на BingX УВІМКНЕНО!**\nТепер схвалені сигнали будуть відкриватися на біржі.", get_main_keyboard())
        elif text == "🔴 Вимкнути торгівлю":
            TRADING_ENABLED = False
            send_telegram("⏸ **Автоторгівлю на BingX ВИМКНЕНО!**\nБот працюватиме в режимі моніторингу (тільки аналітика в Telegram).", get_main_keyboard())
        elif text in ["📊 Стан системи", "/start"]:
            status_str = "🟢 **АКТИВНА**" if TRADING_ENABLED else "🔴 **ВИМКНЕНА (Моніторинг)**"
            send_telegram(f"⚙️ **Статус автоторгівлі:** {status_str}", get_main_keyboard())
        return jsonify({"status": "telegram message processed"}), 200

    # Якщо це сигнал з TradingView
    threading.Thread(target=process_signal, args=(data,)).start()
    return jsonify({"status": "success"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
