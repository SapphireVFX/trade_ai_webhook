import os   
import json  
import requests   
import threading   
import ccxt   
from datetime import datetime, timezone
from flask import Flask, request, jsonify, redirect   
from openai import OpenAI   

def is_weekend_closed():
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() == 5:
        return True
    if now_utc.weekday() == 6 and now_utc.hour < 23:
        return True
    return False

app = Flask(__name__)  

# Ключі середовища з Render  
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")  
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  

# BingX Credentials
BINGX_API_KEY = os.environ.get("BINGX_API_KEY")  
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY")  

# cTrader Credentials
CTRADER_CLIENT_ID = os.environ.get("CTRADER_CLIENT_ID")
CTRADER_CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET")
CTRADER_ACCOUNT_ID = os.environ.get("CTRADER_ACCOUNT_ID")
CTRADER_REFRESH_TOKEN_ENV = os.environ.get("CTRADER_REFRESH_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None  

# ----------------------------------------------------
# 1. Файлове збереження стану та конфігурацій
# ----------------------------------------------------
TRADING_BINGX_FILE = "trading_bingx.txt"
TRADING_CTRADER_FILE = "trading_ctrader.txt"
AI_FILTER_BINGX_FILE = "ai_filter_bingx.txt"
AI_FILTER_CTRADER_FILE = "ai_filter_ctrader.txt"

SYMBOLS_BINGX_FILE = "symbols_bingx.json"
SYMBOLS_CTRADER_FILE = "symbols_ctrader.json"
CTRADER_TOKEN_FILE = "ctrader_token.json"

DEFAULT_BINGX_CONFIG = {  
    "BTC": False,  
    "ZEC": False,  
    "NEAR": False,  
    "HYPE": False  
}

DEFAULT_CTRADER_CONFIG = {
    "XAUUSD": {"enabled": True, "lot": 0.01},
    "EURUSD": {"enabled": False, "lot": 0.01}
}

def get_exchange_state(filepath, default=False):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return f.read().strip() == "True"
        except Exception:
            pass
    return default

def set_exchange_state(filepath, state: bool):
    try:
        with open(filepath, "w") as f:
            f.write(str(state))
    except Exception as e:
        print(f"Помилка збереження стану: {str(e)}")

def load_config(filepath, default_config):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                config = json.load(f)
                for k, v in default_config.items():
                    if k not in config:
                        config[k] = v
                return config
        except Exception:
            pass
    return default_config.copy()

def save_config(filepath, config):
    try:
        with open(filepath, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Помилка збереження конфігурації: {str(e)}")

# Ініціалізація BingX через CCXT  
exchange = None  
if BINGX_API_KEY and BINGX_SECRET_KEY:  
    exchange = ccxt.bingx({  
        'apiKey': BINGX_API_KEY.strip(),  
        'secret': BINGX_SECRET_KEY.strip(),  
        'options': {'defaultType': 'swap', 'recvWindow': 10000},  
        'enableRateLimit': True  
    })  

def send_telegram(message, reply_markup=None):  
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:  
        return  
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"  
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}  
    if reply_markup:  
        payload["reply_markup"] = reply_markup  
    requests.post(url, json=payload)  

def get_main_keyboard():  
    bingx_on = get_exchange_state(TRADING_BINGX_FILE)
    ctrader_on = get_exchange_state(TRADING_CTRADER_FILE)
    
    bingx_btn_text = "🟢 BingX ON" if bingx_on else "🔴 BingX OFF"
    ctrader_btn_text = "🟢 cTrader ON" if ctrader_on else "🔴 cTrader OFF"
    
    return {  
        "keyboard": [  
            [{"text": bingx_btn_text}, {"text": ctrader_btn_text}],  
            [{"text": "⚙️ Налаштування BingX"}, {"text": "⚙️ Налаштування cTrader"}],
            [{"text": "📊 Стан системи"}]  
        ],  
        "resize_keyboard": True  
    }

def get_bingX_inline_keyboard():
    config = load_config(SYMBOLS_BINGX_FILE, DEFAULT_BINGX_CONFIG)
    ai_filter = get_exchange_state(AI_FILTER_BINGX_FILE, default=True)
    
    inline_keyboard = []
    filter_icon = "🟢" if ai_filter else "🔴"
    inline_keyboard.append([{"text": f"{filter_icon} Фільтрація ШІ (Вердикт)", "callback_data": "toggle_ai_bingx"}])
    
    for sym_key, enabled in config.items():
        status_icon = "🟢" if enabled else "🔴"
        action_text = "Автоторгівля" if enabled else "Тільки сигнал"
        btn_text = f"{status_icon} {sym_key} ({action_text})"
        inline_keyboard.append([{"text": btn_text, "callback_data": f"bingx_{sym_key}"}])
    return {"inline_keyboard": inline_keyboard}

def get_ctrader_inline_keyboard():
    config = load_config(SYMBOLS_CTRADER_FILE, DEFAULT_CTRADER_CONFIG)
    ai_filter = get_exchange_state(AI_FILTER_CTRADER_FILE, default=True)
    
    inline_keyboard = []
    filter_icon = "🟢" if ai_filter else "🔴"
    inline_keyboard.append([{"text": f"{filter_icon} Фільтрація ШІ (Вердикт)", "callback_data": "toggle_ai_ctrader"}])
    
    for sym_key, data in config.items():
        enabled = data["enabled"]
        lot = data["lot"]
        status_icon = "🟢" if enabled else "🔴"
        btn_text = f"{status_icon} {sym_key} (Лот: {lot})"
        inline_keyboard.append([{"text": btn_text, "callback_data": f"ctrader_toggle_{sym_key}"}])
    return {"inline_keyboard": inline_keyboard}

def is_bingx_symbol_enabled(ticker):
    config = load_config(SYMBOLS_BINGX_FILE, DEFAULT_BINGX_CONFIG)
    raw = ticker.replace(".P", "").replace("/", "").replace(":", "").upper()
    for sym_key, enabled in config.items():
        if sym_key.upper() in raw:
            return enabled
    return False

def get_ctrader_symbol_config(ticker):
    config = load_config(SYMBOLS_CTRADER_FILE, DEFAULT_CTRADER_CONFIG)
    raw = ticker.replace(".P", "").replace("/", "").replace(":", "").upper()
    for sym_key, data in config.items():
        if sym_key.upper() in raw:
            return data
    return {"enabled": False, "lot": 0.01}

def get_formatted_symbol(symbol):
    if not exchange:
        return symbol
    try:
        raw = symbol.replace('.P', '').replace('/', '').replace(':', '').strip().upper()
        if "XAU" in raw or "GOLD" in raw:
            return "GOLD/USDT:USDT"
        if "EUR" in raw:
            return "EURUSD/USDT:USDT"
        markets = exchange.load_markets(params={'type': 'swap'})
        base_currency = raw.replace('USDT', '')
        for m_symbol, market in markets.items():
            if market.get('swap', False):
                clean_market_symbol = m_symbol.replace('.P', '').replace('/', '').replace(':', '').upper()
                if clean_market_symbol == raw or clean_market_symbol.startswith(f"{base_currency}USDT"):
                    return m_symbol
        return f"{base_currency}/USDT:USDT"
    except Exception:
        base = symbol.replace('USDT', '').replace('.P', '').strip().upper()
        return f"{base}/USDT:USDT"

def close_opposite_positions(symbol, new_action): 
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
                exchange.create_order(symbol=formatted_symbol, type='market', side=close_side, amount=contracts, params={'positionSide': target_position_side}) 
                closed_info += f"\n🔄 **Попередню протилежну позицію ({target_position_side}) закрито по ринку!**" 
        return closed_info 
    except Exception as e: 
        return f"\n⚠ Помилка закриття попередньої позиції: {str(e)}"

def execute_move_be(symbol, entry_price):
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
                exchange.create_order(symbol=formatted_symbol, type='STOP_MARKET', side=sl_side, amount=contracts, params={'stopPrice': sl_price_formatted, 'positionSide': pos_side})
                be_reports.append(f"🛡 **Stop Loss перенесено в BE (`{sl_price_formatted}`) для {formatted_symbol} ({pos_side})**")
        return "\n".join(be_reports) if be_reports else f"ℹ️ Для {formatted_symbol} не знайдено відкритих позицій."
    except Exception as e:
        return f"⚠ Помилка переносу SL в BE: {str(e)}"

def execute_bingx_trade(symbol, action, price, sl, tp1, tp2):  
    if not exchange:  
        return "⚠️ BingX API ключі не знайдені."  
    try:  
        formatted_symbol = get_formatted_symbol(symbol)  
        is_buy = action.upper() == 'BUY'  
        side = 'buy' if is_buy else 'sell'  
        sl_side = 'sell' if is_buy else 'buy'  
        position_side = 'LONG' if is_buy else 'SHORT'  
        closed_info = close_opposite_positions(symbol, action)

        try:
            open_orders = exchange.fetch_open_orders(formatted_symbol)
            for ord in open_orders:
                if ord.get('info', {}).get('positionSide') == position_side:
                    exchange.cancel_order(ord['id'], formatted_symbol)
        except Exception:
            pass

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

        order = exchange.create_order(symbol=formatted_symbol, type='market', side=side, amount=amount, params={'positionSide': position_side})  
        sl_report = ""  
        try:  
            exchange.create_order(symbol=formatted_symbol, type='STOP_MARKET', side=sl_side, amount=amount, params={'stopPrice': sl_price_formatted, 'positionSide': position_side})  
            sl_report = f"🛑 **SL зафіксовано:** `{sl_price_formatted}`\n"  
        except Exception as sl_err:  
            sl_report = f"⚠️ **Помилка виставлення SL:** {str(sl_err)}\n"  

        tp_report = ""  
        try:  
            tp1_amount = float(exchange.amount_to_precision(formatted_symbol, amount * 0.33))  
            exchange.create_order(symbol=formatted_symbol, type='TAKE_PROFIT_MARKET', side=sl_side, amount=tp1_amount, params={'stopPrice': tp1_price_formatted, 'positionSide': position_side})  
            tp_report = f"🎯 **TP1 зафіксовано (33%):** `{tp1_price_formatted}`\n"  
        except Exception as tp_err:  
            tp_report = f"⚠️ **Помилка виставлення TP1:** {str(tp_err)}\n"  

        return (  
            f"✅ **Угоду успішно відкрито на BingX Futures!**\n"  
            f"Інструмент: `{formatted_symbol}` ({position_side})\n"  
            f"Об'єм: `${position_size_usdt}` (Маржа: `${margin_usdt}` x{leverage})\n"  
            f"{closed_info}{sl_report}{tp_report}"  
            f"ID Ордера: `{order['id']}`"  
        )  
    except Exception as e:  
        return f"❌ **Помилка відкриття угоди на BingX:** {str(e)}"  

# ----------------------------------------------------
# 2. cTrader (FxPro) Торгові функції та токени
# ----------------------------------------------------
CTRADER_API_URL = "https://api.spotware.com" # Базовий шлюз OpenAPI (або брокерський ендпоінт)

def refresh_ctrader_token():
    refresh_token = CTRADER_REFRESH_TOKEN_ENV
    print(f"ДЕБАГ: CTRADER_REFRESH_TOKEN з env наявний: {bool(refresh_token)}")
    if not refresh_token and os.path.exists(CTRADER_TOKEN_FILE):
        try:
            with open(CTRADER_TOKEN_FILE, "r") as f:
                token_data = json.load(f)
            refresh_token = token_data.get("refresh_token")
            print("ДЕБАГ: Знайдено refresh_token у локальному файлі token.json")
        except Exception as e:
            print(f"ДЕБАГ: Помилка читання файлу токена: {e}")
            pass
            
    if not refresh_token:
        print("ДЕБАГ: Refresh token взагалі відсутній!")
        return None
        
    try:
        token_url = "https://connect.spotware.com/apps/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": CTRADER_CLIENT_ID,
            "client_secret": CTRADER_CLIENT_SECRET,
            "refresh_token": refresh_token
        }
        res = requests.post(token_url, data=payload)
        print(f"ДЕБАГ: Відповідь від Spotware token API: статус {res.status_code}, текст: {res.text}")
        if res.status_code == 200:
            new_token_data = res.json()
            with open(CTRADER_TOKEN_FILE, "w") as f:
                json.dump(new_token_data, f)
            return new_token_data.get("access_token")
    except Exception as e:
        print(f"Помилка оновлення cTrader token: {str(e)}")
    return None

