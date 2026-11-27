import os
import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from execution import demo_get, get_history_positions
from protection import get_protection_orders
from state_store import load_state
from trader import send_telegram, EXECUTION_ENABLED

CAPITAL = 145.0
MAX_RISK = 5.0
MAX_DAILY_LOSS = 10.0
MAX_POSITIONS = 3

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"

MODE = os.getenv("DASHBOARD_MODE", "overview")
GERMANY = ZoneInfo("Europe/Berlin")


def num(value):
    result = float(value)

    if not math.isfinite(result):
        raise RuntimeError("Invalid numeric data")

    return result


def now_text():
    return datetime.now(GERMANY).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def get_account():
    accounts = demo_get(
        "/api/v2/mix/account/accounts",
        {"productType": PRODUCT_TYPE}
    )

    if not isinstance(accounts, list):
        raise RuntimeError("Invalid account response")

    account = next(
        (
            item for item in accounts
            if item.get("marginCoin") == MARGIN_COIN
        ),
        None
    )

    if account is None:
        raise RuntimeError("USDT account not found")

    return account


def get_positions():
    positions = demo_get(
        "/api/v2/mix/position/all-position",
        {
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN
        }
    )

    if not isinstance(positions, list):
        raise RuntimeError("Invalid positions response")

    return [
        p for p in positions
        if num(p.get("total") or 0) > 0
    ]


def get_today_history():
    now = datetime.now(GERMANY)

    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    data = get_history_positions(
        int(start.timestamp() * 1000),
        int(now.timestamp() * 1000)
    )

    if not isinstance(data, dict):
        raise RuntimeError("Invalid history response")

    records = data.get("list")

    if not isinstance(records, list):
        raise RuntimeError("Invalid history list")

    if len(records) >= 100:
        raise RuntimeError("History pagination required")

    return records


def overview():
    account = get_account()
    positions = get_positions()

    equity = num(account["accountEquity"])
    available = num(account["available"])

    profit = equity - CAPITAL
    roi = profit / CAPITAL * 100

    floating = sum(
        num(p.get("unrealizedPL") or 0)
        for p in positions
    )

    return (
        "📊 账户总览\n"
        "━━━━━━━━━━━━━━\n"
        "环境：Bitget Demo\n"
        f"初始本金：{CAPITAL:.2f} U\n"
        f"当前权益：{equity:.4f} U\n"
        f"可用余额：{available:.4f} U\n"
        f"权益变化：{profit:+.4f} U\n"
        f"累计收益率：{roi:+.2f}%\n"
        f"浮动盈亏：{floating:+.4f} U\n"
        f"当前持仓：{len(positions)}\n"
        f"更新时间：{now_text()}"
    )


def positions_report():
    positions = get_positions()

    if not positions:
        return (
            "📈 当前持仓\n"
            "━━━━━━━━━━━━━━\n"
            "目前没有持仓。\n"
            f"更新时间：{now_text()}"
        )

    state, _ = load_state()
    records = state["positions"]

    lines = [
        "📈 当前持仓",
        "━━━━━━━━━━━━━━"
    ]

    for p in positions:
        symbol = p["symbol"]
        side = p["holdSide"]
        direction = "LONG" if side == "long" else "SHORT"

        key = f"{symbol}:{direction}"
        record = records.get(key)

        lines.extend([
            "",
            f"{symbol} | {direction}",
            f"开仓价：{p.get('openPriceAvg', '未知')}",
            f"标记价格：{p.get('markPrice', '未知')}",
            f"持仓数量：{p.get('total', '未知')}",
            f"杠杆：{p.get('leverage', '未知')}x",
            f"浮动盈亏：{num(p.get('unrealizedPL') or 0):+.4f} U"
        ])

        if record:
            lines.extend([
                f"策略：{record.get('strategy', '未知')}",
                f"记录止损：{record.get('current_stop', '未知')}",
                f"第一止盈：{record.get('tp1_price', '未知')}",
                f"管理状态：{record.get('status', '未知')}",
                f"分批止盈：{'已完成' if record.get('tp1_completed') else '等待中'}",
                f"保本止损：{'已启动' if record.get('breakeven_activated') else '未启动'}",
                f"追踪止损：{'已启动' if record.get('trailing_activated') else '未启动'}"
            ])
        else:
            lines.append("⚠️ 未找到本地管理记录")

    lines.append(f"\n更新时间：{now_text()}")

    return "\n".join(lines)


