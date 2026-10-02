import os  
import json
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

# ----------------------------------------------------
# 1. Файлове збереження стану (Persistence)
# ----------------------------------------------------
TRADING_FILE = "trading_state.txt"
SYMBOLS_FILE = "symbols_config.json"

# Базовий список монет за замовчуванням (BTC та XAU увімкнено, інші - ні)
DEFAULT_SYMBOLS_CONFIG = {
    "BTC": True,
    "XAU": True,
    "ZEC": False,
    "NEAR": False,
    "HYPE": False
}

def is_trading_enabled():
    """Перевіряє загальний стан торгівлі з файлу"""
    if os.path.exists(TRADING_FILE):
        try:
            with open(TRADING_FILE, "r") as f:
                return f.read().strip() == "True"
        except Exception:
            pass
    return True

def set_trading_state(state: bool):
    """Зберігає загальний стан торгівлі у файл"""
    try:
        with open(TRADING_FILE, "w") as f:
            f.write(str(state))
    except Exception as e:
        print(f"Помилка збереження стану торгівлі: {str(e)}")

def load_symbols_config():
    """Завантажує налаштування монет з JSON файлу"""
    if os.path.exists(SYMBOLS_FILE):
        try:
            with open(SYMBOLS_FILE, "r") as f:
                config = json.load(f)
                # Додаємо дефолтні ключі, якщо з'явилися нові монети
                for k, v in DEFAULT_SYMBOLS_CONFIG.items():
                    if k not in config:
                        config[k] = v
                return config
        except Exception:
            pass
    return DEFAULT_SYMBOLS_CONFIG.copy()

