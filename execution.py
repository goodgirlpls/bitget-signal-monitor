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

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"

MAX_LEVERAGE = 100

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT"
]


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

    if float(size) <= 0:
        raise RuntimeError("Invalid order size")

    if min(float(price), float(stop), float(target)) <= 0:
        raise RuntimeError("Invalid price")

    if direction == "LONG":
        if not float(stop) < float(price) < float(target):
            raise RuntimeError("Invalid LONG stop or target")

    if direction == "SHORT":
        if not float(target) < float(price) < float(stop):
            raise RuntimeError("Invalid SHORT stop or target")

    if not client_oid:
        raise RuntimeError("Missing client order ID")

    account = get_symbol_account(symbol)

    if account.get("posMode") != "hedge_mode":
        raise RuntimeError("Unexpected position mode")

    if account.get("marginMode") != "crossed":
        raise RuntimeError("Unexpected margin mode")

    leverage = float(account["crossedMarginLeverage"])

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
        "presetStopSurplusPrice": str(target),
        "presetStopLossPrice": str(stop)
    }

    return demo_post(
        "/api/v2/mix/order/place-order",
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
