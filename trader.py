
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
    place_order,
    get_live_positions,
    place_partial_take_profit,
    modify_stop_loss,
    get_history_protection_orders
)

from position_manager import (
    TP1_R,
    TP1_RATIO,
    TRAIL_START_R,
    calculate_breakeven,
    calculate_position_targets,
    calculate_trailing_stop
)
from protection import (
    verify_protection,
    get_protection_orders
)
from contracts import fetch_contracts

from market_space import (
    calculate_atr,
    find_structure_levels
)

from state_store import load_state, save_state

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

CONTRACTS = {}

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

    protection_cache = {}

    for symbol in SYMBOLS:

        plans = get_protection_orders(symbol)

        if len(plans) >= 100:
            raise RuntimeError("Plan pagination required")

        if plans and symbol not in active_symbols:
            raise RuntimeError(
                f"Residual protection orders: {symbol}"
            )

        protection_cache[symbol] = plans

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

        plans = protection_cache[symbol]

        stops = []
        targets = []
        stop_size = 0.0
        target_size = 0.0
        seen_ids = set()

        for plan in plans:

            if plan.get("symbol") != symbol:
                continue

            if plan.get("holdSide") != side:
                continue

            if plan.get("planStatus") != "live":
                continue

            if plan.get("tradeSide") != "close":
                continue

            order_id = plan.get("orderId")

            if not order_id:
                raise RuntimeError("Missing protection ID")

            if order_id in seen_ids:
                continue

            seen_ids.add(order_id)

            plan_type = plan.get("planType")

            trigger = number(
                plan.get("triggerPrice") or 0
            )

            raw_size = plan.get("size")
            raw_base_volume = plan.get("baseVolume")

            if raw_size in (None, ""):
                raw_size = raw_base_volume

            elif raw_base_volume not in (None, ""):

                if not math.isclose(
                    number(raw_size),
                    number(raw_base_volume),
                    rel_tol=0,
                    abs_tol=1e-8
                ):
                    raise RuntimeError(
                        "Conflicting protection order sizes"
                    )

            covered_size = number(raw_size or 0)

            if plan_type in ("pos_loss", "pos_profit"):
                covered_size = size

            if covered_size <= 0 or trigger <= 0:
                continue

            if plan_type in ("loss_plan", "pos_loss"):
                stops.append(trigger)
                stop_size += covered_size

            elif plan_type in ("profit_plan", "pos_profit"):
                targets.append(trigger)
                target_size += covered_size

        if not stops or not targets:
            raise RuntimeError(
                f"Unverified protection: {symbol}"
            )

        if not math.isclose(
            stop_size,
            size,
            rel_tol=0,
            abs_tol=1e-8
        ):
            raise RuntimeError(
                f"Invalid stop coverage: {symbol}"
            )

        if not math.isclose(
            target_size,
            size,
            rel_tol=0,
            abs_tol=1e-8
        ):
            raise RuntimeError(
                f"Invalid target coverage: {symbol}"
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

        reserved = max(risk, unrealized_loss)

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

    exchange_min_leverage = number(
        spec["min_leverage"]
    )

    exchange_max_leverage = number(
        spec["max_leverage"]
    )

    allowed_max_leverage = min(
        MAX_LEVERAGE,
        exchange_max_leverage
    )

    if not exchange_min_leverage <= leverage <= allowed_max_leverage:
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
        if "market_target" in signal:

            market_target = number(
                signal["market_target"]
            )

            if (
                is_long
                and target > market_target
            ):
                raise RuntimeError(
                    "Target exceeds market profit space"
                )

            if (
                not is_long
                and target < market_target
            ):
                raise RuntimeError(
                    "Target exceeds market profit space"
                )

            market_rr = calculate_rr(
                price,
                stop,
                market_target,
                direction
            )

            if market_rr + 1e-9 < required_rr:
                raise RuntimeError(
                    "Insufficient market RR after rounding"
                )

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

    max_order_size = spec.get("max_order_size")

    if max_order_size is not None:
        quantity = min(
            quantity,
            number(max_order_size)
        )
    else:
        print(
            "WARNING: Exchange maximum order size unavailable",
            symbol
        )

    size_text = round_step(
        quantity,
        spec["size_step"]
    )

    size = number(size_text)

    if size < number(spec["min_size"]):
        raise RuntimeError("Quantity below minimum")
    tp1_size_text = round_step(
        size * TP1_RATIO,
        spec["size_step"]
    )

    tp1_size = number(tp1_size_text)
    remaining_size = size - tp1_size

    minimum_size = number(spec["min_size"])

    if tp1_size < minimum_size:
        raise RuntimeError(
            "Partial TP quantity below minimum"
        )

    if remaining_size < minimum_size - 1e-8:
        raise RuntimeError(
            "Remaining quantity below minimum"
        )

    if not 0 < tp1_size < size:
        raise RuntimeError(
            "Invalid partial TP quantity"
        )
    notional = size * price

    min_notional = max(
        5.0,
        number(spec["min_notional"])
    )

    if notional < min_notional:
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
        "target_size": tp1_size_text,
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

        order_id = None

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

        except Exception as error:

            print("ORDER SUBMISSION UNCERTAIN:", str(error))

            for attempt in range(5):

                time.sleep(2)

                try:

                    recovered = demo_get(
                        "/api/v2/mix/order/detail",
                        {
                            "symbol": order["symbol"],
                            "productType": PRODUCT_TYPE,
                            "clientOid": order["client_oid"]
                        }
                    )

                    if (
                        isinstance(recovered, dict)
                        and recovered.get("orderId")
                        and recovered.get("clientOid")
                        == order["client_oid"]
                        and recovered.get("symbol")
                        == order["symbol"]
                    ):

                        order_id = recovered["orderId"]

                        print(
                            "ORDER RECOVERED:",
                            order_id
                        )

                        break

                except Exception:

                    print(
                        "ORDER RECOVERY ATTEMPT:",
                        attempt + 1
                    )

            if not order_id:
                raise RuntimeError(
                    "Order status unresolved. "
                    "Manual review required. "
                    "No automatic retry."
                ) from error

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

        expected_size = number(order["size"])
        limit_price = number(order["price"])
        stop_price = number(order["stop"])
        target_price = number(order["target"])

        if actual_size <= 0 or actual_price <= 0:
            raise RuntimeError("Invalid fill data")

        if not math.isclose(
            actual_size,
            expected_size,
            rel_tol=0,
            abs_tol=1e-8
        ):
            raise RuntimeError("Unexpected fill size")
        initial_protection_order = dict(order)
        initial_protection_order["target_size"] = 0

        protection = verify_protection(
            initial_protection_order
        )

        if not protection.get("verified"):
            raise RuntimeError(
                "Initial stop protection not verified"
            )

        if order["direction"] == "LONG":

            if actual_price > limit_price + 1e-8:
                raise RuntimeError("Fill exceeds LONG limit")

            valid_prices = (
                stop_price < actual_price < target_price
            )

        elif order["direction"] == "SHORT":

            if actual_price < limit_price - 1e-8:
                raise RuntimeError("Fill below SHORT limit")

            valid_prices = (
                target_price < actual_price < stop_price
            )

        else:
            raise RuntimeError("Invalid direction")

        if not valid_prices:
            raise RuntimeError("Unexpected fill price")

        actual_notional = actual_size * actual_price

        actual_risk = actual_size * risk_per_unit(
            actual_price,
            stop_price
        )

        actual_net_profit = actual_size * profit_per_unit(
            actual_price,
            target_price
        )

        actual_net_rr = calculate_rr(
            actual_price,
            stop_price,
            target_price,
            order["direction"]
        )

        allowed_risk = min(
            MAX_RISK,
            number(order["risk_budget"])
        )

        if actual_risk > allowed_risk + 1e-8:
            raise RuntimeError(
                f"Filled risk exceeds budget: "
                f"{actual_risk:.4f} > {allowed_risk:.4f}"
            )

        required_rr = STRATEGY_MIN_RR[
            order["strategy"]
        ]

        if actual_net_rr + 1e-9 < required_rr:
            raise RuntimeError(
                f"Filled RR below minimum: "
                f"{actual_net_rr:.4f} < {required_rr:.4f}"
            )
        spec = CONTRACTS[order["symbol"]]

        position_targets = calculate_position_targets(
            actual_price,
            stop_price,
            order["direction"]
        )

        tp1_price_text = round_step(
            position_targets["tp1"],
            spec["price_step"],
            upward=order["direction"] == "LONG"
        )

        tp1_size_text = order["target_size"]

        tp1_client_oid = (
            order["client_oid"] + "-tp1"
        )

        state, state_sha = load_state()

        position_key = (
            order["symbol"] + ":" + order["direction"]
        )

        if position_key in state["positions"]:
            raise RuntimeError(
                "Existing position state requires review"
            )

        state["positions"][position_key] = {
            "symbol": order["symbol"],
            "direction": order["direction"],
            "strategy": order["strategy"],
            "signal_id": order["signal_id"],
            "entry_order_id": str(order_id),
            "entry_client_oid": order["client_oid"],
            "entry_price": actual_price,
            "initial_size": actual_size,
            "remaining_size": actual_size,
            "initial_stop": stop_price,
            "current_stop": stop_price,
            "initial_risk": actual_risk,
            "tp1_price": number(tp1_price_text),
            "tp1_size": number(tp1_size_text),
            "tp1_client_oid": tp1_client_oid,
            "tp1_order_id": None,
            "tp1_completed": False,
            "breakeven_price": position_targets["breakeven"],
            "breakeven_activated": False,
            "trail_start_price": position_targets["trail_start"],
            "trailing_activated": False,
            "status": "TP1_PENDING",
            "opened_at": datetime.now(timezone.utc).isoformat()
        }

        state_sha = save_state(state, state_sha)

        print("INITIAL POSITION STATE SAVED")

        print("TP1 PRICE:", tp1_price_text)
        print("TP1 SIZE:", tp1_size_text)
        print("BREAKEVEN:", position_targets["breakeven"])
        print("TRAIL START:", position_targets["trail_start"])

        partial_tp = place_partial_take_profit(
            order["symbol"],
            order["direction"],
            tp1_size_text,
            tp1_price_text,
            tp1_client_oid
        )

        if not isinstance(partial_tp, dict):
            raise RuntimeError(
                "Invalid partial TP response"
            )

        if not partial_tp.get("orderId"):
            raise RuntimeError(
                "Partial TP order ID missing"
            )

        state["positions"][position_key]["tp1_order_id"] = str(
            partial_tp["orderId"]
        )

        state_sha = save_state(state, state_sha)

        print("TP1 ORDER ID SAVED")

        updated_order = dict(order)
        updated_order["target"] = tp1_price_text
        updated_order["target_size"] = tp1_size_text

        protection = verify_protection(updated_order)

        if not protection.get("verified"):
            raise RuntimeError(
                "Partial TP protection not verified"
            )

        state["positions"][position_key]["stop_order_ids"] = [
            str(order_id)
            for order_id in protection["stop_ids"]
        ]

        state["positions"][position_key]["target_order_ids"] = [
            str(order_id)
            for order_id in protection["target_ids"]
        ]

        state["positions"][position_key]["status"] = "ACTIVE"

        state_sha = save_state(state, state_sha)

        print("PROTECTION ORDER IDS SAVED")
        print("PARTIAL TP VERIFIED")
        print("ACTUAL FILL SIZE:", actual_size)
        print("ACTUAL FILL PRICE:", actual_price)
        print("ACTUAL ESTIMATED RISK:", actual_risk)
        print("ACTUAL ESTIMATED PROFIT:", actual_net_profit)
        print("ACTUAL NET RR:", actual_net_rr)

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

def confirm_tp1_fill(record, live_position):

    symbol = record["symbol"]
    direction = record["direction"]

    tp1_order_id = record.get("tp1_order_id")

    if not tp1_order_id:
        raise RuntimeError("Missing TP1 order ID")

    opened_at = datetime.fromisoformat(
        record["opened_at"]
    )

    start_time = int(
        opened_at.timestamp() * 1000
    ) - 60000

    history = get_history_protection_orders(
        symbol,
        start_time
    )

    matches = [
        p for p in history
        if str(p.get("orderId")) == str(tp1_order_id)
    ]

    if not matches:
        return False

    if len(matches) != 1:
        raise RuntimeError("Ambiguous TP1 history")

    plan = matches[0]

    expected_side = (
        "long" if direction == "LONG"
        else "short"
    )

    if (
        str(plan.get("symbol", "")).upper() != symbol
        or plan.get("planType") != "profit_plan"
        or plan.get("posSide") != expected_side
        or plan.get("tradeSide") != "close"
    ):
        raise RuntimeError("TP1 identity mismatch")

    status = plan.get("planStatus")

    if status in ("cancelled", "fail_execute"):
        raise RuntimeError(
            "TP1 cancelled or execution failed"
        )

    if status != "executed":
        return False

    execution_id = plan.get("executeOrderId")

    if not execution_id:
        return False

    detail = demo_get(
        "/api/v2/mix/order/detail",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "orderId": str(execution_id)
        }
    )

    if not isinstance(detail, dict):
        raise RuntimeError("Invalid TP1 execution detail")

    if (
        str(detail.get("orderId")) != str(execution_id)
        or str(detail.get("symbol", "")).upper() != symbol
        or detail.get("posSide") != expected_side
        or detail.get("tradeSide") != "close"
    ):
        raise RuntimeError("TP1 execution identity mismatch")

    if detail.get("state") != "filled":
        return False

    filled_size = number(detail["baseVolume"])
    filled_price = number(detail["priceAvg"])

    expected_size = number(record["tp1_size"])

    if filled_price <= 0 or filled_size <= 0:
        raise RuntimeError("Invalid TP1 fill")

    if not math.isclose(
        filled_size,
        expected_size,
        rel_tol=0,
        abs_tol=1e-8
    ):
        raise RuntimeError("Unexpected TP1 fill size")

    expected_remaining = (
        number(record["initial_size"])
        - filled_size
    )

    if (
        live_position.get("symbol") != symbol
        or live_position.get("holdSide") != expected_side
    ):
        raise RuntimeError("Remaining position mismatch")

    actual_remaining = number(
        live_position["total"]
    )

    if not math.isclose(
        actual_remaining,
        expected_remaining,
        rel_tol=0,
        abs_tol=1e-8
    ):
        raise RuntimeError(
            "Unexpected remaining position size"
        )

    print("TP1 FILL CONFIRMED")
    print("TP1 FILLED SIZE:", filled_size)
    print("TP1 FILLED PRICE:", filled_price)
    print("REMAINING SIZE:", actual_remaining)

    return True
