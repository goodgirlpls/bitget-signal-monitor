import os
import time
import math
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

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"

MAX_LEVERAGE = 100

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT"
]

def positive_number(value):

    try:
        result = float(value)

    except (TypeError, ValueError, OverflowError):
        raise RuntimeError(
            "Invalid numeric value"
        ) from None

    if not math.isfinite(result) or result <= 0:
        raise RuntimeError(
            "Invalid positive numeric value"
        )

    return result
def demo_request(method, path, params=None, body=None):

    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        raise RuntimeError("Missing Demo credentials")

    method = method.upper()

    if method not in ("GET", "POST"):
        raise RuntimeError("Invalid HTTP method")

    timestamp = str(int(time.time() * 1000))

    request_path = path

    if params:
        request_path += "?" + urllib.parse.urlencode(params)

    body_text = ""

    if body is not None:
        body_text = json.dumps(
            body,
            separators=(",", ":")
        )

    message = (
        timestamp
        + method
        + request_path
        + body_text
    )

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
        data=(
            body_text.encode("utf-8")
            if body is not None
            else None
        ),
        method=method
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:
            result = json.load(response)

    except urllib.error.HTTPError as error:

        print("HTTP status:", error.code)

        try:
            details = json.loads(
                error.read().decode("utf-8")
            )

            print(
                "Bitget error code:",
                details.get("code")
            )

            message = str(details.get("msg", ""))

            for secret in (
                API_KEY,
                SECRET_KEY,
                PASSPHRASE
            ):
                if secret:
                    message = message.replace(
                        secret, "***"
                    )

            print("Bitget message:", message)

        except Exception:
            print("Unable to read error details")

        raise RuntimeError(
            "Demo API request rejected"
        ) from None

    if result.get("code") != "00000":
        raise RuntimeError(
            f"Bitget error: {result.get('code')}"
        )

    return result.get("data")


def demo_get(path, params=None):

    return demo_request(
        "GET",
        path,
        params=params
    )


def demo_post(path, body):

    return demo_request(
        "POST",
        path,
        body=body
    )


def get_symbol_account(symbol):

    if symbol not in SYMBOLS:
        raise RuntimeError("Invalid symbol")

    return demo_get(
        "/api/v2/mix/account/account",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN
        }
    )


def get_history_positions(start_time, end_time):

    return demo_get(
        "/api/v2/mix/position/history-position",
        {
            "productType": PRODUCT_TYPE,
            "startTime": str(start_time),
            "endTime": str(end_time),
            "limit": "100"
        }
    )


def place_order(
    symbol,
    direction,
    size,
    price,
    stop,
    target,
    client_oid
):

    if symbol not in SYMBOLS:
        raise RuntimeError("Invalid symbol")

    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("Invalid direction")

    order_size = positive_number(size)
    order_price = positive_number(price)
    stop_price = positive_number(stop)
    target_price = positive_number(target)

    if direction == "LONG":
        if not stop_price < order_price < target_price:
            raise RuntimeError(
                "Invalid LONG stop or target"
            )

    if direction == "SHORT":
        if not target_price < order_price < stop_price:
            raise RuntimeError(
                "Invalid SHORT stop or target"
            )

    if not client_oid:
        raise RuntimeError("Missing client order ID")

    account = get_symbol_account(symbol)

    if account.get("posMode") != "hedge_mode":
        raise RuntimeError("Unexpected position mode")

    if account.get("marginMode") != "crossed":
        raise RuntimeError("Unexpected margin mode")

    leverage = positive_number(
        account["crossedMarginLeverage"]
    )

    if not 1 <= leverage <= MAX_LEVERAGE:
        raise RuntimeError("Leverage exceeds limit")

    side = "buy" if direction == "LONG" else "sell"

    body = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": str(size),
        "price": str(price),
        "side": side,
        "tradeSide": "open",
        "orderType": "limit",
        "force": "fok",
        "clientOid": client_oid,
        "presetStopLossPrice": str(stop)
    }

    return demo_post(
        "/api/v2/mix/order/place-order",
        body
    )

# =========================
# V4.1 DYNAMIC PROTECTION
# =========================

def get_live_positions(symbol):

    if symbol not in SYMBOLS:
        raise RuntimeError("Invalid symbol")

    result = demo_get(
        "/api/v2/mix/position/all-position",
        {
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN
        }
    )

    if not isinstance(result, list):
        raise RuntimeError("Invalid positions response")

    return [
        p for p in result
        if p.get("symbol") == symbol
        and float(p["total"]) > 0
    ]


