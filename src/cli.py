"""Interactive CLI for testing the agent pipeline without Discord.

Also doubles as a read-only terminal dashboard for the paper-trading loop
(/status) -- see docs/paper_trading.md's "終端面板" section. Safe to run
alongside a live `python -m src.main`: both just open their own short-lived
SQLite connections to the same WAL-mode data/market_agent.db file.

Usage:
    python -m src.cli

Commands:
    /brief               — Daily market brief
    /stock 2330          — Analyze specific stock(s)
    /schedule pre|mid|post — Trigger scheduled report (盤前/盤中/收盤後)
    /status              — 紙上交易帳戶狀態（持倉/現金/報酬率/觀察名單/條件單）
    /log <N>             — 紙上交易稽核紀錄，預設最近 20 筆
    /clear               — Clear session memory
    /quit                — Exit
    <free text>          — Ask anything
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table

from src.agents.pipeline import run_agent
from src.bot.scheduler import SLOT_PROMPTS
from src.config import settings

_TW_TZ = timezone(timedelta(hours=8))

console = Console()

SESSION: list[dict] = []
CLI_USER_ID = "cli-test-user"
CLI_CHANNEL_ID = "cli-test-channel"


def _print_report(report: str) -> None:
    console.print(Rule(f"[bold green]分析報告 {datetime.now().strftime('%H:%M:%S')}"))
    console.print(Markdown(report))
    console.print(Rule())


def _pnl_style(pnl_pct: float | None) -> str:
    if pnl_pct is None:
        return "dim"
    return "green" if pnl_pct >= 0 else "red"


async def _print_status() -> None:
    """Read-only snapshot of the paper-trading account -- reuses the exact
    same functions /performance (Discord) and paper_trade_status (MCP tool)
    already use, so this never has its own separate notion of "current
    state" to drift out of sync with those."""
    from src.agents.paper_trading import evaluate_paper_trades, simulate_portfolio_equity
    from src.memory.paper_trading_store import get_active_conditions, get_watchlist
    from src.tools.market_data import get_quote

    result = await evaluate_paper_trades()
    eq = simulate_portfolio_equity(result["positions"])

    console.print(Rule("[bold cyan]紙上交易帳戶狀態"))
    return_style = _pnl_style(eq["total_return_pct"])
    console.print(
        f"起始本金 [bold]{eq['starting_capital']:,.0f}[/bold] → "
        f"目前總資產 [bold]{eq['current_equity']:,.0f}[/bold] "
        f"（累計報酬 [{return_style}]{eq['total_return_pct']}%[/{return_style}]）\n"
        f"可用現金：{eq['current_cash']:,.0f}　"
        f"已平倉最大回撤：{eq['realized_max_drawdown_pct']}%"
    )

    open_positions = [p for p in result["positions"] if p["status"] == "open"]
    closed_positions = [p for p in result["positions"] if p["status"] == "closed"]

    if open_positions:
        table = Table(title="目前持倉")
        for col in ("股票", "類型", "股數", "進場價", "現價", "未實現損益%"):
            table.add_column(col)
        for p in open_positions:
            horizon_label = "長期" if p.get("horizon") == "long_term" else "短線"
            pnl = f"{p['pnl_pct']:+.2f}%" if p["pnl_pct"] is not None else "N/A"
            style = _pnl_style(p["pnl_pct"])
            table.add_row(
                p["symbol"], horizon_label, str(p.get("shares", 0)),
                str(p["entry_price"]), str(p["current_price"]), f"[{style}]{pnl}[/{style}]",
            )
        console.print(table)
    else:
        console.print("[dim]目前沒有持倉[/dim]")

    if closed_positions:
        table = Table(title="最近已平倉")
        for col in ("股票", "類型", "股數", "進場價", "出場價", "已實現損益%"):
            table.add_column(col)
        for p in closed_positions[-10:]:
            horizon_label = "長期" if p.get("horizon") == "long_term" else "短線"
            pnl = f"{p['pnl_pct']:+.2f}%" if p["pnl_pct"] is not None else "N/A"
            style = _pnl_style(p["pnl_pct"])
            table.add_row(
                p["symbol"], horizon_label, str(p.get("shares", 0)),
                str(p["entry_price"]), str(p["current_price"]), f"[{style}]{pnl}[/{style}]",
            )
        console.print(table)

    watchlist = await get_watchlist()
    if watchlist:
        prices = await asyncio.gather(
            *[get_quote(w["symbol"]) for w in watchlist], return_exceptions=True
        )
        table = Table(title="觀察名單")
        for col in ("股票", "策略", "長線分數", "加入時間", "現價", "緊盯狀態"):
            table.add_column(col)
        for w, price in zip(watchlist, prices):
            if isinstance(price, Exception) or price.get("error"):
                price_str = "查詢失敗"
            elif price.get("price") is not None:
                price_str = f"{price['price']:.2f}"
            else:
                price_str = "N/A"
            first_seen = datetime.fromtimestamp(w["first_seen"], tz=_TW_TZ).strftime("%m/%d %H:%M")
            checked = "已緊盯過" if w["last_checked"] else "尚未緊盯"
            horizon_label = {
                "long_term": "長期觀察",
                "short_term": "短線觀察",
                "unclassified": "待分類",
            }.get(w.get("strategy_horizon"), "待分類")
            table.add_row(
                w["symbol"], horizon_label, str(w.get("assessment_score", 0)),
                first_seen, price_str, checked,
            )
        console.print(table)
    else:
        console.print("[dim]觀察名單是空的[/dim]")

    conditions = await get_active_conditions()
    if conditions:
        table = Table(title="有效條件單")
        for col in ("id", "股票", "條件", "動作"):
            table.add_column(col)
        for c in conditions:
            table.add_row(
                str(c["id"]), c["symbol"],
                f"{c['indicator']} {c['operator']} {c['threshold']}", c["action"],
            )
        console.print(table)
    else:
        console.print("[dim]沒有還在等待的條件單[/dim]")


