
import os
import time
import math
import json
import hashlib
import urllib.request
import urllib.parse

from decimal import Decimal, ROUND_DOWN, ROUND_UP
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
    VERSION
)

from risk import (
    ENTRY_FEE,
    EXIT_FEE,
    SLIPPAGE,
    MIN_RR,
    ORB_MIN_RR,
    build_risk_plan,
    risk_per_unit,
    profit_per_unit,
    calculate_rr,
    calculate_target
)
from execution import (
    demo_get,
    get_symbol_account,
    get_history_positions,
    place_order
)

from protection import (
    verify_protection,
    get_protection_orders
)

MODE = os.environ.get("TRADER_MODE", "check")
EXECUTION_ENABLED = False

STRATEGY_MAX_AGE = {
    "A": 900000,
    "B": 900000,
    "C": 900000,
    "D": 60000
}

STRATEGY_MIN_RR = {
    "A": MIN_RR,
    "B": MIN_RR,
    "C": MIN_RR,
    "D": ORB_MIN_RR
}

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"

CONTRACTS = {
    "BTCUSDT": {
        "size_step": "0.0001",
        "price_step": "0.1",
        "min_size": "0.0001"
    },
    "ETHUSDT": {
        "size_step": "0.01",
        "price_step": "0.01",
        "min_size": "0.01"
    },
    "SOLUSDT": {
        "size_step": "0.1",
        "price_step": "0.001",
        "min_size": "0.1"
    }
}


def number(value):

    result = float(value)

    if not math.isfinite(result):
        raise RuntimeError("Invalid numeric value")

    return result


def round_step(value, step, upward=False):

    value = Decimal(str(value))
    step = Decimal(str(step))

    rounding = ROUND_UP if upward else ROUND_DOWN

    result = (
        (value / step).to_integral_value(
            rounding=rounding
        ) * step
    )

    return format(result, "f")


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

    url = (
        f"https://api.telegram.org/bot{token}"
        "/sendMessage"
    )

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

        print("Telegram:", result.get("ok"))

    except Exception:
        print("Telegram notification failed")


def account_snapshot():

    accounts = demo_get(
        "/api/v2/mix/account/accounts",
        {"productType": PRODUCT_TYPE}
    )

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

    positions = demo_get(
        "/api/v2/mix/position/all-position",
        {
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN
        }
    )

    if not isinstance(positions, list):
        raise RuntimeError("Invalid position response")

    active = [
        p for p in positions
        if number(p["total"]) > 0
    ]

    orders = demo_get(
        "/api/v2/mix/order/orders-pending",
        {"productType": PRODUCT_TYPE}
    )

    if not isinstance(orders, dict):
        raise RuntimeError("Invalid pending response")

    if "entrustedList" not in orders:
        raise RuntimeError("Missing pending list")

    pending = orders["entrustedList"] or []

    if not isinstance(pending, list):
        raise RuntimeError("Invalid pending list")

    if len(pending) >= 100:
        raise RuntimeError("Pagination required")

    return {
        "equity": number(account["accountEquity"]),
        "available": number(account["available"]),
        "positions": active,
        "pending": pending
    }


def daily_loss():

    now = datetime.now(timezone.utc)

    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    result = get_history_positions(
        int(start.timestamp() * 1000),
        int(now.timestamp() * 1000)
    )

    if not isinstance(result, dict):
        raise RuntimeError("Invalid history response")

    records = result.get("list")

    if not isinstance(records, list):
        raise RuntimeError("Invalid history list")

    if len(records) >= 100:
        raise RuntimeError("History pagination required")

    loss = 0.0
    net_profit = 0.0

    for position in records:

        pnl = number(position["netProfit"])

        net_profit += pnl

        if pnl < 0:
            loss += abs(pnl)

    return loss, net_profit