def activate_breakeven(record, live_position):

    if MODE != "auto_trade" or not EXECUTION_ENABLED:
        raise RuntimeError("Stop modification disabled")

    if record.get("breakeven_activated"):
        return False

    if not confirm_tp1_fill(record, live_position):
        return False

    symbol = record["symbol"]
    direction = record["direction"]

    side = (
        "long" if direction == "LONG"
        else "short"
    )

    remaining_size = number(
        live_position["total"]
    )

    if remaining_size <= 0:
        raise RuntimeError("No remaining position")

    spec = CONTRACTS[symbol]

    new_stop_text = round_step(
        record["breakeven_price"],
        spec["price_step"],
        upward=direction == "LONG"
    )

    new_stop = number(new_stop_text)

    old_stop = number(record["current_stop"])

    market_price = number(
        live_position["markPrice"]
    )

    if direction == "LONG":
        if not old_stop <= new_stop < market_price:
            raise RuntimeError(
                "Invalid LONG breakeven stop"
            )

    elif direction == "SHORT":
        if not market_price < new_stop <= old_stop:
            raise RuntimeError(
                "Invalid SHORT breakeven stop"
            )

    else:
        raise RuntimeError("Invalid direction")

    plans = get_protection_orders(symbol)

    stop_ids = record.get("stop_order_ids", [])

    stops = [
        p for p in plans
        if p.get("symbol") == symbol
        and p.get("holdSide") == side
        and p.get("planStatus") == "live"
        and p.get("tradeSide") == "close"
        and p.get("planType") in (
            "loss_plan", "pos_loss"
        )
    ]

    if len(stops) != 1:
        raise RuntimeError(
            "Unique stop order not confirmed"
        )

    stop = stops[0]

    stop_id = str(stop.get("orderId", ""))

    if not stop_id or stop_id not in stop_ids:
        raise RuntimeError("Stop order ID mismatch")

    if stop.get("triggerType") != "fill_price":
        raise RuntimeError("Unexpected stop trigger type")

    if not math.isclose(
        number(stop["triggerPrice"]),
        old_stop,
        rel_tol=0,
        abs_tol=1e-8
    ):
        raise RuntimeError("Current stop price mismatch")

    # Only modify a verified position-wide stop.
    # Fixed-size stop behavior requires Demo validation.
    if stop.get("planType") != "pos_loss":
        raise RuntimeError(
            "Position-wide stop required"
        )

    result = modify_stop_loss(
        symbol,
        direction,
        stop_id,
        old_stop,
        new_stop_text,
        remaining_size,
        "pos_loss"
    )

    if not isinstance(result, dict):
        raise RuntimeError(
            "Invalid breakeven modification response"
        )

    expected = {
        "symbol": symbol,
        "direction": direction,
        "size": str(remaining_size),
        "stop": new_stop_text,
        "target_size": 0
    }

    protection = verify_protection(expected)

    if not protection.get("verified"):
        raise RuntimeError(
            "Breakeven protection not verified"
        )

    record["remaining_size"] = remaining_size
    record["current_stop"] = new_stop
    record["tp1_completed"] = True
    record["breakeven_activated"] = True

    record["stop_order_ids"] = [
        str(order_id)
        for order_id in protection["stop_ids"]
    ]

    record["target_order_ids"] = []
    record["status"] = "BREAKEVEN"

    print("BREAKEVEN ACTIVATED")
    print("OLD STOP:", old_stop)
    print("NEW STOP:", new_stop)
    print("REMAINING SIZE:", remaining_size)

    return True
