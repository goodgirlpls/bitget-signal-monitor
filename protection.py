import time
import math
import json

from execution import demo_get

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"


def number(value):
    result = float(value)

    if not math.isfinite(result):
        raise RuntimeError("Invalid numeric value")

    return result


def get_protection_orders(symbol):
    result = demo_get(
        "/api/v2/mix/order/orders-plan-pending",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "planType": "profit_loss",
            "limit": "100"
        }
    )

    print("PROTECTION RAW RESPONSE:")
    print(json.dumps(result, indent=2))

    if not isinstance(result, dict):
        raise RuntimeError("Invalid protection response")

    orders = result.get("entrustedList")

    if orders is None:
        orders = []

    if not isinstance(orders, list):
        raise RuntimeError("Invalid protection order list")

    return orders


def get_active_positions(symbol):
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
        position
        for position in result
        if position.get("symbol") == symbol
        and number(position["total"]) > 0
    ]


def verify_protection(order):
    symbol = order["symbol"]

    expected_side = (
        "long"
        if order["direction"] == "LONG"
        else "short"
    )

    expected_stop = number(order["stop"])
    expected_target = number(order["target"])
    expected_size = number(order["size"])

    print("===== PROTECTION VERIFICATION =====")
    print("SYMBOL:", symbol)
    print("EXPECTED SIDE:", expected_side)
    print("EXPECTED STOP:", expected_stop)
    print("EXPECTED TARGET:", expected_target)
    print("EXPECTED SIZE:", expected_size)

    for attempt in range(5):
        time.sleep(2)

        positions = get_active_positions(symbol)

        matching_positions = [
            position
            for position in positions
            if position.get("holdSide") == expected_side
        ]

        if len(matching_positions) != 1:
            print("POSITION NOT FOUND OR AMBIGUOUS")
            continue

        position_size = number(
            matching_positions[0]["total"]
        )

        if abs(position_size - expected_size) > 1e-8:
            print("POSITION SIZE MISMATCH")
            continue

        plans = get_protection_orders(symbol)

        stop_size = 0.0
        target_size = 0.0

        print("PROTECTION ORDERS FOUND:", len(plans))

        for plan in plans:
            print("CHECKING PLAN:")
            print(json.dumps(plan, indent=2))

            if plan.get("symbol") != symbol:
                print("REJECTED: SYMBOL MISMATCH")
                continue

            if plan.get("holdSide") != expected_side:
                print("REJECTED: SIDE MISMATCH")
                continue

            if plan.get("planStatus") != "live":
                print("REJECTED: STATUS MISMATCH")
                continue

            plan_type = plan.get("planType")

            trigger = number(
                plan.get("triggerPrice") or 0
            )

            size = number(
                plan.get("size") or 0
            )

            if plan_type in (
                "pos_loss",
                "pos_profit"
            ):
                size = position_size

            if plan_type in (
                "loss_plan",
                "pos_loss"
            ):
                if math.isclose(
                    trigger,
                    expected_stop,
                    rel_tol=0,
                    abs_tol=1e-8
                ):
                    stop_size += size
                else:
                    print("STOP PRICE MISMATCH")

            elif plan_type in (
                "profit_plan",
                "pos_profit"
            ):
                if math.isclose(
                    trigger,
                    expected_target,
                    rel_tol=0,
                    abs_tol=1e-8
                ):
                    target_size += size
                else:
                    print("TARGET PRICE MISMATCH")

            else:
                print("UNKNOWN PLAN TYPE:", plan_type)

        stop_ok = stop_size >= position_size - 1e-8
        target_ok = target_size >= position_size - 1e-8

        print("Protection check:", attempt + 1)
        print("Position size:", position_size)
        print("Stop covered size:", stop_size)
        print("Target covered size:", target_size)

        if stop_ok and target_ok:
            print("PROTECTION VERIFIED")

            return {
                "verified": True,
                "stop_verified": True,
                "target_verified": True,
                "position_size": position_size
            }

    return {
        "verified": False,
        "reason": "Protection could not be confirmed"
    }


def main():
    print("===== DEMO PROTECTION CHECK =====")

    for symbol in [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT"
    ]:
        positions = get_active_positions(symbol)
        plans = get_protection_orders(symbol)

        print("\nSymbol:", symbol)
        print("Active positions:", len(positions))
        print("Protection orders:", len(plans))

    print("\nPROTECTION API CHECK PASSED")
    print("No orders were placed.")


if __name__ == "__main__":
    main()