def open_position_risk(snapshot):

    total_risk = 0.0

    active_symbols = {
        p["symbol"]
        for p in snapshot["positions"]
    }

    for symbol in SYMBOLS:

        plans = get_protection_orders(symbol)

        if len(plans) >= 100:
            raise RuntimeError("Plan pagination required")

        if plans and symbol not in active_symbols:
            raise RuntimeError(
                f"Residual protection orders: {symbol}"
            )

    for position in snapshot["positions"]:

        symbol = position["symbol"]

        if symbol not in CONTRACTS:
            raise RuntimeError("Unknown active position")

        side = position["holdSide"]
        size = number(position["total"])
        entry = number(position["openPriceAvg"])

        if side not in ("long", "short"):
            raise RuntimeError("Invalid position side")

        if size <= 0 or entry <= 0:
            raise RuntimeError("Invalid position data")

        plans = get_protection_orders(symbol)

        stops = []
        targets = []

        for plan in plans:

            plan_side = plan.get("holdSide")
            pos_side = plan.get("posSide")

            if plan_side and pos_side and plan_side != pos_side:
                raise RuntimeError("Conflicting protection sides")

            plan_side = plan_side or pos_side

            if plan_side != side:
                continue

            if plan.get("planStatus") != "live":
                continue

            plan_type = plan.get("planType")

            trigger = number(
                plan.get("triggerPrice") or 0
            )

            covered_size = number(
                plan.get("size")
                or plan.get("baseVolume")
                or 0
            )

            if plan_type in ("pos_loss", "pos_profit"):
                covered_size = size

            if covered_size < size - 1e-8:
                continue

            if trigger <= 0:
                continue

            if plan_type in ("loss_plan", "pos_loss"):
                stops.append(trigger)

            if plan_type in ("profit_plan", "pos_profit"):
                targets.append(trigger)

        if not stops or not targets:
            raise RuntimeError(
                f"Unverified protection: {symbol}"
            )

        stop = (
            min(stops)
            if side == "long"
            else max(stops)
        )

        risk = size * risk_per_unit(entry, stop)

        unrealized_loss = max(
            0.0,
            -number(position["unrealizedPL"])
        )

        reserved = max(
            risk,
            unrealized_loss
        )

        total_risk += reserved

        print(
            "OPEN RISK:",
            symbol,
            round(reserved, 4)
        )

    return total_risk

def check_candidate(signal, snapshot):

    symbol = signal["symbol"]
    strategy = signal["strategy"]

    if strategy not in STRATEGY_MAX_AGE:
        return False, "Unsupported strategy"

    if symbol not in CONTRACTS:
        return False, "Unsupported symbol"

    if snapshot["equity"] <= 0:
        return False, "No equity"

    if snapshot["available"] <= 0:
        return False, "No available balance"

    if len(snapshot["positions"]) >= MAX_POSITIONS:
        return False, "Maximum positions reached"

    if snapshot["pending"]:
        return False, "Pending orders require review"

    if any(
        p["symbol"] == symbol
        for p in snapshot["positions"]
    ):
        return False, "Existing position"

    age = (
        int(time.time() * 1000)
        - int(signal["candle_time"])
    )

    max_age = STRATEGY_MAX_AGE[strategy]

    if age < 0 or age >= max_age:
        return False, "Signal expired"

    if (
        number(signal["net_rr"]) + 1e-9
        < STRATEGY_MIN_RR[strategy]
    ):
        return False, "Signal RR too low"

    print("APPROVED STRATEGY:", strategy)
    print("SIGNAL AGE:", age)
    print("MAX SIGNAL AGE:", max_age)

    return True, "Approved"