def history_report():
    records = get_today_history()

    net = sum(
        num(p["netProfit"])
        for p in records
    )

    wins = sum(
        num(p["netProfit"]) > 0
        for p in records
    )

    losses = sum(
        num(p["netProfit"]) < 0
        for p in records
    )

    fees = sum(
        abs(num(p.get("openFee") or 0))
        + abs(num(p.get("closeFee") or 0))
        for p in records
    )

    lines = [
        "📋 今日已平仓交易",
        "━━━━━━━━━━━━━━",
        f"已平仓记录：{len(records)}",
        f"盈利：{wins}",
        f"亏损：{losses}",
        f"净收益：{net:+.4f} U",
        f"手续费：{fees:.4f} U"
    ]

    if records:
        lines.append(
            f"胜率：{wins / len(records) * 100:.2f}%"
        )

    for p in records[-10:]:
        lines.extend([
            "",
            f"{p.get('symbol')} | {p.get('holdSide')}",
            f"入场：{p.get('openAvgPrice', '未知')}",
            f"出场：{p.get('closeAvgPrice', '未知')}",
            f"净收益：{num(p['netProfit']):+.4f} U"
        ])

    lines.append(f"\n更新时间：{now_text()}")

    return "\n".join(lines)


def risk_report():
    from trader import (
        account_snapshot,
        daily_loss,
        open_position_risk
    )
    from contracts import fetch_contracts
    import trader

    trader.CONTRACTS = fetch_contracts()

    snapshot = account_snapshot()
    realized_loss, net_profit = daily_loss()

    open_risk = open_position_risk(snapshot)

    remaining = max(
        0.0,
        MAX_DAILY_LOSS - realized_loss - open_risk
    )

    return (
        "🛡 风险监控\n"
        "━━━━━━━━━━━━━━\n"
        f"单笔计划风险上限：{MAX_RISK:.2f} U\n"
        f"每日计划风险上限：{MAX_DAILY_LOSS:.2f} U\n"
        f"已实现亏损：{realized_loss:.4f} U\n"
        f"当前预留风险：{open_risk:.4f} U\n"
        f"剩余风险预算：{remaining:.4f} U\n"
        f"今日净收益（UTC）：{net_profit:+.4f} U\n"
        f"当前持仓：{len(snapshot['positions'])}/{MAX_POSITIONS}\n"
        f"待成交订单：{len(snapshot['pending'])}\n"
        "保护订单检查：通过\n"
        f"更新时间：{now_text()}"
    )


def system_report():
    account = get_account()
    positions = get_positions()

    state, _ = load_state()

    lines = [
        "🤖 系统状态",
        "━━━━━━━━━━━━━━",
        "交易环境：Bitget Demo",
        "账户API：读取成功",
        "状态存储：读取成功",
        f"代码交易开关：{'开启' if EXECUTION_ENABLED else '关闭'}",
        f"账户权益：{num(account['accountEquity']):.4f} U",
        f"当前持仓：{len(positions)}",
        f"管理记录：{len(state['positions'])}",
        ""
    ]

    for symbol in SYMBOLS:
        plans = get_protection_orders(symbol)

        lines.append(
            f"{symbol} 保护订单：{len(plans)}"
        )

    lines.extend([
        "",
        "本次查询API正常。",
        "不代表最近一次自动交易任务成功。",
        f"更新时间：{now_text()}"
    ])

    return "\n".join(lines)


def daily_report():
    return (
        "📊 每日量化交易报告\n\n"
        + overview()
        + "\n\n"
        + history_report()
        + "\n\n"
        + risk_report()
    )


def main():
    handlers = {
        "overview": overview,
        "positions": positions_report,
        "history": history_report,
        "risk": risk_report,
        "system": system_report,
        "daily": daily_report
    }

    if MODE not in handlers:
        raise RuntimeError("Invalid dashboard mode")

    message = handlers[MODE]()

    print(message)

    while message:
        if len(message) <= 3500:
            send_telegram(message)
            break

        cut = message.rfind("\n", 0, 3500)

        if cut <= 0:
            cut = 3500

        send_telegram(message[:cut])
        message = message[cut:].lstrip("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(type(error).__name__)

        send_telegram(
            "⚠️ Dashboard 查询失败\n"
            f"查询类型：{MODE}\n"
            f"异常类型：{type(error).__name__}\n"
            "请查看 GitHub Actions 日志。\n"
            "本次查询不会提交交易订单。"
        )

        raise