async def _print_log(limit: int) -> None:
    """Reads paper_trading_log -- the permanent audit trail of what the
    loop itself did (broad/tight scans, mechanical triggers, trades,
    condition changes), not to be confused with SESSION (chat history)."""
    from src.memory.paper_trading_store import get_recent_log

    events = await get_recent_log(limit)
    if not events:
        console.print("[dim]還沒有任何紀錄[/dim]")
        return

    table = Table(title=f"紙上交易稽核紀錄（最近 {len(events)} 筆）")
    for col in ("時間", "事件", "股票", "詳情"):
        table.add_column(col)
    for e in events:
        ts = datetime.fromtimestamp(e["ts"], tz=_TW_TZ).strftime("%m/%d %H:%M:%S")
        table.add_row(ts, e["event_type"], e.get("symbol") or "-", e["detail"])
    console.print(table)


async def _run(message: str) -> None:
    with console.status("[bold yellow]分析中，請稍候...", spinner="dots"):
        try:
            result = await run_agent(
                user_message=message,
                user_id=CLI_USER_ID,
                channel_id=CLI_CHANNEL_ID,
                conversation_history=SESSION[-20:],
            )
            report = result.get("final_report", "")
            if isinstance(report, list):
                report = "\n".join(str(r) for r in report)
            sources = result.get("sources", [])
            intent = result.get("intent", "")
            target_symbols = result.get("target_symbols", [])
            conclusion = result.get("conclusion", "")
        except Exception as exc:
            report = f"⚠️ 錯誤：{exc}"
            sources = []
            intent = ""
            target_symbols = []
            conclusion = ""

    _print_report(report or "⚠️ 無法生成報告")

    if sources:
        console.print(f"[dim]來源數量：{len(set(sources))} 筆[/dim]")

    SESSION.append({"role": "user", "content": message})
    SESSION.append({
        "role": "assistant",
        "content": conclusion or report[:400],
        "meta": {
            "symbols": target_symbols,
            "intent": intent,
        },
    })


async def _handle_command_async(cmd: str) -> bool:
    """Handle slash commands. Returns True if handled, False if not a command."""
    parts = cmd.strip().split(maxsplit=1)
    directive = parts[0].lower()

    if directive == "/quit":
        console.print("[bold]Bye![/bold]")
        sys.exit(0)

    if directive == "/clear":
        SESSION.clear()
        console.print("[green]✓ Session 已清除[/green]")
        return True

    if directive == "/brief":
        await _run("請給我今日市場每日簡報和投資建議")
        return True

    if directive == "/stock":
        symbols = parts[1] if len(parts) > 1 else ""
        if not symbols:
            console.print("[red]用法: /stock 2330 2454[/red]")
            return True
        await _run(f"請分析以下股票：{symbols}")
        return True

    if directive == "/schedule":
        slot_map = {"pre": "pre_market", "mid": "mid_session", "post": "post_market"}
        slot_key = parts[1].lower() if len(parts) > 1 else ""
        if slot_key not in slot_map:
            console.print("[red]用法: /schedule pre|mid|post[/red]")
            return True
        await _run(SLOT_PROMPTS[slot_map[slot_key]])
        return True

    if directive == "/status":
        with console.status("[bold yellow]查詢中...", spinner="dots"):
            await _print_status()
        return True

    if directive == "/log":
        limit_str = parts[1] if len(parts) > 1 else "20"
        limit = int(limit_str) if limit_str.isdigit() else 20
        with console.status("[bold yellow]查詢中...", spinner="dots"):
            await _print_log(limit)
        return True

    if directive == "/help":
        console.print(Panel(
            "[bold]/brief[/bold]                今日市場摘要\n"
            "[bold]/stock[/bold] [cyan]<代號>[/cyan]       分析指定股票，例如 /stock 2330\n"
            "[bold]/schedule[/bold] [cyan]pre|mid|post[/cyan]  觸發排程報告（盤前/盤中/收盤後）\n"
            "[bold]/status[/bold]               紙上交易帳戶狀態\n"
            "                     （持倉/現金/報酬率/觀察名單/條件單）\n"
            "[bold]/log[/bold] [cyan]<N>[/cyan]              紙上交易稽核紀錄，預設最近 20 筆\n"
            "[bold]/clear[/bold]                清除對話記憶\n"
            "[bold]/quit[/bold]                 離開\n"
            "[dim]或直接輸入問題[/dim]",
            title="Market Agent CLI",
            border_style="blue",
        ))
        return True

    return False


async def _main_async() -> None:
    from src.memory.store import init_storage
    ok = await init_storage()
    if ok:
        console.print("[dim]✓ 本機資料庫已就緒[/dim]")
    else:
        console.print("[dim]⚠ 本機資料庫初始化失敗，記憶功能停用[/dim]")

    console.print(Panel(
        "[bold green]Market Agent CLI[/bold green]\n"
        f"LLM: [cyan]{settings.llm_backend}[/cyan]\n"
        "輸入 [bold]/help[/bold] 查看指令，[bold]/quit[/bold] 離開",
        border_style="green",
    ))

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: Prompt.ask("\n[bold blue]>[/bold blue]").strip()
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bold]Bye![/bold]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not await _handle_command_async(user_input):
                console.print(f"[red]未知指令：{user_input}，輸入 /help 查看可用指令[/red]")
        else:
            await _run(user_input)


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