def build_order(signal, snapshot, risk_budget):

    symbol = signal["symbol"]
    direction = signal["direction"]
    strategy = signal["strategy"]

    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("Invalid direction")

    spec = CONTRACTS[symbol]
    account = get_symbol_account(symbol)

    if account.get("posMode") != "hedge_mode":
        raise RuntimeError("Unexpected position mode")

    if account.get("marginMode") != "crossed":
        raise RuntimeError("Unexpected margin mode")

    leverage = number(
        account["crossedMarginLeverage"]
    )

    if not 1 <= leverage <= MAX_LEVERAGE:
        raise RuntimeError("Invalid leverage")

    is_long = direction == "LONG"

    required_rr = STRATEGY_MIN_RR[strategy]

    price_text = round_step(
        signal["entry"],
        spec["price_step"],
        upward=not is_long
    )

    stop_text = round_step(
        signal["stop"],
        spec["price_step"],
        upward=not is_long
    )

    price = number(price_text)
    stop = number(stop_text)

    margin_size = (
        snapshot["available"] * 0.80
        / (
            price / leverage
            + price * ENTRY_FEE
        )
    )

    try:

        plan = build_risk_plan(
            entry=price,
            stop=stop,
            direction=direction,
            risk_budget=risk_budget,
            max_notional=margin_size * price,
            required_rr=required_rr
        )

        target_text = round_step(
            plan["target"],
            spec["price_step"],
            upward=is_long
        )

        target = number(target_text)

        unit_risk = risk_per_unit(
            price, stop
        )

        unit_profit = profit_per_unit(
            price, target
        )

        net_rr = calculate_rr(
            price,
            stop,
            target,
            direction
        )

    except ValueError as error:
        raise RuntimeError(
            f"Risk plan rejected: {error}"
        ) from error

    if net_rr + 1e-9 < required_rr:
        raise RuntimeError(
            "Rounded RR below strategy minimum"
        )

    quantity = min(
        plan["quantity"],
        number(signal["quantity"])
    )

    size_text = round_step(
        quantity,
        spec["size_step"]
    )

    size = number(size_text)

    if size < number(spec["min_size"]):
        raise RuntimeError("Quantity below minimum")

    notional = size * price

    if notional < 5:
        raise RuntimeError("Notional below minimum")

    estimated_loss = size * unit_risk

    if estimated_loss > risk_budget + 1e-8:
        raise RuntimeError("Risk budget exceeded")

    client_oid = "demo-" + hashlib.sha256(
        str(signal["signal_id"]).encode("utf-8")
    ).hexdigest()[:24]

    return {
        "symbol": symbol,
        "coin": symbol.replace("USDT", ""),
        "direction": direction,
        "size": size_text,
        "price": price_text,
        "stop": stop_text,
        "target": target_text,
        "client_oid": client_oid,
        "notional": notional,
        "margin": notional / leverage,
        "estimated_loss": estimated_loss,
        "risk_budget": risk_budget,
        "strategy": strategy,
        "signal_id": signal["signal_id"],
        "net_profit": size * unit_profit,
        "net_rr": net_rr,
        "leverage": leverage
    }


def check_duplicate(order):

    now = int(time.time() * 1000)

    result = demo_get(
        "/api/v2/mix/order/orders-history",
        {
            "symbol": order["symbol"],
            "productType": PRODUCT_TYPE,
            "startTime": str(now - 86400000),
            "endTime": str(now),
            "limit": "100"
        }
    )

    if not isinstance(result, dict):
        raise RuntimeError("Invalid order history")

    if "entrustedList" not in result:
        raise RuntimeError("Missing order history list")

    records = result["entrustedList"] or []

    if not isinstance(records, list):
        raise RuntimeError("Invalid order records")

    if len(records) >= 100:
        raise RuntimeError("History pagination required")

    return any(
        r.get("clientOid") == order["client_oid"]
        for r in records
    )


