
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api.bitget.com/api/v2/mix/market/candles"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def get_candles(symbol):
    params = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "granularity": "15m",
        "limit": "200",
    }

    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BitgetSignalMonitor/1.0"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)

    if result.get("code") != "00000":
        raise RuntimeError(f"Bitget API error: {result}")

    candles = result.get("data", [])

    if len(candles) < 200:
        raise RuntimeError(
            f"{symbol}: only {len(candles)} candles returned"
        )

    return sorted(candles, key=lambda row: int(row[0]))


def main():
    for symbol in SYMBOLS:
        print(f"\nChecking {symbol}...")

        try:
            candles = get_candles(symbol)
            last = candles[-1]

            timestamp = datetime.fromtimestamp(
                int(last[0]) / 1000, tz=timezone.utc
            ).isoformat()

            print("STATUS: SUCCESS")
            print("Candles:", len(candles))
            print("Latest candle UTC:", timestamp)
            print("Open:", last[1])
            print("High:", last[2])
            print("Low:", last[3])
            print("Close:", last[4])
            print("Volume:", last[5])

        except Exception as error:
            print("STATUS: FAILED")
            print("Reason:", error)
            raise


if __name__ == "__main__":
    main()