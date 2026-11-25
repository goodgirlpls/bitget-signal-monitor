import os
import time
import hmac
import hashlib
import base64
import json
import urllib.request
import urllib.error

BASE_URL = "https://api.bitget.com"

API_KEY = os.environ.get("BITGET_DEMO_API_KEY")
SECRET_KEY = os.environ.get("BITGET_DEMO_SECRET_KEY")
PASSPHRASE = os.environ.get("BITGET_DEMO_PASSPHRASE")


def check_credentials():
    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        raise RuntimeError("Missing Bitget Demo API credentials")


def demo_get(path, query=""):
    timestamp = str(int(time.time() * 1000))

    request_path = path
    if query:
        request_path += "?" + query

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
        raise RuntimeError(
            f"Bitget HTTP error: {error.code}"
        ) from None

    if result.get("code") != "00000":
        raise RuntimeError(
            f"Bitget API error: {result.get('code')}"
        )

    return result.get("data")


def main():
    check_credentials()

    print("MODE: DEMO ONLY")
    print("Connecting to Bitget Demo API...")

    accounts = demo_get(
        "/api/v2/mix/account/accounts",
        "productType=USDT-FUTURES"
    )

    if not isinstance(accounts, list):
        raise RuntimeError("Unexpected account response")

    print("API CONNECTION SUCCESS")

    for account in accounts:
        print("--------------------------")
        print("Currency:", account.get("marginCoin"))
        print("Available:", account.get("available"))
        print("Account equity:", account.get("accountEquity"))
        print("Unrealized PnL:", account.get("unrealizedPL"))

    print("--------------------------")
    print("TEST COMPLETED")
    print("No orders were placed.")


if __name__ == "__main__":
    main()