def execute_order(order, signal):

    if MODE != "auto_trade":
        raise RuntimeError("Invalid execution mode")

    if not EXECUTION_ENABLED:
        print("ORDER EXECUTION DISABLED")
        return False

    if check_duplicate(order):
        print("DUPLICATE SIGNAL - SKIPPED")
        return False

    age = (
        int(time.time() * 1000)
        - int(signal["candle_time"])
    )

    max_age = STRATEGY_MAX_AGE[
        signal["strategy"]
    ]

    if age < 0 or age >= max_age:
        print("SIGNAL EXPIRED")
        print("STRATEGY:", signal["strategy"])
        print("SIGNAL AGE:", age)
        return False

    print("ORDER SUBMISSION")
    print("ORDER STRATEGY:", signal["strategy"])
    print("SIGNAL ID:", signal["signal_id"])
    print("CLIENT OID:", order["client_oid"])
    print(json.dumps(order, indent=2))

    try:

        result = place_order(
            order["symbol"],
            order["direction"],
            order["size"],
            order["price"],
            order["stop"],
            order["target"],
            order["client_oid"]
        )

        if not isinstance(result, dict):
            raise RuntimeError("Invalid order response")

        order_id = result.get("orderId")

        if not order_id:
            raise RuntimeError("Order ID missing")

        detail = None

        for attempt in range(5):

            time.sleep(2)

            detail = demo_get(
                "/api/v2/mix/order/detail",
                {
                    "symbol": order["symbol"],
                    "productType": PRODUCT_TYPE,
                    "orderId": order_id
                }
            )

            if not isinstance(detail, dict):
                raise RuntimeError("Invalid order detail")

            if detail.get("state") in (
                "filled",
                "cancelled",
                "canceled"
            ):
                break

        print("ORDER DETAIL:", detail)

        state = detail.get("state")

        if state in ("cancelled", "canceled"):

            if number(detail.get("baseVolume") or 0) > 0:
                raise RuntimeError("Unexpected partial fill")

            send_telegram(
                "Bitget Demo 未成交\n\n"
                f"币种：{order['symbol']}\n"
                f"数量：{order['size']} {order['coin']}\n"
                f"限价：{order['price']}\n"
                "原因：FOK限价单已取消。\n"
                "不追价，本轮结束。"
            )

            return False

        if state != "filled":
            raise RuntimeError("Uncertain order state")

        actual_size = number(detail["baseVolume"])
        actual_price = number(detail["priceAvg"])

        if abs(actual_size - number(order["size"])) > 1e-8:
            raise RuntimeError("Unexpected fill size")

        protection = verify_protection(order)

        if not protection.get("verified"):
            raise RuntimeError("Protection not verified")

        actual_notional = actual_size * actual_price

        actual_risk = actual_size * risk_per_unit(
            actual_price,
            number(order["stop"])
        )

        if order["direction"] == "LONG":
            valid_prices = (
                number(order["stop"])
                < actual_price
                < number(order["target"])
            )
        else:
            valid_prices = (
                number(order["target"])
                < actual_price
                < number(order["stop"])
            )

        if not valid_prices:
            raise RuntimeError("Unexpected fill price")

        actual_net_profit = actual_size * profit_per_unit(
            actual_price,
            number(order["target"])
        )

        actual_net_rr = calculate_rr(
            actual_price,
            number(order["stop"]),
            number(order["target"]),
            order["direction"]
        )

        print("ACTUAL ESTIMATED PROFIT:", actual_net_profit)
        print("ACTUAL NET RR:", actual_net_rr)

        allowed_risk = min(
            MAX_RISK,
            number(order["risk_budget"])
        )

        if actual_risk > allowed_risk + 1e-8:
            raise RuntimeError(
                f"Filled risk exceeds budget: "
                f"{actual_risk:.4f} > {allowed_risk:.4f}"
            )

        message = (
            "Bitget Demo 自动开仓成功\n\n"
            f"策略：{signal['strategy']}\n"
            f"币种：{order['symbol']}\n"
            f"方向：{order['direction']}\n"
            f"开仓数量：{actual_size} {order['coin']}\n"
            f"实际成交价：{actual_price}\n"
            f"杠杆：{order['leverage']:g}x\n"
            "保证金模式：全仓 Cross\n"
            f"名义仓位：{actual_notional:.2f} U\n"
            f"估算初始保证金："
            f"{actual_notional / order['leverage']:.2f} U\n\n"
            f"止损：{order['stop']}\n"
            f"止盈：{order['target']}\n"
            f"估算止损风险：{actual_risk:.2f} U\n"
            f"计划净利润：{order['net_profit']:.2f} U\n"
            f"计划盈亏比：1:{order['net_rr']:.2f}\n"
            f"成交后预计净利润：{actual_net_profit:.2f} U\n"
            f"成交后预计盈亏比：1:{actual_net_rr:.2f}\n\n"
            "成交状态：已确认\n"
            "止盈止损：已查询确认\n"
            "账户：模拟盘 Demo\n"
            f"订单编号：{order_id}"
        )

        send_telegram(message)

        print("TRADE AND PROTECTION VERIFIED")

        return True

    except Exception:

        send_telegram(
            "Bitget Demo 订单异常警报\n\n"
            f"币种：{order['symbol']}\n"
            f"Client ID：{order['client_oid']}\n"
            "订单可能已提交或成交。\n"
            "请立即检查持仓及止盈止损。\n"
            "本轮停止，不自动重试。"
        )

        raise


