import json
import urllib.request
import urllib.parse

from trader import demo_get, account_snapshot

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

BASE_URL = "https://api.bitget.com"


def get_contract(symbol):
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "productType": "USDT-FUTURES"
    })

    url = (
        BASE_URL
        + "/api/v2/mix/market/contracts?"
        + params
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BitgetDemoTrader/2.0"}
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        result = json.load(response)

    if result.get("code") != "00000":
        raise RuntimeError("Contract query failed")

    contracts = result.get("data") or []

    for contract in contracts:
        if contract.get("symbol") == symbol:
            return contract

    raise RuntimeError("Contract not found")


def main():
    print("===== DEMO EXECUTION CHECK =====")

    snapshot = account_snapshot()

    print("Equity:", snapshot["equity"])
    print("Available:", snapshot["available"])
    print("Positions:", len(snapshot["positions"]))
    print("Pending orders:", len(snapshot["pending"]))

    for symbol in SYMBOLS:
        print("\n===== " + symbol + " =====")

        account = demo_get(
            "/api/v2/mix/account/account",
            {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT"
            }
        )

        contract = get_contract(symbol)

        print("Position mode:", account.get("posMode"))
        print("Margin mode:", account.get("marginMode"))
        print("Cross leverage:", account.get("crossedMarginLeverage"))

        fields = [
            "minTradeNum",
            "minTradeUSDT",
            "sizeMultiplier",
            "volumePlace",
            "pricePlace",
            "priceEndStep",
            "minLever",
            "maxLever",
            "symbolStatus",
            "makerFeeRate",
            "takerFeeRate"
        ]

        for field in fields:
            print(field + ":", contract.get(field))

    print("\nEXECUTION CHECK COMPLETED")
    print("No orders were placed.")


if __name__ == "__main__":
    main()