def get_ctrader_access_token():
    # Спочатку перевіряємо прямий access token з енв Render
    direct_token = os.environ.get("CTRADER_ACCESS_TOKEN")
    if direct_token:
        print("ДЕБАГ: Використовується прямий CTRADER_ACCESS_TOKEN з env Render")
        return direct_token
        
    # Якщо його немає, пробуємо через звичні файли/refresh
    if os.path.exists(CTRADER_TOKEN_FILE):
        try:
            with open(CTRADER_TOKEN_FILE, "r") as f:
                token_data = json.load(f)
            if "access_token" in token_data:
                return token_data.get("access_token")
        except Exception:
            pass
    return refresh_ctrader_token()

def execute_ctrader_trade(symbol, action, price, sl, tp1, lot_size):
    access_token = get_ctrader_access_token()
    if not access_token or not CTRADER_ACCOUNT_ID:
        return "⚠️ cTrader не авторизовано або відсутній Account ID."
    
    try:
        clean_symbol = symbol.replace('.P', '').replace('/', '').replace(':', '').strip().upper()
        # В cTrader об'єм задається в сотих долях цента / одиницях базової валюти (1 лот = 100 000)
        volume_units = int(float(lot_size) * 100000)
        trade_side = "BUY" if action.upper() == "BUY" else "SELL"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # Офіційний REST ендпоінт OpenAPI Spotware для створення ринкового ордера
        # (Базовий шлюз OpenAPI редиректить запит на відповідний датацентр брокера)
        order_url = f"https://api.spotware.com/v1/accounts/{CTRADER_ACCOUNT_ID}/orders"
        
        payload = {
            "symbol": clean_symbol,
            "tradeSide": trade_side,
            "volume": volume_units,
            "orderType": "MARKET",
            "stopLoss": float(sl),
            "takeProfit": float(tp1)
        }
        
        print(f"Відправка реального ордера в cTrader OpenAPI: {payload}")
        res = requests.post(order_url, json=payload, headers=headers, timeout=10)
        
        print(f"Відповідь від cTrader Execution API: статус {res.status_code}, текст: {res.text}")
        
        if res.status_code in [200, 201]:
            resp_data = res.json()
            order_id = resp_data.get("orderId", "Невідомо")
            return (
                f"✅ **Успішно відкрито угоду на FxPro cTrader!**\n"
                f"Інструмент: `{clean_symbol}` ({trade_side})\n"
                f"Об'єм: `{lot_size}` лотів ({volume_units} одиниць)\n"
                f"Вхід: `{price}` | SL: `{sl}` | TP1: `{tp1}`\n"
                f"🏛 Рахунок: `{CTRADER_ACCOUNT_ID}`\n"
                f"ID Ордера: `{order_id}`"
            )
        else:
            return f"❌ **Помилка виконання cTrader (Статус {res.status_code}):** {res.text}"
            
    except Exception as e:
        return f"❌ **Помилка запиту до cTrader API:** {str(e)}"

