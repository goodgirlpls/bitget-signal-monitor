import json
import urllib.request
import urllib.parse

from decimal import Decimal


CONTRACT_API = (
    "https://api.bitget.com"
    "/api/v2/mix/market/contracts"
)

PRODUCT_TYPE = "USDT-FUTURES"

SUPPORTED_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT"
)


def positive_decimal(value):

    result = Decimal(str(value))

    if not result.is_finite() or result <= 0:
        raise ValueError("Invalid positive decimal")

    return result


def decimal_places(value):

    result = Decimal(str(value))

    if not result.is_finite():
        raise ValueError("Invalid decimal places")

    if result != result.to_integral_value():
        raise ValueError("Invalid decimal places")

    if not 0 <= result <= 18:
        raise ValueError("Invalid decimal places")

    return int(result)


def parse_contract(data):

    symbol = data["symbol"]

    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError("Unsupported symbol")

    if data["symbolType"] != "perpetual":
        raise ValueError("Not a perpetual contract")

    if data["quoteCoin"] != "USDT":
        raise ValueError("Unexpected quote coin")

    if "USDT" not in data["supportMarginCoins"]:
        raise ValueError("USDT margin unsupported")

    if data["symbolStatus"] != "normal":
        raise ValueError(
            f"{symbol} is not available for trading"
        )

    price_place = decimal_places(
        data["pricePlace"]
    )

    volume_place = decimal_places(
        data["volumePlace"]
    )

    price_end_step = positive_decimal(
        data["priceEndStep"]
    )

    price_step = (
        price_end_step
        * Decimal("10") ** (-price_place)
    )

    size_step = positive_decimal(
        data["sizeMultiplier"]
    )

    min_size = positive_decimal(
        data["minTradeNum"]
    )

    min_notional = positive_decimal(
        data["minTradeUSDT"]
    )

    max_order_size = positive_decimal(
        data["maxOrderQty"]
    )

    min_leverage = positive_decimal(
        data["minLever"]
    )

    max_leverage = positive_decimal(
        data["maxLever"]
    )

    if min_leverage > max_leverage:
        raise ValueError("Invalid leverage limits")

    if min_size > max_order_size:
        raise ValueError("Invalid order size limits")

    quantity_unit = (
        Decimal("10") ** (-volume_place)
    )

    if size_step % quantity_unit != 0:
        raise ValueError("Invalid quantity step")

    price_unit = (
        Decimal("10") ** (-price_place)
    )

    if price_step % price_unit != 0:
        raise ValueError("Invalid price step")

    return {
        "symbol": symbol,
        "price_step": format(price_step, "f"),
        "size_step": format(size_step, "f"),
        "min_size": format(min_size, "f"),
        "min_notional": format(min_notional, "f"),
        "max_order_size": format(
            max_order_size, "f"
        ),
        "price_place": price_place,
        "volume_place": volume_place,
        "min_leverage": format(
            min_leverage, "f"
        ),
        "max_leverage": format(
            max_leverage, "f"
        ),
        "status": data["symbolStatus"]
    }


def fetch_contracts():

    query = urllib.parse.urlencode({
        "productType": PRODUCT_TYPE
    })

    request = urllib.request.Request(
        CONTRACT_API + "?" + query,
        headers={
            "User-Agent": "BitgetDemoTrader/3.2",
            "Accept": "application/json",
            "paptrading": "1"
        },
        method="GET"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            result = json.load(response)

        if not isinstance(result, dict):
            raise ValueError("Invalid API response")

        if result.get("code") != "00000":
            raise ValueError(
                f"API error: {result.get('code')}"
            )

        records = result.get("data")

        if not isinstance(records, list):
            raise ValueError("Invalid contract list")

        contracts = {}

        for record in records:

            symbol = record.get("symbol")

            if symbol not in SUPPORTED_SYMBOLS:
                continue

            if symbol in contracts:
                raise ValueError(
                    f"Duplicate contract: {symbol}"
                )

            contracts[symbol] = parse_contract(
                record
            )

        for symbol in SUPPORTED_SYMBOLS:

            if symbol not in contracts:
                raise ValueError(
                    f"Missing contract: {symbol}"
                )

        return contracts

    except Exception as error:

        raise RuntimeError(
            f"Contract specification error: {error}"
        ) from error


def main():

    contracts = fetch_contracts()

    print("DYNAMIC CONTRACT SPECIFICATIONS")

    for symbol, spec in contracts.items():

        print()
        print("SYMBOL:", symbol)
        print(json.dumps(spec, indent=2))

    print()
    print("ALL CONTRACT SPECIFICATIONS VERIFIED")
    print("No orders were placed.")


if __name__ == "__main__":

    main()