def scan_market():

    snapshot = account_snapshot()

    if snapshot["pending"]:
        print("PENDING ORDERS - BLOCKED")
        return

    realized_loss, _ = daily_loss()
    reserved_risk = open_position_risk(snapshot)

    remaining = (
        MAX_DAILY_LOSS
        - realized_loss
        - reserved_risk
    )

    print("REALIZED LOSS:", realized_loss)
    print("OPEN RISK:", reserved_risk)
    print("REMAINING RISK:", remaining)

    if remaining <= 0:
        print("DAILY RISK LIMIT REACHED")
        return

    candidates = []

    for symbol in SYMBOLS:

        candles = get_candles(symbol)
        signal = analyze_signal(symbol, candles)

        if signal is None:
            print(symbol, "NO SIGNAL")
            continue

        approved, reason = check_candidate(
            signal,
            snapshot
        )

        print(symbol, approved, reason)

        if approved:
            candidates.append(signal)

    priority = {
        "D": 0,
        "A": 1,
        "B": 2,
        "C": 3
    }

    candidates.sort(
        key=lambda s: priority.get(s["strategy"], 99)
    )

    if not candidates:
        print("NO ELIGIBLE CANDIDATES")
        return

    for signal in candidates:

        snapshot = account_snapshot()

        approved, reason = check_candidate(
            signal,
            snapshot
        )

        if not approved:
            print("SKIPPED:", reason)
            continue

        realized_loss, _ = daily_loss()
        reserved_risk = open_position_risk(snapshot)

        remaining = (
            MAX_DAILY_LOSS
            - realized_loss
            - reserved_risk
        )

        risk_budget = min(MAX_RISK, remaining)

        if risk_budget <= 0:
            print("NO REMAINING RISK BUDGET")
            return

        try:

            order = build_order(
                signal,
                snapshot,
                risk_budget
            )

        except RuntimeError as error:

            print(
                "ORDER SKIPPED:",
                signal["symbol"],
                "STRATEGY:", signal["strategy"],
                "REASON:", str(error)
            )

            send_telegram(
                "Bitget Demo 未开仓\n\n"
                f"币种：{signal['symbol']}\n"
                f"原因：{error}"
            )

            continue

        execute_order(order, signal)

        # 每轮最多尝试一笔订单。
        # 下一轮重新检查账户及风险。
        return

    print("NO EXECUTABLE ORDERS")


def report(snapshot):

    loss, net_profit = daily_loss()
    equity = snapshot["equity"]

    difference = equity - CAPITAL
    percentage = difference / CAPITAL * 100

    message = (
        "Bitget Demo 账户报告\n\n"
        f"初始本金：{CAPITAL:.2f} U\n"
        f"当前权益：{equity:.2f} U\n"
        f"可用余额：{snapshot['available']:.2f} U\n"
        f"权益变化：{difference:+.2f} U\n"
        f"收益率：{percentage:+.2f}%\n"
        f"今日已平仓净收益：{net_profit:+.2f} U\n"
        f"今日累计亏损：{loss:.2f} U\n"
        f"当前持仓：{len(snapshot['positions'])}\n"
        f"普通挂单：{len(snapshot['pending'])}\n\n"
        "每日统计按UTC零点重置。"
    )

    print(message)
    send_telegram(message)


def main():

    if MODE not in ("check", "auto_trade", "report"):
        raise RuntimeError("Invalid trader mode")

    print("MODE: DEMO ONLY")
    print("STRATEGY VERSION:", VERSION)
    print("TRADER MODE:", MODE)
    print("ORDER EXECUTION:", EXECUTION_ENABLED)
    print("CAPITAL:", CAPITAL)
    print("MAX RISK:", MAX_RISK)
    print("MAX DAILY LOSS:", MAX_DAILY_LOSS)
    print("MAX POSITIONS:", MAX_POSITIONS)
    print("MAX LEVERAGE:", MAX_LEVERAGE)

    snapshot = account_snapshot()

    print("EQUITY:", snapshot["equity"])
    print("AVAILABLE:", snapshot["available"])
    print("POSITIONS:", len(snapshot["positions"]))
    print("PENDING:", len(snapshot["pending"]))

    if MODE == "check":

        loss, net_profit = daily_loss()
        reserved = open_position_risk(snapshot)

        print("DAILY LOSS:", loss)
        print("DAILY NET PROFIT:", net_profit)
        print("OPEN RISK:", reserved)
        print("ACCOUNT CHECK PASSED")
        print("No orders were placed.")

    elif MODE == "auto_trade":

        scan_market()

    elif MODE == "report":

        report(snapshot)


if __name__ == "__main__":

    try:
        main()

    except Exception:

        send_telegram(
            "Bitget Demo Trader 运行异常。\n"
            "本轮停止交易，请检查GitHub日志。"
        )

        raise