def execute_ctrader_move_be(symbol, entry_price):
    access_token = get_ctrader_access_token()
    if not access_token or not CTRADER_ACCOUNT_ID:
        return "⚠️ cTrader не авторизовано."
    
    clean_symbol = symbol.replace('.P', '').replace('/', '').replace(':', '').strip().upper()
    return f"🛡 **cTrader BE:** Надіслано запит на перенесення SL в безубиток (`{entry_price}`) для `{clean_symbol}`."

# ----------------------------------------------------
# 3. Головна обробка сигналів
# ----------------------------------------------------
# ----------------------------------------------------
# 3. Головна обробка сигналів
# ----------------------------------------------------
def process_signal(data):  
    print(f"Отримано дані від TradingView: {data}") # ДЕБАГ: виводимо весь JSON у логи
    
    if not data or (float(data.get("price", 0)) == 0 and str(data.get("action", "")).upper() != "MOVE_BE"):
        print(f"Порожній запит або ціна 0. Дані: {data}")
        return

    action = str(data.get("action", "BUY")).upper()  
    ticker = data.get("ticker", "XAUUSD")  
    raw_ticker = str(ticker).replace('.P', '').replace('/', '').replace(':', '').strip().upper()
    is_fx_or_gold = "XAU" in raw_ticker or "GOLD" in raw_ticker or raw_ticker in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "USDCHF"]

    if is_fx_or_gold and is_weekend_closed():
        print(f"ℹ Сигнал {action} для {ticker} проігноровано (ринок закритий на вихідні).")
        return
  
    if action == "MOVE_BE":
        if is_fx_or_gold:
            if not get_exchange_state(TRADING_CTRADER_FILE):
                return
            sym_conf = get_ctrader_symbol_config(ticker)
            if not sym_conf["enabled"]:
                return
            entry_price = data.get("entry_price")
            be_result = execute_ctrader_move_be(ticker, entry_price)
        else:
            if not get_exchange_state(TRADING_BINGX_FILE) or not is_bingx_symbol_enabled(ticker):
                return
            entry_price = data.get("entry_price")
            formatted_symbol = get_formatted_symbol(ticker)
            be_result = execute_move_be(formatted_symbol, entry_price)
        
        if "🛡" in be_result:
            send_telegram(f"⚡️ **СИГНАЛ BE ДЛЯ {ticker}:**\n{be_result}")
        return

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
Надай відповідь ЧІТКО за структурою:  
1. **ФІНАЛЬНИЙ ВЕРДИКТ:** Напиши **[APPROVED]** або **[REJECTED]** у першому ж рядку та короткий підсумок.  
2. **Оцінка Risk-to-Reward.**  
3. **Контекст SMC/FVG.**  
Не використовуй символи ### для заголовків.  
"""  
  
    try:  
        response = client.chat.completions.create(  
            model="gpt-4o-mini",  
            messages=[{"role": "user", "content": prompt}],  
            max_tokens=750,
            timeout=15  # Обмежуємо час очікування відповіді від ШІ до 15 секунд
        )  
        ai_verdict = response.choices[0].message.content  
    except Exception as e:  
        ai_verdict = f"**ФІНАЛЬНИЙ ВЕРДИКТ:** [APPROVED]\n⚠️ Помилка/таймаут запиту до ШІ: {str(e)}"  

    trade_report = ""  
    
    if is_fx_or_gold:
        ai_filter_enabled = get_exchange_state(AI_FILTER_CTRADER_FILE, default=True)
        should_trade = (not ai_filter_enabled) or ("[APPROVED]" in ai_verdict)
        
        if get_exchange_state(TRADING_CTRADER_FILE):
            sym_conf = get_ctrader_symbol_config(ticker)
            if sym_conf["enabled"]:
                if should_trade:
                    trade_report = "\n\n" + execute_ctrader_trade(ticker, action, price, sl, tp1, sym_conf["lot"])
                else:
                    trade_report = f"\n\n🛑 ШІ відхилив сигнал (`[REJECTED]`), угоду в cTrader пропущено через увімкнену фільтрацію."
            else:
                trade_report = f"\n\n👁 Моніторинг: для `{ticker}` автоторгівлю в cTrader вимкнено."
        else:
            trade_report = "\n\n⏸ Автоторгівлю cTrader вимкнено."
    else:
        ai_filter_enabled = get_exchange_state(AI_FILTER_BINGX_FILE, default=True)
        should_trade = (not ai_filter_enabled) or ("[APPROVED]" in ai_verdict)
        
        if get_exchange_state(TRADING_BINGX_FILE):
            if is_bingx_symbol_enabled(ticker):
                if should_trade:
                    trade_report = "\n\n" + execute_bingx_trade(ticker, action, price, sl, tp1, tp2)  
                else:
                    trade_report = f"\n\n🛑 ШІ відхилив сигнал (`[REJECTED]`), угоду на BingX пропущено через увімкнену фільтрацію."
            else:
                trade_report = f"\n\n👁 Моніторинг: для `{ticker}` автоторгівлю на BingX вимкнено."
        else:  
            trade_report = "\n\n⏸ Автоторгівлю BingX вимкнено."  

    msg = (  
        f"⚡️ **НОВИЙ СИГНАЛ: {ticker} ({action})**\n\n"  
        f"📍 **Вхід:** `{price}`\n"  
        f"🛑 **SL:** `{sl}`\n"  
        f"🎯 **TP1:** `{tp1}`\n\n"  
        f"🤖 **Аналіз ШІ:**\n{ai_verdict}{trade_report}"  
    )  
    send_telegram(msg, get_main_keyboard())

# ----------------------------------------------------
# cTrader OAuth Ендпоінти
# ----------------------------------------------------
@app.route('/ctrader/login')
def ctrader_login():
    redirect_uri = "https://trade-ai-webhook.onrender.com/ctrader/callback"
    auth_url = f"https://connect.spotware.com/apps/auth?client_id={CTRADER_CLIENT_ID}&redirect_uri={redirect_uri}&scope=trading"
    return redirect(auth_url)

@app.route('/ctrader/callback')
def ctrader_callback():
    code = request.args.get('code')
    if not code:
        return "Помилка: не отримано авторизаційний код від cTrader.", 400

    redirect_uri = "https://trade-ai-webhook.onrender.com/ctrader/callback"
    token_url = "https://connect.spotware.com/apps/token"
    
    payload = {
        "grant_type": "authorization_code",
        "client_id": CTRADER_CLIENT_ID,
        "client_secret": CTRADER_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "code": code
    }
    
    res = requests.post(token_url, data=payload)
    if res.status_code == 200:
        token_data = res.json()
        refresh_token = token_data.get("refresh_token")
        
        with open(CTRADER_TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
            
        send_telegram(f"✅ **cTrader успішно авторизовано!**\nВаш Refresh Token:\n`{refresh_token}`")
        
        return f"""
            <h3>Успіх! cTrader авторизовано.</h3>
            <p>Ваш <b>Refresh Token</b> (скопіюйте його для додавання у змінні Render як <code>CTRADER_REFRESH_TOKEN</code>):</p>
            <textarea rows="4" cols="80" style="font-family:monospace;">{refresh_token}</textarea>
            <p>Можете закрити цю сторінку.</p>
        """, 200
    else:
        return f"<h3>Помилка авторизації cTrader:</h3><pre>{res.text}</pre>", 400

@app.route('/', methods=['POST', 'GET'])
@app.route('/webhook', methods=['POST', 'GET'])  
def webhook():  
    if request.method == 'GET':
        return "TradeAI Webhook Server is Live!", 200

    data = request.get_json(silent=True) or {}  
  
    if "callback_query" in data:
        callback = data["callback_query"]
        cb_data = callback.get("data", "")
        
        if cb_data == "toggle_ai_bingx":
            current = get_exchange_state(AI_FILTER_BINGX_FILE, default=True)
            set_exchange_state(AI_FILTER_BINGX_FILE, not current)
            message_id = callback["message"]["message_id"]
            chat_id = callback["message"]["chat"]["id"]
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
            requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "reply_markup": get_bingX_inline_keyboard()})
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": callback["id"], "text": "Фільтрацію ШІ для BingX змінено!"})
            
        elif cb_data == "toggle_ai_ctrader":
            current = get_exchange_state(AI_FILTER_CTRADER_FILE, default=True)
            set_exchange_state(AI_FILTER_CTRADER_FILE, not current)
            message_id = callback["message"]["message_id"]
            chat_id = callback["message"]["chat"]["id"]
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
            requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "reply_markup": get_ctrader_inline_keyboard()})
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": callback["id"], "text": "Фільтрацію ШІ для cTrader змінено!"})

        elif cb_data.startswith("bingx_"):
            sym_key = cb_data.replace("bingx_", "")
            config = load_config(SYMBOLS_BINGX_FILE, DEFAULT_BINGX_CONFIG)
            if sym_key in config:
                config[sym_key] = not config[sym_key]
                save_config(SYMBOLS_BINGX_FILE, config)
                
            message_id = callback["message"]["message_id"]
            chat_id = callback["message"]["chat"]["id"]
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
            requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "reply_markup": get_bingX_inline_keyboard()})
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": callback["id"]})
            
        elif cb_data.startswith("ctrader_toggle_"):
            sym_key = cb_data.replace("ctrader_toggle_", "")
            config = load_config(SYMBOLS_CTRADER_FILE, DEFAULT_CTRADER_CONFIG)
            if sym_key in config:
                config[sym_key]["enabled"] = not config[sym_key]["enabled"]
                save_config(SYMBOLS_CTRADER_FILE, config)
                
            message_id = callback["message"]["message_id"]
            chat_id = callback["message"]["chat"]["id"]
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
            requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "reply_markup": get_ctrader_inline_keyboard()})
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": callback["id"]})
            
        return jsonify({"status": "callback processed"}), 200

    if "message" in data and "text" in data["message"]:  
        text = data["message"]["text"]  
        
        if "BingX" in text and "Налаштування" not in text:
            current_state = get_exchange_state(TRADING_BINGX_FILE)
            new_state = not current_state
            set_exchange_state(TRADING_BINGX_FILE, new_state)
            status_msg = "✅ **Автоторгівлю на BingX УВІМКНЕНО!**" if new_state else "⏸ **Автоторгівлю на BingX ВИМКНЕНО!**"
            send_telegram(status_msg, get_main_keyboard())
            
        elif "cTrader" in text and "Налаштування" not in text:
            current_state = get_exchange_state(TRADING_CTRADER_FILE)
            new_state = not current_state
            set_exchange_state(TRADING_CTRADER_FILE, new_state)
            status_msg = "✅ **Автоторгівлю на cTrader УВІМКНЕНО!**" if new_state else "⏸ **Автоторгівлю на cTrader ВИМКНЕНО!**"
            send_telegram(status_msg, get_main_keyboard())
            
        elif text == "⚙️ Налаштування BingX":
            send_telegram("⚙️ **Налаштування монет BingX:**", get_bingX_inline_keyboard())
        elif text == "⚙️ Налаштування cTrader":
            send_telegram("⚙️ **Налаштування інструментів та лотів cTrader:**", get_ctrader_inline_keyboard())
        elif text in ["📊 Стан системи", "/start"]:  
            bingx_status = "🟢 Активна" if get_exchange_state(TRADING_BINGX_FILE) else "🔴 Вимкнена"
            ctrader_status = "🟢 Активна" if get_exchange_state(TRADING_CTRADER_FILE) else "🔴 Вимкнена"
            has_ctrader = "🟢 Підключено" if os.path.exists(CTRADER_TOKEN_FILE) or os.environ.get("CTRADER_REFRESH_TOKEN") else "🔴 Не авторизовано"
            
            msg = (
                f"⚙️ **Статус BingX:** {bingx_status}\n"
                f"⚙️ **Статус cTrader (FxPro):** {ctrader_status}\n"
                f"🏛 **cTrader OAuth:** {has_ctrader}"
            )
            send_telegram(msg, get_main_keyboard())  
        return jsonify({"status": "telegram message processed"}), 200

    threading.Thread(target=process_signal, args=(data,)).start()  
    return jsonify({"status": "success"}), 200  

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
