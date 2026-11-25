import os
import time
import hmac
import hashlib
import base64
import json
import urllib.request
import urllib.error
import urllib.parse

BASE_URL = "https://api.bitget.com"

API_KEY = os.environ.get("BITGET_DEMO_API_KEY")
SECRET_KEY = os.environ.get("BITGET_DEMO_SECRET_KEY")
PASSPHRASE = os.environ.get("BITGET_DEMO_PASSPHRASE")

CAPITAL = 145.0
MAX_RISK = 5.0
MAX_DAILY_LOSS = 10.0
MAX_LEVERAGE = 100
MAX_POSITIONS = 3
MIN_RR = 1.8

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


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
            print("Bitget error code:", details.get("code"))
            msg = str(details.get("msg", ""))

            for secret in (
                API_KEY, SECRET_KEY, PASSPHRASE
            ):
                if secret:
                    msg = msg.replace(secret, "***")

            print("Bitget error message:", msg)

        except (ValueError, UnicodeError):
            print("No readable API error details")

        raise RuntimeError("Bitget request rejected") from None

    if result.get("code") != "00000":
        raise RuntimeError(
            f"Bitget API error: {result.get('code')}"
        )

    return result.get("data")


def get_accounts():
    return demo_get(
        "/api/v2/mix/account/accounts",
        {"productType": "USDT-FUTURES"}
    )


def get_positions():
    return demo_get(
        "/api/v2/mix/position/all-position",
        {
            "productType": "USDT-FUTURES",
            "marginCoin": "USDT"
        }
    )


def get_pending_orders():
    return demo_get(
        "/api/v2/mix/order/orders-pending",
        {"productType": "USDT-FUTURES"}
    )


def main():
    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        raise RuntimeError("Missing Demo API credentials")

    print("MODE: DEMO ONLY")
    print("ORDER EXECUTION: DISABLED")

    print("\n===== ACCOUNT =====")

    accounts = get_accounts()

    if not isinstance(accounts, list):
        raise RuntimeError("Unexpected account response")

    for account in accounts:
        print("Currency:", account.get("marginCoin"))
        print("Available:", account.get("available"))
        print("Equity:", account.get("accountEquity"))
        print("Unrealized PnL:", account.get("unrealizedPL"))

    print("\n===== POSITIONS =====")

    positions = get_positions()

    if not isinstance(positions, list):
        raise RuntimeError("Unexpected position response")

    active_positions = [
        p for p in positions
        if float(p.get("total") or 0) > 0
    ]

    print("Active positions:", len(active_positions))

    for position in active_positions:
        print("--------------------------")
        print("Symbol:", position.get("symbol"))
        print("Direction:", position.get("holdSide"))
        print("Quantity:", position.get("total"))
        print("Entry:", position.get("openPriceAvg"))
        print("Leverage:", position.get("leverage"))
        print("Margin mode:", position.get("marginMode"))
        print("Unrealized PnL:", position.get("unrealizedPL"))
        print("Liquidation price:", position.get("liquidationPrice"))

    print("\n===== PENDING ORDERS =====")

    orders = get_pending_orders()

    if not isinstance(orders, dict):
        raise RuntimeError("Unexpected pending order response")

    pending = orders.get("entrustedList") or []

    if not isinstance(pending, list):
        raise RuntimeError("Unexpected order list")

    print("Pending orders on returned page:", len(pending))

    for order in pending:
        print("--------------------------")
        print("Symbol:", order.get("symbol"))
        print("Side:", order.get("side"))
        print("Price:", order.get("price"))
        print("Size:", order.get("size"))
        print("Status:", order.get("status"))

    print("\n===== RISK SETTINGS =====")

    print("Experiment capital:", CAPITAL)
    print("Maximum planned risk:", MAX_RISK)
    print("Daily loss limit:", MAX_DAILY_LOSS)
    print("Leverage ceiling:", MAX_LEVERAGE)
    print("Maximum positions:", MAX_POSITIONS)
    print("Minimum net R/R:", MIN_RR)

    print("\nALL READ TESTS PASSED")
    print("No orders were placed.")


if __name__ == "__main__":
    main()
