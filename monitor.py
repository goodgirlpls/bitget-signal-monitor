zhel
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
BASE_URL = "https://api.bitget.com/api/v2/mix/market/candles"

CAPITAL = 50.0
MAX_RISK = 0.50
MAX_LEVERAGE = 2
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
        raise RuntimeError(f"{symbol}: API error {result}")

    raw = result.get("data") or []
    if len(raw) < 200:
        raise RuntimeError(f"{symbol}: only {len(raw)} candles")

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
        if not all(math.isfinite(v) for k, v in candle.items()
                   if k != "time"):
            raise RuntimeError("Invalid market data")
        if not (0 < candle["low"] <= candle["open"] <= candle["high"]
                and candle["low"] <= candle["close"] <= candle["high"]
                and candle["volume"] >= 0):
            raise RuntimeError("Invalid OHLCV")
        candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    times = [c["time"] for c in candles]
    if len(set(times)) != len(times):
        raise RuntimeError("Duplicate candle timestamps")
    if any(b - a != 900000 for a, b in zip(times, times[1:])):
        raise RuntimeError("Missing 15-minute candles")

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if times[-1] > now_ms + 60000:
        raise RuntimeError("Future candle timestamp")

    # Discard the current, unfinished 15-minute candle.
    completed = [
        c for c in candles
        if c["time"] + 900000 <= now_ms
    ]

    if len(completed) < 150:
        raise RuntimeError("Not enough completed candles")
    if now_ms - (completed[-1]["time"] + 900000) > 1800000:
        raise RuntimeError("Market data is stale")

    return completed


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

    # Prior 20 completed candles, excluding the signal candle.
    prior_high = max(c["high"] for c in candles[-21:-1])
    prior_low = min(c["low"] for c in candles[-21:-1])

    bos_up = price > prior_high and previous["close"] <= prior_high
    bos_down = price < prior_low and previous["close"] >= prior_low

    # First EMA20 retest within the last 10 completed candles.
    # A retest must occur after the trend is already established.
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

    recent_long = [i for i in range(len(candles)-10, len(candles))
                   if long_retest(i)]
    recent_short = [i for i in range(len(candles)-10, len(candles))
                    if short_retest(i)]

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
            reason = "20-candle breakout"
        elif first_long and price > previous["close"]:
            direction = "LONG"
            reason = "EMA20 first retest"

    elif downtrend and volume_ratio >= 1.2:
        if bos_down:
            direction = "SHORT"
            reason = "20-candle breakdown"
        elif first_short and price < previous["close"]:
            direction = "SHORT"
            reason = "EMA20 first retest"

    print(f"\n{symbol}")
    print("Candle UTC:", datetime.fromtimestamp(
        last["time"] / 1000, timezone.utc).isoformat())
    print("Close:", price)
    print("EMA20:", round(e20[-1], 4))
    print("EMA60:", round(e60[-1], 4))
    print("EMA120:", round(e120[-1], 4))
    print("Volume ratio:", round(volume_ratio, 2))
    print("BOS up/down:", bos_up, bos_down)
    print("First EMA20 retest:", first_long, first_short)

    if direction is None:
        print("DECISION: NO SIGNAL")
        return

    # Stop behind recent five-candle structure.
    if direction == "LONG":
        stop = min(c["low"] for c in candles[-5:])
        if stop >= price:
            return
        stop_distance = price - stop
    else:
        stop = max(c["high"] for c in candles[-5:])
        if stop <= price:
            return
        stop_distance = stop - price

    # Conservative round-trip cost estimate.
    cost_rate = ENTRY_FEE + EXIT_FEE + 2 * SLIPPAGE
    risk_per_unit = stop_distance + price * cost_rate

    if risk_per_unit <= 0:
        return

    quantity = min(
        MAX_RISK / risk_per_unit,
        CAPITAL * MAX_LEVERAGE / price,
    )
    notional = quantity * price
    estimated_loss = quantity * risk_per_unit

    if direction == "LONG":
        target = price + 2 * risk_per_unit
        gross_profit = (target - price) * quantity
    else:
        target = price - 2 * risk_per_unit
        if target <= 0:
            return
        gross_profit = (price - target) * quantity

    estimated_cost = quantity * price * cost_rate
    net_profit = gross_profit - estimated_cost
    net_rr = net_profit / estimated_loss

    if notional < 5 or net_rr < 1.5:
        print("DECISION: REJECTED - costs or size")
        return

    print("DECISION: CANDIDATE SIGNAL")
    print("Direction:", direction)
    print("Reason:", reason)
    print("Reference entry:", round(price, 6))
    print("Stop:", round(stop, 6))
    print("Target:", round(target, 6))
    print("Theoretical quantity:", round(quantity, 8))
    print("Notional USDT:", round(notional, 4))
    print("Estimated loss USDT:", round(estimated_loss, 4))
    print("Estimated net profit USDT:", round(net_profit, 4))
    print("Estimated net R/R:", round(net_rr, 2))
    print("INVALIDATION: structure stop breached")
    print("WARNING: historical candle-close candidate,")
    print("not a verified live executable order.")
    
    import os

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Telegram secrets missing")

    candle_time = datetime.fromtimestamp(
        last["time"] / 1000, timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    message = (
        f"Bitget 15m 模拟候选信号\n"
        f"币种：{symbol}\n"
        f"方向：{direction}\n"
        f"原因：{reason}\n"
        f"K线时间：{candle_time}\n"
        f"参考入场价：{price:.6f}\n"
        f"止损：{stop:.6f}\n"
        f"止盈：{target:.6f}\n"
        f"理论数量：{quantity:.8f}\n"
        f"预计亏损：{estimated_loss:.4f} USDT\n"
        f"预计净盈亏比：{net_rr:.2f}\n"
        f"注意：仅供模拟研究，不是实时可执行报价。"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=payload, method="POST"
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError("Telegram notification failed")

    print("TELEGRAM SIGNAL SENT")

    

def main():
    failures = []

    for symbol in SYMBOLS:
        try:
            candles = get_candles(symbol)
            print(f"\nDATA SUCCESS: {symbol}, {len(candles)} closed candles")
            analyze(symbol, candles)
        except Exception as error:
            print(f"DATA FAILED: {symbol}: {error}")
            failures.append(symbol)

    if failures:
        raise RuntimeError("Data unavailable: " + ", ".join(failures))



if __name__ == "__main__":
    main()