def save_symbols_config(config):
    """Зберігає налаштування монет у JSON файл"""
    try:
        with open(SYMBOLS_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Помилка збереження конфігурації монет: {str(e)}")

def is_symbol_auto_trade_enabled(ticker):
    """Перевіряє, чи увімкнена автоторгівля для конкретного тикера"""
    config = load_symbols_config()
    raw = ticker.replace(".P", "").replace("/", "").replace(":", "").upper()
    
    # Пошук відповідності тикера в конфігу
    for sym_key, enabled in config.items():
        if sym_key.upper() in raw:
            return enabled
    return False

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
    """Створення нижнього меню у Telegram"""  
    trading_on = is_trading_enabled()
    return {  
        "keyboard": [  
            [{"text": "🟢 Увімкнути торгівлю" if not trading_on else "🔴 Вимкнути торгівлю"}],  
            [{"text": "⚙️ Налаштування монет"}, {"text": "📊 Стан системи"}]  
        ],  
        "resize_keyboard": True  
    }  

def get_symbols_inline_keyboard():
    """Формує інтерактивне Inline-меню для вибору монет"""
    config = load_symbols_config()
    inline_keyboard = []
    
    for sym_key, enabled in config.items():
        status_icon = "🟢" if enabled else "🔴"
        action_text = "Автоторгівля" if enabled else "Тільки сигнал"
        btn_text = f"{status_icon} {sym_key} ({action_text})"
        callback_data = f"toggle_sym_{sym_key}"
        inline_keyboard.append([{"text": btn_text, "callback_data": callback_data}])
        
    return {"inline_keyboard": inline_keyboard}

def get_formatted_symbol(symbol):  
    """Знаходить точну назву ф'ючерсного символу BingX (Perpetual Swap)"""  
    if not exchange:  
        return symbol  
  
    try:  
        markets = exchange.load_markets(params={'type': 'swap'})  
        raw = symbol.replace('.P', '').replace('/', '').replace(':', '').strip().upper()  

        if "XAU" in raw or "GOLD" in raw:  
            for m_symbol, market in markets.items():  
                if market.get('swap', False) and ("XAU" in m_symbol or "GOLD" in m_symbol):  
                    return m_symbol  
            return "XAUT/USDT:USDT"  

        base_currency = raw.replace('USDT', '')  
        for m_symbol, market in markets.items():  
            if market.get('swap', False):  
                clean_market_symbol = m_symbol.replace('.P', '').replace('/', '').replace(':', '').upper()  
                if clean_market_symbol == raw or clean_market_symbol.startswith(f"{base_currency}USDT"):  
                    return m_symbol  
  
        return f"{base_currency}/USDT:USDT"  
  
    except Exception as e:  
        print(f"Market Lookup Error: {str(e)}")  
        raw_clean = symbol.replace('.P', '').replace('/', '').replace(':', '').strip().upper()  
        if "XAU" in raw_clean or "GOLD" in raw_clean:  
            return "XAUT/USDT:USDT"  
        base = raw_clean.replace('USDT', '')  
        return f"{base}/USDT:USDT"  

def close_opposite_positions(symbol, new_action):  
    """Завжди закриває протилежні відкриті позиції у Hedge Mode"""  
    if not exchange:  
        return ""  
    try:  
        formatted_symbol = get_formatted_symbol(symbol)  
        positions = exchange.fetch_positions([formatted_symbol])  
        
        target_position_side = 'SHORT' if new_action.upper() == 'BUY' else 'LONG'
        close_side = 'buy' if target_position_side == 'SHORT' else 'sell'
        closed_info = ""  

        for pos in positions:  
            pos_side = str(pos.get('side', '')).upper()
            contracts = float(pos.get('contracts', 0) or 0)

            if pos['symbol'] == formatted_symbol and pos_side == target_position_side and contracts > 0:  
                exchange.create_order(  
                    symbol=formatted_symbol,  
                    type='market',  
                    side=close_side,  
                    amount=contracts,  
                    params={
                        'positionSide': target_position_side,  
                        'reduceOnly': True
                    }  
                )  
                closed_info += f"\n🔄 **Попередню протилежну позицію ({target_position_side}) закрито по ринку!**"  
        return closed_info  
    except Exception as e:  
        return f"\n⚠️️ Помилка закриття попередньої позиції: {str(e)}"  

def execute_move_be(symbol, entry_price):
    """Модифікація Stop Loss на рівень безубитку (BE) на BingX"""
    if not exchange:
        return "⚠️ BingX API ключі відсутні."
    try:
        formatted_symbol = get_formatted_symbol(symbol)
        positions = exchange.fetch_positions([formatted_symbol])
        
        be_reports = []
        for pos in positions:
            contracts = float(pos.get('contracts', 0) or 0)
            if pos['symbol'] == formatted_symbol and contracts > 0:
                pos_side = str(pos.get('side', '')).upper()
                sl_side = 'sell' if pos_side == 'LONG' else 'buy'
                
                open_orders = exchange.fetch_open_orders(formatted_symbol)
                for ord in open_orders:
                    if ord.get('info', {}).get('positionSide') == pos_side and ord.get('type') == 'STOP_MARKET':
                        exchange.cancel_order(ord['id'], formatted_symbol)
                
                sl_price = float(entry_price) if entry_price else float(pos.get('entryPrice', 0))
                try:
                    sl_price_formatted = float(exchange.price_to_precision(formatted_symbol, sl_price))
                except Exception:
                    sl_price_formatted = sl_price
                
                exchange.create_order(
                    symbol=formatted_symbol,
                    type='STOP_MARKET',
                    side=sl_side,
                    amount=contracts,
                    params={
                        'stopPrice': sl_price_formatted,
                        'positionSide': pos_side
                    }
                )
                be_reports.append(f"🛡 **Stop Loss перенесено в BE (`{sl_price_formatted}`) для {formatted_symbol} ({pos_side})**")
        
        if be_reports:
            return "\n".join(be_reports)
        else:
            return f"ℹ️ Для {formatted_symbol} не знайдено відкритих позицій для переносу в BE."
    except Exception as e:
        return f"⚠️ Помилка переносу SL в BE: {str(e)}"

def execute_bingx_trade(symbol, action, price, sl, tp1, tp2):  
    """Автоматична торгівля на ф'ючерсах BingX у Hedge Mode з виставленням SL/TP"""  
    if not exchange:  
        return "⚠️ BingX API ключі не знайдені в Environment Variables."  
    
    try:  
        formatted_symbol = get_formatted_symbol(symbol)  
        is_buy = action.upper() == 'BUY'  
        side = 'buy' if is_buy else 'sell'  
        sl_side = 'sell' if is_buy else 'buy'  
        position_side = 'LONG' if is_buy else 'SHORT'  

        closed_info = close_opposite_positions(symbol, action)

        # Очищення старих ордерів
        try:
            open_orders = exchange.fetch_open_orders(formatted_symbol)
            for ord in open_orders:
                if ord.get('info', {}).get('positionSide') == position_side:
                    exchange.cancel_order(ord['id'], formatted_symbol)
        except Exception as cancel_err:
            print(f"Очищення старих ордерів: {str(cancel_err)}")

        margin_usdt = float(os.environ.get("TRADE_MARGIN_USDT", 10))  
        leverage = int(os.environ.get("TRADE_LEVERAGE", 10))  

        try:  
            exchange.set_leverage(leverage, formatted_symbol)  
        except Exception:  
            pass  

        position_size_usdt = margin_usdt * leverage  
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

        order = exchange.create_order(  
            symbol=formatted_symbol,  
            type='market',  
            side=side,  
            amount=amount,  
            params={'positionSide': position_side}  
        )  

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
            f"{closed_info}"
            f"{sl_report}"  
            f"{tp_report}"  
            f"📈 Залишок (67%) утримується за стратегією.\n"  
            f"ID Ордера: `{order['id']}`"  
        )  
    except Exception as e:  
        return f"❌ **Помилка відкриття угоди на BingX:** {str(e)}"  

