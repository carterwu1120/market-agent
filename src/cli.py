"""Interactive CLI for testing the agent pipeline without Discord.

Usage:
    python -m src.cli

Commands:
    /brief          — Daily market brief
    /stock 2330     — Analyze specific stock(s)
    /clear          — Clear session memory
    /quit           — Exit
    <free text>     — Ask anything
"""

import asyncio
import sys
from datetime import datetime

from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich import print as rprint

from src.agents.graph import run_agent
from src.config import settings

console = Console()

SESSION: list[dict] = []
CLI_USER_ID = "cli-test-user"
CLI_CHANNEL_ID = "cli-test-channel"


def _print_report(report: str) -> None:
    console.print(Rule(f"[bold green]分析報告 {datetime.now().strftime('%H:%M:%S')}"))
    console.print(Markdown(report))
    console.print(Rule())


async def _run(message: str) -> None:
    with console.status("[bold yellow]分析中，請稍候...", spinner="dots"):
        try:
            result = await run_agent(
                user_message=message,
                user_id=CLI_USER_ID,
                channel_id=CLI_CHANNEL_ID,
                conversation_history=SESSION[-20:],
            )
            report = (
                result.get("final_report", "")
                if isinstance(result, dict)
                else getattr(result, "final_report", "")
            )
            sources = (
                result.get("sources", [])
                if isinstance(result, dict)
                else getattr(result, "sources", [])
            )
        except Exception as exc:
            report = f"⚠️ 錯誤：{exc}"
            sources = []

    _print_report(report or "⚠️ 無法生成報告")

    if sources:
        console.print(f"[dim]來源數量：{len(set(sources))} 筆[/dim]")

    # Keep session
    SESSION.append({"role": "user", "content": message})
    SESSION.append({"role": "assistant", "content": report[:400]})


def _handle_command(cmd: str) -> bool:
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
        asyncio.run(_run("請給我今日市場每日簡報和投資建議"))
        return True

    if directive == "/stock":
        symbols = parts[1] if len(parts) > 1 else ""
        if not symbols:
            console.print("[red]用法: /stock 2330 2454[/red]")
            return True
        asyncio.run(_run(f"請分析以下股票：{symbols}"))
        return True

    if directive == "/help":
        console.print(Panel(
            "[bold]/brief[/bold]          今日市場摘要\n"
            "[bold]/stock[/bold] [cyan]<代號>[/cyan]   分析指定股票，例如 /stock 2330\n"
            "[bold]/clear[/bold]          清除對話記憶\n"
            "[bold]/quit[/bold]           離開\n"
            "[dim]或直接輸入問題[/dim]",
            title="Market Agent CLI",
            border_style="blue",
        ))
        return True

    return False


def main() -> None:
    # Suppress noisy loggers in CLI mode
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    # Auto-init DB tables if PostgreSQL is available
    async def _init():
        from src.memory.database import try_init_db
        ok = await try_init_db()
        if ok:
            console.print("[dim]✓ DB connected[/dim]")
        else:
            console.print("[dim]⚠ DB offline — memory features disabled[/dim]")
    asyncio.run(_init())

    console.print(Panel(
        f"[bold green]Market Agent CLI[/bold green]\n"
        f"LLM: [cyan]{settings.llm_provider}/{settings.llm_model}[/cyan]\n"
        f"輸入 [bold]/help[/bold] 查看指令，[bold]/quit[/bold] 離開",
        border_style="green",
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold blue]>[/bold blue]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bold]Bye![/bold]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not _handle_command(user_input):
                console.print(f"[red]未知指令：{user_input}，輸入 /help 查看可用指令[/red]")
        else:
            asyncio.run(_run(user_input))


if __name__ == "__main__":
    main()
