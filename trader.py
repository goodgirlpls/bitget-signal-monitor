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
    MIN_RR,
    ENTRY_FEE,
    EXIT_FEE,
    SLIPPAGE,
    VERSION
)

from execution import (
    demo_get,
    get_symbol_account,
    get_history_positions,
    place_order
)


MODE = os.environ.get("TRADER_MODE", "check")

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"

EXECUTION_ENABLED = True

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
        raise RuntimeError("Invalid numeric data")

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

        print(
            "TELEGRAM SENT"
            if result.get("ok")
            else "TELEGRAM FAILED"
        )

    except Exception:
        print("Telegram notification failed")


def account_snapshot():

    accounts = demo_get(
        "/api/v2/mix/account/accounts",
        {"productType": PRODUCT_TYPE}
    )

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
        raise RuntimeError("Invalid order response")

    pending = orders.get("entrustedList") or []

    if orders.get("endId"):
        raise RuntimeError(
            "Pending order pagination requires review"
        )

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

    if result.get("endId"):
        raise RuntimeError(
            "History pagination requires review"
        )

    loss = 0.0
    net_profit = 0.0

    for position in records:

        if "netProfit" not in position:
            raise RuntimeError(
                "Net profit field missing"
            )

        pnl = number(position["netProfit"])

        net_profit += pnl

        if pnl < 0:
            loss += abs(pnl)

    return loss, net_profit


def show_account(snapshot):

    print("\n===== DEMO ACCOUNT =====")
    print("Equity:", snapshot["equity"])
    print("Available:", snapshot["available"])
    print("Positions:", len(snapshot["positions"]))
    print("Pending:", len(snapshot["pending"]))

    for position in snapshot["positions"]:

        print(
            position.get("symbol"),
            position.get("holdSide"),
            position.get("total"),
            position.get("unrealizedPL")
        )


def check_candidate(signal, snapshot):

    symbol = signal["symbol"]

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
        p.get("symbol") == symbol
        for p in snapshot["positions"]
    ):
        return False, "Existing position"

    age = (
        int(time.time() * 1000)
        - int(signal["candle_time"])
    )

    if age < 0 or age >= 900000:
        return False, "Signal expired"

    if number(signal["net_rr"]) < MIN_RR:
        return False, "Signal RR too low"

    return True, "Basic checks passed"


