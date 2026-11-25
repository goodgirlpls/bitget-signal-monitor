import os
import time
import hmac
import hashlib
import base64
import json
import math
import urllib.request
import urllib.error
import urllib.parse

from datetime import datetime, timezone

from monitor import (
    get_candles,
    analyze_signal,
    SYMBOLS,
    CAPITAL,
    MAX_RISK,
    MAX_DAILY_LOSS,
    MAX_LEVERAGE,
    MAX_POSITIONS,
    MIN_RR,
    ENTRY_FEE,
    EXIT_FEE,
    SLIPPAGE,
    VERSION
)


BASE_URL = "https://api.bitget.com"

API_KEY = os.environ.get("BITGET_DEMO_API_KEY")
SECRET_KEY = os.environ.get("BITGET_DEMO_SECRET_KEY")
PASSPHRASE = os.environ.get("BITGET_DEMO_PASSPHRASE")

MODE = os.environ.get("TRADER_MODE", "check")

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"
MARGIN_MODE = "crossed"

EXECUTION_ENABLED = False


def demo_get(path, params=None):

    timestamp = str(int(time.time() * 1000))
    request_path = path

    if params:
        request_path += "?" + urllib.parse.urlencode(params)

    message = timestamp + "GET" + request_path

    signature = base64.b64encode(
        hmac.new(
            SECRET_KEY.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).digest()
    ).decode("utf-8")

    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
        "paptrading": "1",
        "locale": "en-US"
    }

    request = urllib.request.Request(
        BASE_URL + request_path,
        headers=headers,
        method="GET"
    )

    try:
        with urllib.request.urlopen(
            request, timeout=20
        ) as response:
            result = json.load(response)

    except urllib.error.HTTPError as error:
        print("HTTP status:", error.code)

        try:
            details = json.loads(
                error.read().decode("utf-8")
            )

            print("Bitget error:", details.get("code"))

            msg = str(details.get("msg", ""))

            for secret in (
                API_KEY, SECRET_KEY, PASSPHRASE
            ):
                if secret:
                    msg = msg.replace(secret, "***")

            print(msg)

        except Exception:
            print("Unable to read API error")

        raise RuntimeError("Bitget request failed") from None

    if result.get("code") != "00000":
        raise RuntimeError(
            f"Bitget API error: {result.get('code')}"
        )

    return result.get("data")


def get_accounts():
    return demo_get(
        "/api/v2/mix/account/accounts",
        {"productType": PRODUCT_TYPE}
    )


def get_positions():
    return demo_get(
        "/api/v2/mix/position/all-position",
        {
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN
        }
    )


def get_pending_orders():
    return demo_get(
        "/api/v2/mix/order/orders-pending",
        {"productType": PRODUCT_TYPE}
    )


def send_telegram(message):

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram secrets missing")
        return

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request, timeout=20
        ) as response:
            result = json.load(response)

        if result.get("ok"):
            print("TELEGRAM SENT")
        else:
            print("Telegram rejected notification")

    except Exception:
        print("Telegram notification failed")


def number(value):

    result = float(value)

    if not math.isfinite(result):
        raise RuntimeError("Invalid numeric data")

    return result


def account_snapshot():

    accounts = get_accounts()

    if not isinstance(accounts, list):
        raise RuntimeError("Invalid account response")

    account = next(
        (
            a for a in accounts
            if a.get("marginCoin") == MARGIN_COIN
        ),
        None
    )

    if account is None:
        raise RuntimeError("USDT account missing")

    equity = number(account["accountEquity"])
    available = number(account["available"])

    positions = get_positions()

    if not isinstance(positions, list):
        raise RuntimeError("Invalid positions response")

    active = [
        p for p in positions
        if number(p["total"]) > 0
    ]

    orders = get_pending_orders()

    if not isinstance(orders, dict):
        raise RuntimeError("Invalid orders response")

    pending = orders.get("entrustedList") or []

    if not isinstance(pending, list):
        raise RuntimeError("Invalid pending order list")

    return {
        "equity": equity,
        "available": available,
        "positions": active,
        "pending": pending
    }


def show_account(snapshot):

    print("\n===== DEMO ACCOUNT =====")
    print("Equity:", snapshot["equity"])
    print("Available:", snapshot["available"])
    print("Active positions:", len(snapshot["positions"]))
    print("Pending orders:", len(snapshot["pending"]))

    for position in snapshot["positions"]:
        print(
            position.get("symbol"),
            position.get("holdSide"),
            position.get("total"),
            position.get("unrealizedPL")
        )


