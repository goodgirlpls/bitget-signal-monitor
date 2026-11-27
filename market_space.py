import math

from risk import (
    MIN_RR,
    ORB_MIN_RR,
    calculate_rr,
    risk_per_unit,
    profit_per_unit
)


# =========================
# MARKET SPACE SETTINGS
# =========================

LOOKBACK = 120
PIVOT_LEFT = 3
PIVOT_RIGHT = 3

ATR_PERIOD = 14
ATR_BUFFER = 0.15


# =========================
# ATR
# =========================

def calculate_atr(candles):

    if len(candles) < ATR_PERIOD + 1:
        raise ValueError("Insufficient ATR data")

    true_ranges = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        true_ranges.append(tr)

    atr = sum(
        true_ranges[-ATR_PERIOD:]
    ) / ATR_PERIOD

    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("Invalid ATR")

    return atr


# =========================
# MARKET STRUCTURE
# =========================

def find_structure_levels(candles):

    history = candles[-LOOKBACK:]

    resistance = []
    support = []

    for i in range(
        PIVOT_LEFT,
        len(history) - PIVOT_RIGHT
    ):

        current = history[i]

        neighbors = (
            history[i - PIVOT_LEFT:i]
            + history[i + 1:i + PIVOT_RIGHT + 1]
        )

        high = current["high"]
        low = current["low"]

        if all(
            high > c["high"]
            for c in neighbors
        ):
            resistance.append(high)

        if all(
            low < c["low"]
            for c in neighbors
        ):
            support.append(low)

    return {
        "resistance": sorted(set(resistance)),
        "support": sorted(set(support))
    }


# =========================
# MARKET OPPORTUNITY
# =========================

def evaluate_market_space(signal, candles):

    entry = float(signal["entry"])
    stop = float(signal["stop"])

    direction = signal["direction"]
    strategy = signal["strategy"]

    required_rr = (
        ORB_MIN_RR
        if strategy == "D"
        else MIN_RR
    )

    if direction not in ("LONG", "SHORT"):
        raise ValueError("Invalid direction")

    if not all(
        math.isfinite(v) and v > 0
        for v in (entry, stop)
    ):
        raise ValueError("Invalid prices")

    if direction == "LONG" and stop >= entry:
        raise ValueError("Invalid LONG stop")

    if direction == "SHORT" and stop <= entry:
        raise ValueError("Invalid SHORT stop")

    if len(candles) < 30:
        return {
            "approved": False,
            "reason": "INSUFFICIENT MARKET DATA"
        }

    atr = calculate_atr(candles)
    buffer = atr * ATR_BUFFER

    levels = find_structure_levels(candles)

    if direction == "LONG":

        obstacles = [
            level
            for level in levels["resistance"]
            if level > entry
        ]

        if not obstacles:
            return {
                "approved": False,
                "reason": "NO CONFIRMED RESISTANCE",
                "atr": atr
            }

        obstacle = min(obstacles)
        target = obstacle - buffer

        if target <= entry:
            return {
                "approved": False,
                "reason": "RESISTANCE TOO CLOSE"
            }

    else:

        obstacles = [
            level
            for level in levels["support"]
            if level < entry
        ]

        if not obstacles:
            return {
                "approved": False,
                "reason": "NO CONFIRMED SUPPORT",
                "atr": atr
            }

        obstacle = max(obstacles)
        target = obstacle + buffer

        if not 0 < target < entry:
            return {
                "approved": False,
                "reason": "SUPPORT TOO CLOSE"
            }

    unit_risk = risk_per_unit(entry, stop)
    unit_profit = profit_per_unit(entry, target)

    if unit_profit <= 0:
        return {
            "approved": False,
            "reason": "NO NET PROFIT SPACE"
        }

    market_rr = calculate_rr(
        entry,
        stop,
        target,
        direction
    )

    approved = market_rr + 1e-9 >= required_rr

    return {
        "approved": approved,
        "reason": (
            "MARKET SPACE APPROVED"
            if approved
            else "INSUFFICIENT MARKET RR"
        ),
        "market_target": target,
        "market_rr": market_rr,
        "required_rr": required_rr,
        "obstacle": obstacle,
        "atr": atr,
        "atr_buffer": buffer,
        "unit_risk": unit_risk,
        "unit_profit": unit_profit
    }


# =========================
# SIGNAL FILTER
# =========================

def apply_market_filter(signal, candles):

    if signal is None:
        return None

    result = evaluate_market_space(
        signal,
        candles
    )

    print("===== MARKET SPACE ANALYSIS =====")
    print("SYMBOL:", signal["symbol"])
    print("STRATEGY:", signal["strategy"])
    print("DIRECTION:", signal["direction"])

    for key, value in result.items():
        print(key.upper(), ":", value)

    if not result["approved"]:
        print("DECISION: MARKET SPACE REJECTED")
        return None

    updated = dict(signal)

    updated["market_target"] = result["market_target"]
    updated["market_rr"] = result["market_rr"]
    updated["market_obstacle"] = result["obstacle"]
    updated["market_atr"] = result["atr"]

    print("DECISION: MARKET SPACE APPROVED")

    return updated