def process_signal(data):  
    # 0. Захист від порожніх та пінг-запитів
    if not data or (float(data.get("price", 0)) == 0 and str(data.get("action", "")).upper() != "MOVE_BE"):
        print("Отримано порожній запит/ping. Ігноруємо.")
        return

    action = str(data.get("action", "BUY")).upper()  
    ticker = data.get("ticker", "XAUUSD")  
    formatted_symbol = get_formatted_symbol(ticker)  
  
    # А. Сигнал переносу в BE
    if action == "MOVE_BE":
        entry_price = data.get("entry_price")
        be_result = execute_move_be(formatted_symbol, entry_price)
        send_telegram(f"⚡️ **СИГНАЛ BE ДЛЯ {ticker}:**\n{be_result}")
        return

    # Б. Сигнал Входу (BUY / SELL)
    price = float(data.get("price", 0))  
    sl = float(data.get("sl", 0))  
    tp1 = float(data.get("tp1", 0))  
    tp2 = float(data.get("tp2", 0))  
    tp3 = float(data.get("tp3", 0))  
    timeframe = data.get("timeframe", "5m")  
  
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

    close_report = ""
    trade_report = ""  
    
    trading_enabled = is_trading_enabled()
    symbol_enabled = is_symbol_auto_trade_enabled(ticker)

    if "[APPROVED]" in ai_verdict:  
        if trading_enabled and symbol_enabled:  
            trade_report = "\n\n" + execute_bingx_trade(ticker, action, price, sl, tp1, tp2)  
        elif not symbol_enabled:
            trade_report = f"\n\n👁 **РЕЖИМ МОНІТОРИНГУ:** Сигнал проаналізовано ШІ, але автоторгівлю для `{ticker}` вимкнено у налаштуваннях монет."
        else:  
            trade_report = "\n\n⏸ **Автоторгівлю вимкнено в Telegram.** Сигнал проаналізовано, але угоду на BingX не створено."  

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

@app.route('/', methods=['POST', 'GET'])
@app.route('/webhook', methods=['POST', 'GET'])  
def webhook():  
    if request.method == 'GET':
        return "TradeAI Webhook Server is Live!", 200

    data = request.get_json(silent=True) or {}  
  
    # 1. Обробка подій з Telegram (кнопки меню та callback inline-кнопки)
    if "callback_query" in data:
        callback = data["callback_query"]
        cb_data = callback.get("data", "")
        
        if cb_data.startswith("toggle_sym_"):
            sym_key = cb_data.replace("toggle_sym_", "")
            config = load_symbols_config()
            if sym_key in config:
                config[sym_key] = not config[sym_key]
                save_symbols_config(config)
                
            # Оновлюємо inline-меню у повідомленні Telegram
            message_id = callback["message"]["message_id"]
            chat_id = callback["message"]["chat"]["id"]
            
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": get_symbols_inline_keyboard()
            }
            requests.post(url, json=payload)
            
            # Підтверджуємо нажаття callback
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": callback["id"]})
        return jsonify({"status": "callback processed"}), 200

    if "message" in data and "text" in data["message"]:  
        text = data["message"]["text"]  
        if text in ["🟢 Увімкнути торгівлю", "Увімкнути торгівлю"]:  
            set_trading_state(True)  
            send_telegram("✅ **Автоторгівлю на BingX УВІМКНЕНО!**\nТепер дозволені монети будуть відкриватися на біржі.", get_main_keyboard())  
        elif text in ["🔴 Вимкнути торгівлю", "Вимкнути торгівлю"]:  
            set_trading_state(False)  
            send_telegram("⏸ **Глобальну автоторгівлю ВИМКНЕНО!**\nБот працюватиме суворо в режимі моніторингу.", get_main_keyboard())  
        elif text == "⚙️ Налаштування монет":
            send_telegram("⚙️ **Налаштування режимів для монет:**\nНатискайте на кнопки нижче, щоб увімкнути 🟢 (Автоторгівля) або 🔴 (Тільки сигнал):", get_symbols_inline_keyboard())
        elif text in ["📊 Стан системи", "/start"]:  
            status_str = "🟢 **АКТИВНА**" if is_trading_enabled() else "🔴 **ВИМКНЕНА (Моніторинг)**"  
            config = load_symbols_config()
            active_syms = [k for k, v in config.items() if v]
            active_str = ", ".join(active_syms) if active_syms else "Жодної"
            
            msg = (
                f"⚙️ **Статус автоторгівлі:** {status_str}\n"
                f"🎯 **Автоторгівля увімкнена для:** `{active_str}`"
            )
            send_telegram(msg, get_main_keyboard())  
        return jsonify({"status": "telegram message processed"}), 200  
  
    # 2. Обробка сигнального вебхука TradingView  
    threading.Thread(target=process_signal, args=(data,)).start()  
    return jsonify({"status": "success"}), 200  

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
