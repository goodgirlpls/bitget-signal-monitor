import os
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# =========================
# STRATEGY SETTINGS
# =========================

VERSION = "2.0"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

BASE_URL = "https://api.bitget.com/api/v2/mix/market/candles"

CAPITAL = 145.0
MAX_RISK = 5.0
MAX_DAILY_LOSS = 10.0
MAX_POSITIONS = 3
MAX_LEVERAGE = 100
MIN_RR = 1.8

ENTRY_FEE = 0.0006
EXIT_FEE = 0.0006
SLIPPAGE = 0.0005

VOLUME_RATIO = 1.2

COMPRESSION_THRESHOLD = 0.003
COMPRESSION_BARS = 5
COMPRESSION_LOOKBACK = 20

CANDLE_MS = 900000


# =========================
# MARKET DATA
# =========================

def ema(values, period):
    if len(values) < period:
        raise RuntimeError("Not enough EMA data")

    result = []
    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period

    for i, value in enumerate(values):
        if i < period - 1:
            result.append(None)
        elif i == period - 1:
            result.append(current)
        else:
            current = (
                value * multiplier
                + current * (1 - multiplier)
            )
            result.append(current)

    return result


def get_candles(symbol):
    params = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "granularity": "15m",
        "limit": "200"
    }

    url = BASE_URL + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BitgetSignalMonitor/2.0"}
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        result = json.load(response)

    if result.get("code") != "00000":
        raise RuntimeError("Bitget API error")

    raw = result.get("data") or []

    if len(raw) < 200:
        raise RuntimeError("Not enough candles")

    candles = []

    for row in raw:
        candle = {
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5])
        }

        if not all(
            math.isfinite(v)
            for k, v in candle.items()
            if k != "time"
        ):
            raise RuntimeError("Invalid market data")

        if not (
            0 < candle["low"]
            <= candle["open"]
            <= candle["high"]
            and candle["low"]
            <= candle["close"]
            <= candle["high"]
            and candle["volume"] >= 0
        ):
            raise RuntimeError("Invalid OHLCV")

        candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    times = [c["time"] for c in candles]

    if len(set(times)) != len(times):
        raise RuntimeError("Duplicate candles")

    if any(
        b - a != CANDLE_MS
        for a, b in zip(times, times[1:])
    ):
        raise RuntimeError("Missing candles")

    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    if times[-1] > now_ms + 60000:
        raise RuntimeError("Future candle timestamp")

    completed = [
        c for c in candles
        if c["time"] + CANDLE_MS <= now_ms
    ]

    if len(completed) < 150:
        raise RuntimeError("Not enough closed candles")

    if now_ms - (
        completed[-1]["time"] + CANDLE_MS
    ) > CANDLE_MS:
        raise RuntimeError("Stale market data")

    return completed


# =========================
# TELEGRAM
# =========================

