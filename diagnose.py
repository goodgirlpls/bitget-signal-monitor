import time
import json

from execution import demo_get

SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"


def show(title, data):
    print("\n===== " + title + " =====")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    print("MODE: DEMO ONLY")
    print("READ-ONLY DIAGNOSTIC")

    end_time = int(time.time() * 1000)
    start_time = end_time - 86400000

    endpoints = [
        (
            "PENDING PROTECTION ORDERS",
            "/api/v2/mix/order/orders-plan-pending",
            {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "planType": "profit_loss",
                "limit": "100"
            }
        ),
        (
            "HISTORICAL PROTECTION ORDERS",
            "/api/v2/mix/order/orders-plan-history",
            {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "planType": "profit_loss",
                "startTime": str(start_time),
                "endTime": str(end_time),
                "limit": "100"
            }
        ),
        (
            "HISTORICAL POSITIONS",
            "/api/v2/mix/position/history-position",
            {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "startTime": str(start_time),
                "endTime": str(end_time),
                "limit": "100"
            }
        )
    ]

    for title, path, params in endpoints:
        try:
            result = demo_get(path, params)
            show(title, result)

        except Exception as error:
            print(title, "FAILED:", str(error))

    print("\n===== DIAGNOSTIC FINISHED =====")
    print("No orders were placed.")


if __name__ == "__main__":
    main()
