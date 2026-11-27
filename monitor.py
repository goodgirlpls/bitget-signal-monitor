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
                    f"C. BOS结构突破\n"
                    f"D. 纽约开盘区间突破（ORB）\n\n"
                    f"策略D状态：{get_orb_status(symbol)}"
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


# =========================
# STRATEGY D
# NEW YORK OPENING RANGE BREAKOUT
# =========================

from zoneinfo import ZoneInfo
from datetime import timedelta

VERSION = "3.0"

NY_TZ = ZoneInfo("America/New_York")
DE_TZ = ZoneInfo("Europe/Berlin")

ORB_MIN_RR = 2.0
ORB_RETEST_TOLERANCE = 0.0005
ORB_SIGNAL_MAX_AGE_MS = 60000

ABC_ANALYZE_SIGNAL = analyze_signal

ORB_STATUS = {}


def set_orb_status(symbol, status):

    ORB_STATUS[symbol] = status

    print("ORB:", status)


def get_orb_status(symbol):

    return ORB_STATUS.get(
        symbol,
        "尚未完成检测"
    )


def orb_session_status():

    now = datetime.now(NY_TZ)

    minutes = now.hour * 60 + now.minute

    if now.weekday() >= 5:
        return "周末暂停检测"

    if minutes < 570:
        return "等待纽约开盘"

    if minutes < 575:
        return "正在建立开盘区间"

    if minutes >= 630:
        return "今日ORB交易窗口已结束"

    return "交易窗口开放，等待检测"


def get_multi_candles(symbol, granularity):

    intervals = {
        "1m": 60000,
        "5m": 300000,
        "15m": 900000
    }

    interval = intervals[granularity]

    params = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "granularity": granularity,
        "limit": "200"
    }

    url = BASE_URL + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BitgetSignalMonitor/3.0"
        }
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        result = json.load(response)

    if result.get("code") != "00000":
        raise RuntimeError("Market data API error")

    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    candles = []

    for row in result.get("data") or []:

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

        if candle["time"] + interval <= now_ms:
            candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    if not candles:
        raise RuntimeError("No closed candles")

    times = [c["time"] for c in candles]

    if len(times) != len(set(times)):
        raise RuntimeError("Duplicate candles")

    if any(
        b - a != interval
        for a, b in zip(times, times[1:])
    ):
        raise RuntimeError("Missing candles")

    if now_ms - (times[-1] + interval) >= interval:
        raise RuntimeError("Stale market data")

    return candles


def orb_session():

    now_ny = datetime.now(NY_TZ)

    session_open = now_ny.replace(
        hour=9,
        minute=30,
        second=0,
        microsecond=0
    )

    range_end = session_open + timedelta(minutes=5)

    session_end = session_open + timedelta(hours=1)

    if now_ny.weekday() >= 5:
        return None

    if not range_end <= now_ny < session_end:
        return None

    return {
        "open": session_open,
        "range_end": range_end,
        "end": session_end
    }


