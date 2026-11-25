import os
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone


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


def ema(values, period):
    result = []
    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period

    for i, value in enumerate(values):
        if i < period - 1:
            result.append(None)
        elif i == period - 1:
            result.append(current)
        else:
            current = value * multiplier + current * (1 - multiplier)
            result.append(current)

    return result


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
        headers={"User-Agent": "BitgetSignalMonitor/2.0"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
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
            "volume": float(row[5]),
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
        raise RuntimeError("Duplicate candle timestamps")

    if any(
        b - a != 900000
        for a, b in zip(times, times[1:])
    ):
        raise RuntimeError("Missing 15-minute candles")

    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    if times[-1] > now_ms + 60000:
        raise RuntimeError("Future candle timestamp")

    completed = [
        c for c in candles
        if c["time"] + 900000 <= now_ms
    ]

    if len(completed) < 150:
        raise RuntimeError("Not enough completed candles")

    if now_ms - (
        completed[-1]["time"] + 900000
    ) > 1800000:
        raise RuntimeError("Market data is stale")

    return completed


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

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError(
            "Telegram notification failed"
        ) from None

    if not result.get("ok"):
        raise RuntimeError("Telegram notification failed")

    print("TELEGRAM MESSAGE SENT")


def analyze(symbol, candles):
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

    prior_high = max(
        c["high"] for c in candles[-21:-1]
    )

    prior_low = min(
        c["low"] for c in candles[-21:-1]
    )

    bos_up = (
        price > prior_high
        and previous["close"] <= prior_high
    )

    bos_down = (
        price < prior_low
        and previous["close"] >= prior_low
    )

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
        i for i in range(len(candles) - 10, len(candles))
        if long_retest(i)
    ]

    recent_short = [
        i for i in range(len(candles) - 10, len(candles))
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

    direction = None
    reason = None

    if uptrend and volume_ratio >= 1.2:
        if bos_up:
            direction = "LONG"
            reason = "20根K线向上突破"
        elif first_long and price > previous["close"]:
            direction = "LONG"
            reason = "第一次回踩EMA20"

    elif downtrend and volume_ratio >= 1.2:
        if bos_down:
            direction = "SHORT"
            reason = "20根K线向下突破"
        elif first_short and price < previous["close"]:
            direction = "SHORT"
            reason = "第一次反抽EMA20"

    print(f"\nSYMBOL: {symbol}")
    print("Close:", price)
    print("EMA20:", e20[-1])
    print("EMA60:", e60[-1])
    print("EMA120:", e120[-1])
    print("Volume ratio:", round(volume_ratio, 2))
    print("BOS up/down:", bos_up, bos_down)
    print("First retest:", first_long, first_short)

    if direction is None:
        print("DECISION: NO SIGNAL")
        return None

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
        MIN_RR * risk_per_unit + price * cost_rate
    )

    if direction == "LONG":
        target = price + target_distance
        gross_profit = (target - price) * quantity
    else:
        target = price - target_distance

        if target <= 0:
            return None

        gross_profit = (price - target) * quantity

    net_profit = gross_profit - estimated_cost

    if estimated_loss <= 0:
        return None

    net_rr = net_profit / estimated_loss

    if notional < 5 or net_rr < MIN_RR - 1e-9:
        print("DECISION: REJECTED")
        return None

    candle_time = datetime.fromtimestamp(
        (last["time"] + 900000) / 1000,
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    direction_text = (
        "做多 LONG"
        if direction == "LONG"
        else "做空 SHORT"
    )

    message = (
        f"📊 Bitget 15m 候选信号\n\n"
        f"币种：{symbol}\n"
        f"方向：{direction_text}\n"
        f"原因：{reason}\n"
        f"K线收盘：{candle_time}\n\n"
        f"参考入场价：{price:.6f}\n"
        f"止损：{stop:.6f}\n"
        f"止盈：{target:.6f}\n\n"
        f"参考本金：{CAPITAL:.2f} U\n"
        f"理论数量：{quantity:.8f}\n"
        f"名义仓位：{notional:.2f} U\n"
        f"预计亏损：{estimated_loss:.2f} U\n"
        f"预计净利润：{net_profit:.2f} U\n"
        f"预计净盈亏比：1:{net_rr:.2f}\n\n"
        f"每日计划亏损上限：{MAX_DAILY_LOSS:.2f} U\n"
        f"最多同时持仓：{MAX_POSITIONS}笔\n"
        f"以上持仓及亏损限制需手动检查。\n\n"
        f"⚠️ 模拟候选信号，并非实时成交报价。\n"
        f"实际手续费、滑点及强平风险可能导致更大亏损。\n"
        f"入场前请重新检测行情。"
    )

    print("DECISION: CANDIDATE SIGNAL")

    return message


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

            message = analyze(symbol, candles)

            if message:
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
                    (last["time"] + 900000) / 1000,
                    timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")

                message = (
                    f"🔄 重新检测结果\n\n"
                    f"币种：{symbol}\n"
                    f"检测时间：{checked_time}\n"
                    f"K线收盘：{candle_time}\n"
                    f"最近收盘价：{last['close']:.6f}\n\n"
                    f"❌ 当前没有符合策略的候选信号。\n"
                    f"继续等待。\n\n"
                    f"本次检测基于最新已收盘的15分钟K线。"
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