def update_trailing_stop(record, live_position):

    if MODE != "auto_trade" or not EXECUTION_ENABLED:
        raise RuntimeError("Stop modification disabled")

    if not record.get("tp1_completed"):
        return False

    if not record.get("breakeven_activated"):
        return False

    symbol = record["symbol"]
    direction = record["direction"]

    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("Invalid direction")

    is_long = direction == "LONG"
    side = "long" if is_long else "short"

    if (
        live_position.get("symbol") != symbol
        or live_position.get("holdSide") != side
    ):
        raise RuntimeError("Position identity mismatch")

    market_price = number(
        live_position["markPrice"]
    )

    remaining_size = number(
        live_position["total"]
    )

    if remaining_size <= 0 or market_price <= 0:
        raise RuntimeError("Invalid position data")

    if not math.isclose(
        remaining_size,
        number(record["remaining_size"]),
        rel_tol=0,
        abs_tol=1e-8
    ):
        raise RuntimeError("Position size mismatch")

    trail_start = number(
        record["trail_start_price"]
    )

    if not record.get("trailing_activated"):

        reached = (
            market_price >= trail_start
            if is_long
            else market_price <= trail_start
        )

        if not reached:
            return False

        record["trailing_activated"] = True
        record["status"] = "TRAILING"

        print("TRAILING ACTIVATED:", symbol)

        return True

    candles = get_candles(symbol)

    now = int(time.time() * 1000)

    candles = [
        c for c in candles
        if c["time"] + 900000 <= now
    ]

    if len(candles) < 30:
        return False

    atr = calculate_atr(candles)

    levels = find_structure_levels(candles)

    if is_long:

        valid_levels = [
            level for level in levels["support"]
            if level < market_price
        ]

        if not valid_levels:
            return False

        structure_level = max(valid_levels)

    else:

        valid_levels = [
            level for level in levels["resistance"]
            if level > market_price
        ]

        if not valid_levels:
            return False

        structure_level = min(valid_levels)

    old_stop = number(record["current_stop"])

    candidate = calculate_trailing_stop(
        direction,
        old_stop,
        structure_level,
        atr,
        market_price
    )

    spec = CONTRACTS[symbol]

    new_stop_text = round_step(
        candidate,
        spec["price_step"],
        upward=not is_long
    )

    new_stop = number(new_stop_text)

    if is_long:
        if not old_stop < new_stop < market_price:
            return False

    else:
        if not market_price < new_stop < old_stop:
            return False

    plans = get_protection_orders(symbol)

    stops = [
        p for p in plans
        if p.get("symbol") == symbol
        and p.get("holdSide") == side
        and p.get("planStatus") == "live"
        and p.get("tradeSide") == "close"
        and p.get("planType") in (
            "loss_plan", "pos_loss"
        )
    ]

    if len(stops) != 1:
        raise RuntimeError(
            "Unique trailing stop not confirmed"
        )

    stop = stops[0]

    stop_id = str(stop.get("orderId", ""))

    if stop_id not in record.get("stop_order_ids", []):
        raise RuntimeError("Stop ID mismatch")

    if stop.get("planType") != "pos_loss":
        raise RuntimeError(
            "Position-wide stop required"
        )

    if stop.get("triggerType") != "fill_price":
        raise RuntimeError("Unexpected trigger type")

    if not math.isclose(
        number(stop["triggerPrice"]),
        old_stop,
        rel_tol=0,
        abs_tol=1e-8
    ):
        raise RuntimeError("Current stop mismatch")

    result = modify_stop_loss(
        symbol,
        direction,
        stop_id,
        old_stop,
        new_stop_text,
        remaining_size,
        "pos_loss"
    )

    if not isinstance(result, dict):
        raise RuntimeError(
            "Invalid trailing modification response"
        )

    expected = {
        "symbol": symbol,
        "direction": direction,
        "size": str(remaining_size),
        "stop": new_stop_text,
        "target_size": 0
    }

    protection = verify_protection(expected)

    if not protection.get("verified"):
        raise RuntimeError(
            "Trailing protection not verified"
        )

    record["current_stop"] = new_stop

    record["stop_order_ids"] = [
        str(order_id)
        for order_id in protection["stop_ids"]
    ]

    record["status"] = "TRAILING"

    print("TRAILING STOP UPDATED")
    print("SYMBOL:", symbol)
    print("STRUCTURE LEVEL:", structure_level)
    print("ATR:", atr)
    print("OLD STOP:", old_stop)
    print("NEW STOP:", new_stop)

    return True
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


    global CONTRACTS

    if MODE not in ("check", "auto_trade", "report"):
        raise RuntimeError("Invalid trader mode")

    CONTRACTS = fetch_contracts()

    print("DYNAMIC CONTRACTS LOADED")

    for symbol, spec in CONTRACTS.items():
        print("CONTRACT:", symbol)
        print(json.dumps(spec, indent=2))

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