def analyze_orb(symbol):

    session = orb_session()

    if session is None:
        return None

    print(f"\nORB SCAN: {symbol}")

    print(
        "NEW YORK:",
        datetime.now(NY_TZ).isoformat()
    )

    print(
        "GERMANY:",
        datetime.now(DE_TZ).isoformat()
    )

    opening_ms = int(
        session["open"].timestamp() * 1000
    )

    range_end_ms = int(
        session["range_end"].timestamp() * 1000
    )

    candles_5m = get_multi_candles(
        symbol, "5m"
    )

    candles_1m = get_multi_candles(
        symbol, "1m"
    )

    opening_candle = next(
        (
            c for c in candles_5m
            if c["time"] == opening_ms
        ),
        None
    )

    if opening_candle is None:
        print("ORB: OPENING CANDLE NOT FOUND")
        return None

    orb_high = opening_candle["high"]
    orb_low = opening_candle["low"]

    print("ORB HIGH:", orb_high)
    print("ORB LOW:", orb_low)

    session_candles = [
        c for c in candles_1m
        if c["time"] >= range_end_ms
    ]

    if len(session_candles) < 2:
        return None

    if session_candles[0]["time"] != range_end_ms:
        return None

    breakout_direction = None
    breakout_index = None

    for i, candle in enumerate(session_candles[:-1]):

        if (
            candle["close"] > orb_high
            and candle["close"] > candle["open"]
        ):
            breakout_direction = "LONG"
            breakout_index = i
            break

        if (
            candle["close"] < orb_low
            and candle["close"] < candle["open"]
        ):
            breakout_direction = "SHORT"
            breakout_index = i
            break

    if breakout_direction is None:
        print("ORB: WAITING FOR BREAKOUT")
        return None

    last = session_candles[-1]
    price = last["close"]

    retest_candles = session_candles[
        breakout_index + 1:
    ]

    if not retest_candles:
        return None

    direction = breakout_direction

    if direction == "LONG":

        tolerance = (
            orb_high * ORB_RETEST_TOLERANCE
        )

        if any(
            c["close"] < orb_high - tolerance
            for c in retest_candles
        ):
            print("ORB: LONG BREAKOUT INVALIDATED")
            return None

        def valid_retest(c):
            return (
                c["low"] <= orb_high + tolerance
                and c["close"] > orb_high
                and c["close"] > c["open"]
            )

        if not valid_retest(last):
            print("ORB: WAITING FOR LONG RETEST")
            return None

        if any(
            valid_retest(c)
            for c in retest_candles[:-1]
        ):
            print("ORB: FIRST RETEST ALREADY PASSED")
            return None

        stop = min(
            c["low"] for c in retest_candles
        ) - tolerance

        reason = "纽约开盘区间向上突破后首次回踩"

    else:

        tolerance = (
            orb_low * ORB_RETEST_TOLERANCE
        )

        if any(
            c["close"] > orb_low + tolerance
            for c in retest_candles
        ):
            print("ORB: SHORT BREAKOUT INVALIDATED")
            return None

        def valid_retest(c):
            return (
                c["high"] >= orb_low - tolerance
                and c["close"] < orb_low
                and c["close"] < c["open"]
            )

        if not valid_retest(last):
            print("ORB: WAITING FOR SHORT RETEST")
            return None

        if any(
            valid_retest(c)
            for c in retest_candles[:-1]
        ):
            print("ORB: FIRST RETEST ALREADY PASSED")
            return None

        stop = max(
            c["high"] for c in retest_candles
        ) + tolerance

        reason = "纽约开盘区间向下突破后首次反抽"

    if direction == "LONG" and not 0 < stop < price:
        return None

    if direction == "SHORT" and stop <= price:
        return None

    stop_distance = abs(price - stop)

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

    estimated_loss = (
        quantity * risk_per_unit
    )

    estimated_cost = (
        quantity * price * cost_rate
    )

    target_distance = (
        ORB_MIN_RR * risk_per_unit
        + price * cost_rate
    )

    if direction == "LONG":
        target = price + target_distance
    else:
        target = price - target_distance

    if target <= 0:
        return None

    net_profit = (
        abs(target - price) * quantity
        - estimated_cost
    )

    net_rr = net_profit / estimated_loss

    if notional < 5:
        return None

    if net_rr < ORB_MIN_RR - 1e-9:
        return None

    candle_time = last["time"] + 60000

    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    if not (
        0 <= now_ms - candle_time
        < ORB_SIGNAL_MAX_AGE_MS
    ):
        print("ORB: SIGNAL EXPIRED")
        return None

    previous_volumes = [
        c["volume"]
        for c in candles_1m[-21:-1]
    ]

    average_volume = (
        sum(previous_volumes)
        / len(previous_volumes)
        if previous_volumes else 0
    )

    volume_ratio = (
        last["volume"] / average_volume
        if average_volume > 0 else 0
    )

    signal = {
        "version": VERSION,
        "symbol": symbol,
        "strategy": "D",
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
        "ema_spread": None,
        "compression": None,
        "candle_time": candle_time,
        "timeframe": "1m",
        "max_age_ms": 60000,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "signal_id": (
            f"{symbol}-ORB-"
            f"{session['open'].date()}-"
            f"{direction}"
        )
    }

    print("ORB: CANDIDATE SIGNAL")
    print("STRATEGY: D")
    print("DIRECTION:", direction)
    print("ENTRY:", price)
    print("STOP:", stop)
    print("TARGET:", target)
    print("NET RR:", net_rr)

    return signal


# =========================
# COMBINED STRATEGY ENGINE
# =========================

def analyze_signal(symbol, candles):

    set_orb_status(
        symbol,
        orb_session_status()
    )

    if orb_session() is not None:

        try:

            orb_signal = analyze_orb(symbol)

            if orb_signal is not None:

                set_orb_status(
                    symbol,
                    "发现策略D候选信号"
                )

                return orb_signal

            set_orb_status(
                symbol,
                "已检测，暂无符合条件的信号"
            )

        except Exception as error:

            set_orb_status(
                symbol,
                "检测失败，行情接口或数据异常"
            )

            print(
                "ORB SCAN FAILED:",
                symbol,
                str(error)
            )

    signal = ABC_ANALYZE_SIGNAL(
        symbol, candles
    )

    if signal is not None:
        signal["timeframe"] = "15m"
        signal["max_age_ms"] = 900000

    return signal


# =========================
# UPDATED TELEGRAM MESSAGE
# =========================

ORIGINAL_FORMAT_SIGNAL = format_signal


def format_signal(signal):

    message = ORIGINAL_FORMAT_SIGNAL(signal)

    if signal["strategy"] == "D":

        candle_dt = datetime.fromtimestamp(
            signal["candle_time"] / 1000,
            timezone.utc
        )

        ny_time = candle_dt.astimezone(
            NY_TZ
        ).strftime("%H:%M")

        de_time = candle_dt.astimezone(
            DE_TZ
        ).strftime("%H:%M")

        message = message.replace(
            "Bitget 15m 策略",
            "Bitget ORB 策略"
        )

        message += (
            "\n\n纽约开盘区间策略 D"
            f"\n纽约时间：{ny_time}"
            f"\n德国时间：{de_time}"
            f"\n区间最高价：{signal['orb_high']}"
            f"\n区间最低价：{signal['orb_low']}"
            "\n入场周期：1分钟"
        )

    return message


if __name__ == "__main__":
    main()