def check_candidate(signal, snapshot):

    symbol = signal["symbol"]

    if snapshot["equity"] <= 0:
        return False, "Account equity is zero"

    if snapshot["available"] <= 0:
        return False, "No available balance"

    if len(snapshot["positions"]) >= MAX_POSITIONS:
        return False, "Maximum positions reached"

    if any(
        p.get("symbol") == symbol
        for p in snapshot["positions"]
    ):
        return False, "Existing position"

    if any(
        o.get("symbol") == symbol
        for o in snapshot["pending"]
    ):
        return False, "Existing pending order"

    if signal["estimated_loss"] > MAX_RISK + 1e-8:
        return False, "Risk exceeds maximum"

    if signal["net_rr"] < MIN_RR - 1e-8:
        return False, "Risk reward too low"

    age = (
        int(time.time() * 1000)
        - signal["candle_time"]
    )

    if age < 0 or age >= 900000:
        return False, "Signal expired"

    return True, "Basic checks passed"


def scan_market(snapshot):

    print("\n===== MARKET SCAN =====")

    candidates = []

    for symbol in SYMBOLS:

        candles = get_candles(symbol)
        signal = analyze_signal(symbol, candles)

        if signal is None:
            continue

        approved, reason = check_candidate(
            signal,
            snapshot
        )

        print("Precheck:", approved, reason)

        if approved:
            candidates.append(signal)

    priority = {"A": 0, "B": 1, "C": 2}

    candidates.sort(
        key=lambda s: priority.get(s["strategy"], 99)
    )

    if not candidates:
        print("NO ELIGIBLE CANDIDATES")
        return

    for signal in candidates:

        message = (
            "🤖 Bitget Demo 交易预检\n\n"
            f"策略：{signal['strategy']}\n"
            f"币种：{signal['symbol']}\n"
            f"方向：{signal['direction']}\n"
            f"原因：{signal['reason']}\n\n"
            f"参考入场：{signal['entry']:.6f}\n"
            f"参考止损：{signal['stop']:.6f}\n"
            f"参考止盈：{signal['target']:.6f}\n"
            f"理论数量：{signal['quantity']:.8f}\n"
            f"计划风险：{signal['estimated_loss']:.2f} U\n"
            f"净盈亏比：1:{signal['net_rr']:.2f}\n\n"
            "状态：基础预检通过\n"
            "尚未验证每日亏损及交易所端保护订单。\n"
            "未提交任何订单。"
        )

        send_telegram(message)

    print("ORDER EXECUTION DISABLED")


def report(snapshot):

    equity = snapshot["equity"]
    difference = equity - CAPITAL
    percentage = difference / CAPITAL * 100

    message = (
        "📊 Bitget Demo 账户报告\n\n"
        f"初始参考本金：{CAPITAL:.2f} U\n"
        f"当前权益：{equity:.2f} U\n"
        f"可用余额：{snapshot['available']:.2f} U\n"
        f"权益变化：{difference:+.2f} U\n"
        f"权益变化率：{percentage:+.2f}%\n"
        f"当前持仓：{len(snapshot['positions'])}\n"
        f"普通挂单：{len(snapshot['pending'])}\n\n"
        "注意：权益变化包含浮动盈亏，"
        "未剔除模拟资金追加或减少。"
    )

    print(message)
    send_telegram(message)


def main():

    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        raise RuntimeError("Missing Demo API credentials")

    if MODE not in ("check", "auto_trade", "report"):
        raise RuntimeError("Invalid trader mode")

    print("MODE: DEMO ONLY")
    print("STRATEGY VERSION:", VERSION)
    print("TRADER MODE:", MODE)
    print("ORDER EXECUTION: DISABLED")

    print("Capital:", CAPITAL)
    print("Maximum planned risk:", MAX_RISK)
    print("Daily planned loss limit:", MAX_DAILY_LOSS)
    print("Leverage ceiling:", MAX_LEVERAGE)
    print("Maximum positions:", MAX_POSITIONS)
    print("Minimum net RR:", MIN_RR)
    print("Intended margin mode:", MARGIN_MODE)

    snapshot = account_snapshot()
    show_account(snapshot)

    if MODE == "check":
        print("ACCOUNT CHECK PASSED")

    elif MODE == "auto_trade":
        scan_market(snapshot)

    elif MODE == "report":
        report(snapshot)

    print("No orders were placed.")


if __name__ == "__main__":
    main()