def place_partial_take_profit(
    symbol,
    direction,
    size,
    target,
    client_oid
):

    if symbol not in SYMBOLS:
        raise RuntimeError("Invalid symbol")

    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("Invalid direction")

    positive_number(size)
    positive_number(target)

    if not client_oid:
        raise RuntimeError("Missing client order ID")

    hold_side = (
        "long" if direction == "LONG"
        else "short"
    )

    positions = [
        p for p in get_live_positions(symbol)
        if p.get("holdSide") == hold_side
    ]

    if len(positions) != 1:
        raise RuntimeError("Position not confirmed")

    position_size = positive_number(
        positions[0]["total"]
    )

    if float(size) >= position_size:
        raise RuntimeError(
            "Partial TP must be smaller than position"
        )

    body = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
        "planType": "profit_plan",
        "triggerPrice": str(target),
        "triggerType": "fill_price",
        "executePrice": "0",
        "holdSide": hold_side,
        "size": str(size),
        "clientOid": client_oid
    }

    return demo_post(
        "/api/v2/mix/order/place-tpsl-order",
        body
    )


def modify_stop_loss(
    symbol,
    direction,
    order_id,
    old_stop,
    new_stop,
    size,
    plan_type
):

    if symbol not in SYMBOLS:
        raise RuntimeError("Invalid symbol")

    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("Invalid direction")

    if plan_type not in ("loss_plan", "pos_loss"):
        raise RuntimeError("Invalid stop plan type")

    if not order_id:
        raise RuntimeError("Missing stop order ID")

    old_price = positive_number(old_stop)
    new_price = positive_number(new_stop)

    if direction == "LONG":
        if new_price < old_price:
            raise RuntimeError(
                "LONG stop cannot move backward"
            )

    if direction == "SHORT":
        if new_price > old_price:
            raise RuntimeError(
                "SHORT stop cannot move backward"
            )

    if plan_type == "pos_loss":
        size_text = ""
    else:
        positive_number(size)
        size_text = str(size)

    body = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
        "orderId": str(order_id),
        "triggerPrice": str(new_stop),
        "triggerType": "fill_price",
        "executePrice": "0",
        "size": size_text
    }

    return demo_post(
        "/api/v2/mix/order/modify-tpsl-order",
        body
    )
def test_post():

    account = get_symbol_account("BTCUSDT")

    if account.get("marginMode") != "crossed":
        raise RuntimeError("Unexpected margin mode")

    leverage = str(account["crossedMarginLeverage"])

    result = demo_post(
        "/api/v2/mix/account/set-leverage",
        {
            "symbol": "BTCUSDT",
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN,
            "leverage": leverage
        }
    )

    print("POST RESPONSE:", result)

    updated = get_symbol_account("BTCUSDT")

    if str(updated.get("crossedMarginLeverage")) != leverage:
        raise RuntimeError("Leverage verification failed")

    print("POST SIGNATURE TEST PASSED")
    print("Leverage unchanged:", leverage)
    print("No orders were placed.")


def test_history():

    end_time = int(time.time() * 1000)
    start_time = end_time - 86400000

    result = get_history_positions(
        start_time,
        end_time
    )

    print("\n===== HISTORY POSITIONS =====")

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
    )

    print("HISTORY REQUEST COMPLETED")


def main():

    print("===== DEMO EXECUTION MODULE =====")

    for symbol in SYMBOLS:

        account = get_symbol_account(symbol)

        print("\nSymbol:", symbol)
        print("Position mode:", account.get("posMode"))
        print("Margin mode:", account.get("marginMode"))

        print(
            "Current leverage:",
            account.get("crossedMarginLeverage")
        )

        if account.get("posMode") != "hedge_mode":
            raise RuntimeError("Unexpected position mode")

        if account.get("marginMode") != "crossed":
            raise RuntimeError("Unexpected margin mode")

    print("\nEXECUTION MODULE READ CHECK PASSED")
    print("No orders were placed.")


if __name__ == "__main__":
    main()
    test_post()
    test_history()

    print("\n===== EXECUTION MODULE READY =====")
    print("Order function loaded:", callable(place_order))
    print("Automatic order submission is not enabled.")
