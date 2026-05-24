"""Interactive CLI for testing the agent pipeline without Discord.

Usage:
    python -m src.cli

Commands:
    /brief               — Daily market brief
    /stock 2330          — Analyze specific stock(s)
    /schedule pre|mid|post — Trigger scheduled report (盤前/盤中/收盤後)
    /clear               — Clear session memory
    /quit                — Exit
    <free text>          — Ask anything
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

from src.agents.graph import run_agent
from src.config import settings
from src.bot.scheduler import SLOT_PROMPTS

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
            if isinstance(report, list):
                report = "\n".join(str(r) for r in report)
            sources = (
                result.get("sources", [])
                if isinstance(result, dict)
                else getattr(result, "sources", [])
            )
            intent = (
                result.get("intent", "")
                if isinstance(result, dict)
                else getattr(result, "intent", "")
            )
            target_symbols = (
                result.get("target_symbols", [])
                if isinstance(result, dict)
                else getattr(result, "target_symbols", [])
            )
            conclusion = (
                result.get("conclusion", "")
                if isinstance(result, dict)
                else getattr(result, "conclusion", "")
            )
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

    if directive == "/help":
        console.print(Panel(
            "[bold]/brief[/bold]                今日市場摘要\n"
            "[bold]/stock[/bold] [cyan]<代號>[/cyan]       分析指定股票，例如 /stock 2330\n"
            "[bold]/schedule[/bold] [cyan]pre|mid|post[/cyan]  觸發排程報告（盤前/盤中/收盤後）\n"
            "[bold]/clear[/bold]                清除對話記憶\n"
            "[bold]/quit[/bold]                 離開\n"
            "[dim]或直接輸入問題[/dim]",
            title="Market Agent CLI",
            border_style="blue",
        ))
        return True

    return False


async def _main_async() -> None:
    # Auto-init DB tables if PostgreSQL is available
    from src.memory.database import try_init_db
    ok = await try_init_db()
    if ok:
        console.print("[dim]✓ DB connected[/dim]")
    else:
        console.print("[dim]⚠ DB offline — memory features disabled[/dim]")

    console.print(Panel(
        f"[bold green]Market Agent CLI[/bold green]\n"
        f"LLM: [cyan]{settings.llm_provider}/{settings.llm_model}[/cyan]\n"
        f"輸入 [bold]/help[/bold] 查看指令，[bold]/quit[/bold] 離開",
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