def build_order(signal, snapshot, risk_budget):

    symbol = signal["symbol"]
    direction = signal["direction"]

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

    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("Invalid direction")

    is_long = direction == "LONG"

    reference = number(signal["entry"])

    price_text = round_step(
        reference,
        spec["price_step"],
        upward=not is_long
    )

    stop_text = round_step(
        signal["stop"],
        spec["price_step"],
        upward=not is_long
    )

    target_text = round_step(
        signal["target"],
        spec["price_step"],
        upward=not is_long
    )

    price = number(price_text)
    stop = number(stop_text)
    target = number(target_text)

    if is_long:

        if not 0 < stop < price < target:
            raise RuntimeError("Invalid LONG prices")

    else:

        if not 0 < target < price < stop:
            raise RuntimeError("Invalid SHORT prices")

    risk_per_unit = (
        abs(price - stop)
        + price * ENTRY_FEE
        + stop * EXIT_FEE
        + (price + stop) * SLIPPAGE
    )

    profit_per_unit = (
        abs(target - price)
        - price * ENTRY_FEE
        - target * EXIT_FEE
        - (price + target) * SLIPPAGE
    )

    if risk_per_unit <= 0:
        raise RuntimeError("Invalid risk calculation")

    net_rr = profit_per_unit / risk_per_unit

    if net_rr < MIN_RR:
        raise RuntimeError(
            "Rounded order RR below minimum"
        )

    risk_size = risk_budget / risk_per_unit

    margin_size = (
        snapshot["available"] * 0.80
        / (
            price / leverage
            + price * ENTRY_FEE
        )
    )

    quantity = min(
        risk_size,
        margin_size,
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
        raise RuntimeError("Notional below 5 USDT")

    estimated_loss = size * risk_per_unit

    if estimated_loss > risk_budget + 1e-8:
        raise RuntimeError("Risk budget exceeded")

    client_oid = "demo-" + hashlib.sha256(
        str(signal["signal_id"]).encode("utf-8")
    ).hexdigest()[:24]

    return {
        "symbol": symbol,
        "direction": direction,
        "size": size_text,
        "price": price_text,
        "stop": stop_text,
        "target": target_text,
        "client_oid": client_oid,
        "notional": notional,
        "estimated_loss": estimated_loss,
        "net_profit": size * profit_per_unit,
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

    records = result.get("entrustedList")

    if records is None:
        records = []

    if not isinstance(records, list):
        raise RuntimeError("Invalid history orders")

    if result.get("endId"):
        raise RuntimeError(
            "Order history pagination requires review"
        )

    for record in records:

        if record.get("clientOid") == order["client_oid"]:
            return True

    return False


def execute_order(order, signal):

    if not EXECUTION_ENABLED:
        print("Execution disabled")
        return

    if MODE != "auto_trade":
        raise RuntimeError("Invalid execution mode")

    if check_duplicate(order):
        print("DUPLICATE SIGNAL - SKIPPED")
        return

    age = (
        int(time.time() * 1000)
        - int(signal["candle_time"])
    )

    if age < 0 or age >= 900000:
        print("SIGNAL EXPIRED - SKIPPED")
        return

    print("\n===== ORDER SUBMISSION =====")
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

        print("ORDER RESPONSE:", result)

        if not isinstance(result, dict):
            raise RuntimeError("Invalid order response")

        order_id = result.get("orderId")

        if not order_id:
            raise RuntimeError("Order ID missing")

        detail = None

        for attempt in range(3):

            time.sleep(2)

            detail = demo_get(
                "/api/v2/mix/order/detail",
                {
                    "symbol": order["symbol"],
                    "productType": PRODUCT_TYPE,
                    "orderId": order_id
                }
            )

            if detail.get("state") in (
                "filled",
                "cancelled",
                "canceled"
            ):
                break

        print("ORDER DETAIL:", detail)

        state = detail.get("state", "unknown")

        message = (
            "Bitget Demo 订单报告\n\n"
            f"策略：{signal['strategy']}\n"
            f"币种：{order['symbol']}\n"
            f"方向：{order['direction']}\n"
            f"杠杆：{order['leverage']}x\n"
            f"数量：{order['size']}\n"
            f"限价：{order['price']}\n"
            f"止损：{order['stop']}\n"
            f"止盈：{order['target']}\n"
            f"名义仓位：{order['notional']:.2f} U\n"
            f"计划风险：{order['estimated_loss']:.2f} U\n"
            f"预计净利润：{order['net_profit']:.2f} U\n"
            f"净盈亏比：1:{order['net_rr']:.2f}\n"
            f"订单状态：{state}\n"
            f"订单编号：{order_id}\n\n"
            "止盈止损已随开仓请求提交，"
            "仍需检查交易所保护订单。"
        )

        send_telegram(message)

        if state not in ("cancelled", "canceled"):
            send_telegram(
                "请检查模拟盘持仓及止盈止损。"
                "本轮不再提交其他订单。"
            )

    except Exception as error:

        print("ORDER STATUS REQUIRES REVIEW")

        send_telegram(
            "Bitget Demo 订单状态异常。\n"
            f"币种：{order['symbol']}\n"
            f"Client ID：{order['client_oid']}\n"
            "请检查交易所订单及持仓。"
            "程序不会自动重试。"
        )

        raise RuntimeError(
            "Execution halted for manual review"
        ) from error


def scan_market():

    print("\n===== MARKET SCAN =====")

    snapshot = account_snapshot()

    loss, net_profit = daily_loss()

    print("Daily realized losses:", loss)
    print("Daily net profit:", net_profit)

    if loss >= MAX_DAILY_LOSS:

        print("DAILY LOSS LIMIT REACHED")

        send_telegram(
            "Bitget Demo 每日亏损熔断："
            f"{loss:.2f} U。\n"
            "停止新增仓位。"
        )

        return

    if snapshot["positions"]:

        print(
            "EXISTING POSITIONS - "
            "PROTECTION REVIEW REQUIRED"
        )

        return

    if snapshot["pending"]:

        print("PENDING ORDERS - NO NEW ENTRY")

        return

    candidates = []

    for symbol in SYMBOLS:

        candles = get_candles(symbol)

        signal = analyze_signal(symbol, candles)

        if signal is None:
            continue

        approved, reason = check_candidate(
            signal,
            snapshot
        )

        print(symbol, approved, reason)

        if approved:
            candidates.append(signal)

    priority = {"A": 0, "B": 1, "C": 2}

    candidates.sort(
        key=lambda s: priority.get(s["strategy"], 99)
    )

    if not candidates:

        print("NO ELIGIBLE CANDIDATES")
        print("No orders were placed.")

        return

    for signal in candidates:

        snapshot = account_snapshot()

        if snapshot["positions"] or snapshot["pending"]:
            print("Account changed - entry halted")
            return

        loss, net_profit = daily_loss()

        remaining = MAX_DAILY_LOSS - loss

        risk_budget = min(
            MAX_RISK,
            remaining
        )

        if risk_budget <= 0:
            print("No remaining daily risk budget")
            return

        approved, reason = check_candidate(
            signal,
            snapshot
        )

        if not approved:
            print("SKIPPED:", reason)
            continue

        try:

            order = build_order(
                signal,
                snapshot,
                risk_budget
            )

        except RuntimeError as error:

            print(
                signal["symbol"],
                "ORDER SKIPPED:",
                str(error)
            )

            continue

        execute_order(order, signal)

        return

    print("NO EXECUTABLE ORDERS")


def report(snapshot):

    loss, net_profit = daily_loss()

    equity = snapshot["equity"]

    difference = equity - CAPITAL

    percentage = difference / CAPITAL * 100

    message = (
        "Bitget Demo 账户报告\n\n"
        f"初始参考本金：{CAPITAL:.2f} U\n"
        f"当前权益：{equity:.2f} U\n"
        f"可用余额：{snapshot['available']:.2f} U\n"
        f"权益变化：{difference:+.2f} U\n"
        f"权益变化率：{percentage:+.2f}%\n"
        f"今日已平仓净收益：{net_profit:+.2f} U\n"
        f"今日累计亏损：{loss:.2f} U\n"
        f"当前持仓：{len(snapshot['positions'])}\n"
        f"普通挂单：{len(snapshot['pending'])}\n\n"
        "每日统计按 UTC 零点重置。\n"
        "权益变化包含浮动盈亏及资金调整。"
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

    print("Capital:", CAPITAL)
    print("Maximum planned risk:", MAX_RISK)
    print("Daily planned loss limit:", MAX_DAILY_LOSS)
    print("Leverage ceiling:", MAX_LEVERAGE)
    print("Maximum positions:", MAX_POSITIONS)
    print("Minimum net RR:", MIN_RR)

    snapshot = account_snapshot()

    show_account(snapshot)

    if MODE == "check":

        loss, net_profit = daily_loss()

        print("Daily realized losses:", loss)
        print("Daily net profit:", net_profit)
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
            "Bitget Demo Trader 运行异常，"
            "本轮程序已停止。请检查 GitHub 日志。"
        )

        raise