def send_telegram(symbol, message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Telegram secrets missing")

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "🔄 重新检测行情",
                    "callback_data": f"recheck:{symbol}"
                }
            ]
        ]
    }

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "reply_markup": json.dumps(keyboard)
    }).encode("utf-8")

    url = (
        f"https://api.telegram.org/bot"
        f"{token}/sendMessage"
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
    except Exception:
        raise RuntimeError(
            "Telegram notification failed"
        ) from None

    if not result.get("ok"):
        raise RuntimeError("Telegram notification failed")

    print("TELEGRAM MESSAGE SENT")


# =========================
# STRATEGY A
# EMA COMPRESSION BREAKOUT
# =========================

def detect_compression(candles, e20, e60, e120):
    spreads = []

    for i, candle in enumerate(candles):
        values = [e20[i], e60[i], e120[i]]

        if any(v is None for v in values):
            spreads.append(None)
            continue

        spread = (
            max(values) - min(values)
        ) / candle["close"]

        spreads.append(spread)

    start = max(
        0,
        len(candles) - 1 - COMPRESSION_LOOKBACK
    )

    end = len(candles) - 1

    runs = []
    current_run = []

    for i in range(start, end):
        if (
            spreads[i] is not None
            and spreads[i] <= COMPRESSION_THRESHOLD
        ):
            current_run.append(i)
        else:
            if len(current_run) >= COMPRESSION_BARS:
                runs.append(current_run)
            current_run = []

    if len(current_run) >= COMPRESSION_BARS:
        runs.append(current_run)

    if not runs:
        return None, spreads[-1]

    run = runs[-1]

    zone_high = max(
        candles[i]["high"] for i in run
    )

    zone_low = min(
        candles[i]["low"] for i in run
    )

    return {
        "high": zone_high,
        "low": zone_low,
        "bars": len(run),
        "end_index": run[-1],
        "spread": spreads[run[-1]]
    }, spreads[-1]


# =========================
# SHARED TRADING STRATEGY
# =========================

def analyze_signal(symbol, candles):
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    e20 = ema(closes, 20)
    e60 = ema(closes, 60)
    e120 = ema(closes, 120)

    last = candles[-1]
    previous = candles[-2]
    price = last["close"]

    volume_average = sum(volumes[-21:-1]) / 20

    volume_ratio = (
        last["volume"] / volume_average
        if volume_average > 0 else 0
    )

    uptrend = e20[-1] > e60[-1] > e120[-1]
    downtrend = e20[-1] < e60[-1] < e120[-1]

    direction = None
    strategy = None
    reason = None

    compression, spread = detect_compression(
        candles, e20, e60, e120
    )

    print(f"\nSYMBOL: {symbol}")
    print("VERSION:", VERSION)
    print("Close:", price)
    print("EMA20:", e20[-1])
    print("EMA60:", e60[-1])
    print("EMA120:", e120[-1])
    print("Volume ratio:", round(volume_ratio, 3))

    if spread is not None:
        print(
            "EMA spread:",
            round(spread * 100, 4),
            "%"
        )

    print("Compression zone:", compression)

    # Reject insufficient volume

    if volume_ratio < VOLUME_RATIO:
        print("DECISION: LOW VOLUME")
        return None

    # -------------------------
    # A: Compression breakout
    # -------------------------

    if compression is not None:
        zone_high = compression["high"]
        zone_low = compression["low"]

        highest_ema = max(
            e20[-1], e60[-1], e120[-1]
        )

        lowest_ema = min(
            e20[-1], e60[-1], e120[-1]
        )

        prior_highest_ema = max(
            e20[-2], e60[-2], e120[-2]
        )

        prior_lowest_ema = min(
            e20[-2], e60[-2], e120[-2]
        )

        long_boundary = max(
            zone_high, highest_ema
        )

        short_boundary = min(
            zone_low, lowest_ema
        )

        prior_long_boundary = max(
            zone_high, prior_highest_ema
        )

        prior_short_boundary = min(
            zone_low, prior_lowest_ema
        )

        no_previous_long_break = all(
            c["close"] <= zone_high
            for c in candles[
                compression["end_index"] + 1:-1
            ]
        )

        no_previous_short_break = all(
            c["close"] >= zone_low
            for c in candles[
                compression["end_index"] + 1:-1
            ]
        )

        expanding = (
            spread is not None
            and spread > compression["spread"]
        )

        if (
            price > long_boundary
            and previous["close"] <= prior_long_boundary
            and no_previous_long_break
            and e20[-1] > e20[-2]
            and expanding
            and last["close"] > last["open"]
        ):
            direction = "LONG"
            strategy = "A"
            reason = "EMA均线密集放量向上突破"

        elif (
            price < short_boundary
            and previous["close"] >= prior_short_boundary
            and no_previous_short_break
            and e20[-1] < e20[-2]
            and expanding
            and last["close"] < last["open"]
        ):
            direction = "SHORT"
            strategy = "A"
            reason = "EMA均线密集放量向下突破"

    # -------------------------
    # B: First EMA20 retest
    # -------------------------

    def long_retest(i):
        return (
            e20[i] is not None
            and e60[i] is not None
            and e120[i] is not None
            and e20[i] > e60[i] > e120[i]
            and candles[i]["low"] <= e20[i]
            and candles[i]["close"] >= e20[i]
        )

    def short_retest(i):
        return (
            e20[i] is not None
            and e60[i] is not None
            and e120[i] is not None
            and e20[i] < e60[i] < e120[i]
            and candles[i]["high"] >= e20[i]
            and candles[i]["close"] <= e20[i]
        )

    recent_long = [
        i for i in range(
            len(candles) - 10, len(candles)
        )
        if long_retest(i)
    ]

    recent_short = [
        i for i in range(
            len(candles) - 10, len(candles)
        )
        if short_retest(i)
    ]

    first_long = (
        len(recent_long) == 1
        and recent_long[0] == len(candles) - 1
    )

    first_short = (
        len(recent_short) == 1
        and recent_short[0] == len(candles) - 1
    )

    if direction is None:
        if (
            uptrend
            and first_long
            and price > previous["close"]
        ):
            direction = "LONG"
            strategy = "B"
            reason = "第一次回踩EMA20"

        elif (
            downtrend
            and first_short
            and price < previous["close"]
        ):
            direction = "SHORT"
            strategy = "B"
            reason = "第一次反抽EMA20"

    # -------------------------
    # C: BOS breakout
    # -------------------------

    prior_high = max(
        c["high"] for c in candles[-21:-1]
    )

    prior_low = min(
        c["low"] for c in candles[-21:-1]
    )

    if direction is None:
        if (
            uptrend
            and price > prior_high
            and previous["close"] <= prior_high
        ):
            direction = "LONG"
            strategy = "C"
            reason = "20根K线向上突破"

        elif (
            downtrend
            and price < prior_low
            and previous["close"] >= prior_low
        ):
            direction = "SHORT"
            strategy = "C"
            reason = "20根K线向下突破"

    if direction is None:
        print("DECISION: NO SIGNAL")
        return None

    # =========================
    # RISK CALCULATION
    # =========================

    if direction == "LONG":
        stop = min(
            c["low"] for c in candles[-5:]
        )

        if stop >= price:
            return None

        stop_distance = price - stop

    else:
        stop = max(
            c["high"] for c in candles[-5:]
        )

        if stop <= price:
            return None

        stop_distance = stop - price

    cost_rate = (
        ENTRY_FEE + EXIT_FEE + 2 * SLIPPAGE
    )

    risk_per_unit = (
        stop_distance + price * cost_rate
    )

    if risk_per_unit <= 0:
        return None

    quantity = min(
        MAX_RISK / risk_per_unit,
        CAPITAL * MAX_LEVERAGE / price
    )

    notional = quantity * price
    estimated_loss = quantity * risk_per_unit
    estimated_cost = quantity * price * cost_rate

    target_distance = (
        MIN_RR * risk_per_unit
        + price * cost_rate
    )

    if direction == "LONG":
        target = price + target_distance
        gross_profit = (
            target - price
        ) * quantity
    else:
        target = price - target_distance

        if target <= 0:
            return None

        gross_profit = (
            price - target
        ) * quantity

    net_profit = gross_profit - estimated_cost

    if estimated_loss <= 0:
        return None

    net_rr = net_profit / estimated_loss

    if notional < 5 or net_rr < MIN_RR - 1e-9:
        print("DECISION: REJECTED")
        return None

    signal = {
        "version": VERSION,
        "symbol": symbol,
        "strategy": strategy,
        "reason": reason,
        "direction": direction,
        "entry": price,
        "stop": stop,
        "target": target,
        "quantity": quantity,
        "notional": notional,
        "estimated_loss": estimated_loss,
        "estimated_cost": estimated_cost,
        "net_profit": net_profit,
        "net_rr": net_rr,
        "volume_ratio": volume_ratio,
        "ema_spread": spread,
        "compression": compression,
        "candle_time": last["time"] + CANDLE_MS,
        "signal_id": (
            f"{symbol}-{last['time']}-{direction}"
        )
    }

    print("DECISION: CANDIDATE SIGNAL")
    print("STRATEGY:", strategy)
    print("SIGNAL ID:", signal["signal_id"])

    return signal


# =========================
# SIGNAL MESSAGE
# =========================

def format_signal(signal):
    candle_time = datetime.fromtimestamp(
        signal["candle_time"] / 1000,
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    direction_text = (
        "做多 LONG"
        if signal["direction"] == "LONG"
        else "做空 SHORT"
    )

    spread_text = (
        f"{signal['ema_spread'] * 100:.4f}%"
        if signal["ema_spread"] is not None
        else "N/A"
    )

    message = (
        f"📊 Bitget 15m 策略 V{VERSION}\n\n"
        f"币种：{signal['symbol']}\n"
        f"策略：{signal['strategy']}\n"
        f"方向：{direction_text}\n"
        f"原因：{signal['reason']}\n"
        f"K线收盘：{candle_time}\n\n"
        f"均线距离：{spread_text}\n"
        f"成交量倍率："
        f"{signal['volume_ratio']:.2f}\n\n"
        f"参考入场价：{signal['entry']:.6f}\n"
        f"止损：{signal['stop']:.6f}\n"
        f"止盈：{signal['target']:.6f}\n\n"
        f"参考本金：{CAPITAL:.2f} U\n"
        f"理论数量：{signal['quantity']:.8f}\n"
        f"名义仓位：{signal['notional']:.2f} U\n"
        f"预计亏损：{signal['estimated_loss']:.2f} U\n"
        f"预计净利润：{signal['net_profit']:.2f} U\n"
        f"预计净盈亏比：1:{signal['net_rr']:.2f}\n\n"
        f"每日计划亏损上限：{MAX_DAILY_LOSS:.2f} U\n"
        f"最多同时持仓：{MAX_POSITIONS}笔\n"
        f"以上限制需在交易前检查。\n\n"
        f"⚠️ 候选信号，并非实际成交报价。\n"
        f"手续费、滑点及强平可能扩大亏损。\n"
        f"入场前请重新检测行情。"
    )

    return message


def analyze(symbol, candles):
    signal = analyze_signal(symbol, candles)

    if signal is None:
        return None

    return format_signal(signal)


# =========================
# MAIN
# =========================

def main():
    mode = os.environ.get(
        "MONITOR_MODE", "monitor"
    ).strip().lower()

    selected_symbol = os.environ.get(
        "RECHECK_SYMBOL", ""
    ).strip().upper()

    if mode not in ("monitor", "recheck"):
        raise RuntimeError("Invalid monitor mode")

    if mode == "recheck":
        if selected_symbol not in SYMBOLS:
            raise RuntimeError("Invalid recheck symbol")

        symbols = [selected_symbol]
    else:
        symbols = SYMBOLS

    print("STRATEGY VERSION:", VERSION)
    print("MODE:", mode)
    print("SYMBOLS:", symbols)

    failures = []

    for symbol in symbols:
        try:
            candles = get_candles(symbol)

            print(
                f"DATA SUCCESS: {symbol}, "
                f"{len(candles)} closed candles"
            )

            signal = analyze_signal(symbol, candles)

            if signal:
                message = format_signal(signal)

                if mode == "recheck":
                    message = (
                        "🔄 重新检测结果\n\n"
                        + message
                    )

                send_telegram(symbol, message)

            elif mode == "recheck":
                last = candles[-1]

                checked_time = datetime.now(
                    timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")

                candle_time = datetime.fromtimestamp(
                    (last["time"] + CANDLE_MS) / 1000,
                    timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")

                message = (
                    f"🔄 重新检测结果\n\n"
                    f"策略版本：V{VERSION}\n"
                    f"币种：{symbol}\n"
                    f"检测时间：{checked_time}\n"
                    f"K线收盘：{candle_time}\n"
                    f"最近收盘价：{last['close']:.6f}\n\n"
                    f"❌ 当前没有符合策略的候选信号。\n"
                    f"继续等待。\n\n"
                    f"已检测：\n"
                    f"A. 均线密集突破\n"
                    f"B. 首次EMA20回踩\n"
                    f"C. BOS结构突破"
                )

                send_telegram(symbol, message)

        except Exception as error:
            print(
                f"PROCESS FAILED: {symbol}: "
                f"{type(error).__name__}: {error}"
            )

            failures.append(symbol)

            if mode == "recheck":
                try:
                    send_telegram(
                        symbol,
                        f"⚠️ {symbol} 重新检测失败。\n"
                        f"暂时无法确认行情，请稍后重试。"
                    )
                except Exception:
                    print("Failure notification unavailable")

    if failures:
        raise RuntimeError(
            "Processing failed: " + ", ".join(failures)
        )


if __name__ == "__main__":
    main()
