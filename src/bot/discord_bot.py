"""Discord bot adapter.

Commands:
  /brief          — Today's market daily brief
  /stock <code>   — Analyze specific stock(s)
  /clear          — Clear current session memory
  /help           — Show available commands

Free-text messages trigger the orchestrator automatically.
"""

import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger

from src.agents.pipeline import run_agent
from src.config import settings
from src.memory.session_store import append_message, clear_session, get_session_messages
from src.memory.store import init_storage

MAX_DISCORD_LENGTH = 1900  # leave room for formatting


def chunk_message(text: str, max_len: int = MAX_DISCORD_LENGTH) -> list[str]:
    """Split long text into Discord-safe chunks."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


class MarketAgentBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_storage()
        if settings.discord_guild_id:
            guild = discord.Object(id=int(settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        logger.info("Discord bot ready, slash commands synced")
        from src.bot.scheduler import start_scheduled_tasks
        start_scheduled_tasks(self)

        if settings.paper_trading_enabled:
            import asyncio

            from src.agents.paper_trading_loop import run as run_paper_trading
            asyncio.create_task(run_paper_trading())
            logger.info("Paper trading loop started as background task")
        else:
            logger.info("Paper trading loop disabled (PAPER_TRADING_ENABLED=false)")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (id: {self.user.id})")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="市場動態 | /brief /stock /help"
        ))


bot = MarketAgentBot()


def _is_allowed_channel(channel_id: str) -> bool:
    allowed = settings.allowed_channels
    return not allowed or channel_id in allowed


async def _process_and_reply(
    interaction_or_message,
    user_message: str,
    is_interaction: bool = True,
) -> None:
    """Core handler: run the agent pipeline and reply."""
    if is_interaction:
        ctx = interaction_or_message
        user_id = str(ctx.user.id)
        username = str(ctx.user)
        channel_id = str(ctx.channel_id)
        if not _is_allowed_channel(channel_id):
            await ctx.response.send_message("❌ 此頻道不開放使用，請前往指定頻道。", ephemeral=True)
            return
        await ctx.response.defer(thinking=True)
        async def send(text: str):
            await ctx.followup.send(text)
    else:
        msg = interaction_or_message
        user_id = str(msg.author.id)
        username = str(msg.author)
        channel_id = str(msg.channel.id)
        if not _is_allowed_channel(channel_id):
            return  # silently ignore in non-slash context
        async def send(text: str):
            await msg.channel.send(text)

    # Load session history — best-effort, empty on failure
    try:
        history = await get_session_messages(channel_id, user_id)
    except Exception as exc:
        logger.warning(f"Session read failed, proceeding with empty history: {exc}")
        history = []

    intent = ""
    target_symbols = []
    conclusion = ""
    try:
        result = await run_agent(
            user_message=user_message,
            user_id=user_id,
            channel_id=channel_id,
            conversation_history=history,
        )
        report = result.get("final_report", "") or "⚠️ 無法生成報告，請稍後再試。"
        intent = result.get("intent", "")
        target_symbols = result.get("target_symbols", [])
        conclusion = result.get("conclusion", "")
    except Exception as exc:
        logger.error(f"Agent pipeline error: {exc}", exc_info=True)
        report = f"⚠️ 系統錯誤：{exc}"

    # Send response first — cache/persistence failures must not block the reply
    for chunk in chunk_message(report):
        await send(chunk)

    # Persist to session store — best-effort
    try:
        await append_message(channel_id, user_id, "user", user_message, username=username)
        await append_message(
            channel_id, user_id, "assistant",
            content=conclusion or report[:500],
            meta={"symbols": target_symbols, "intent": intent},
        )
    except Exception as exc:
        logger.warning(f"Session write failed (not saved): {exc}")


# ── Slash Commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="brief", description="今日市場摘要與投資建議")
async def cmd_brief(interaction: discord.Interaction):
    await _process_and_reply(interaction, "請給我今日市場每日簡報和投資建議")


@bot.tree.command(name="stock", description="分析指定股票")
@app_commands.describe(symbols="股票代號，多個用空格分隔，例如: 2330 2454")
async def cmd_stock(interaction: discord.Interaction, symbols: str):
    await _process_and_reply(interaction, f"請分析以下股票：{symbols}")


@bot.tree.command(name="clear", description="清除目前對話記憶")
async def cmd_clear(interaction: discord.Interaction):
    await clear_session(str(interaction.channel_id), str(interaction.user.id))
    await interaction.response.send_message("✅ 對話記憶已清除", ephemeral=True)


@bot.tree.command(name="performance", description="查看 agent 過去建議的紙上交易績效")
async def cmd_performance(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    from src.agents.paper_trading import evaluate_paper_trades, simulate_portfolio_equity

    result = await evaluate_paper_trades()
    if not result["positions"]:
        await interaction.followup.send("目前還沒有任何紙上交易記錄。")
        return

    eq = simulate_portfolio_equity(result["positions"])
    lines = [
        f"**紙上交易績效**（持有中 {result['open_count']} 筆，已平倉 {result['closed_count']} 筆）",
        (
            f"勝率（已平倉，跟部位大小無關）：{result['win_rate']}%"
            if result["win_rate"] is not None
            else "勝率：資料不足"
        ),
        (
            f"平均報酬（已平倉，跟部位大小無關）：{result['avg_return_pct']}%"
            if result["avg_return_pct"] is not None
            else "平均報酬：資料不足"
        ),
        (
            f"模擬帳戶：起始本金 {eq['starting_capital']:,.0f} → 目前總資產 "
            f"{eq['current_equity']:,.0f}（累計報酬 {eq['total_return_pct']}%，"
            f"可用現金 {eq['current_cash']:,.0f}，"
            f"已平倉最大回撤 {eq['realized_max_drawdown_pct']}%）"
        ),
        "",
    ]
    for p in result["positions"][-15:]:
        pnl = f"{p['pnl_pct']:+.2f}%" if p["pnl_pct"] is not None else "N/A"
        horizon_label = "長期" if p.get("horizon") == "long_term" else "短線"
        shares = p.get("shares", 0)
        if p["status"] == "open":
            lines.append(
                f"- {p['symbol']}（{horizon_label}，{shares}股）持有中（{p['entry_date']} 進場 "
                f"{p['entry_price']}）→ 浮動 {pnl}"
            )
        else:
            lines.append(
                f"- {p['symbol']}（{horizon_label}，{shares}股）已平倉（{p['entry_date']} 進場 "
                f"{p['entry_price']} → {p['exit_date']} 出場 {p['exit_price']}，"
                f"{p['exit_reason']}）→ 實現 {pnl}"
            )
    await interaction.followup.send("\n".join(lines))


@bot.tree.command(name="watchlist", description="查看紙上交易目前的觀察名單")
async def cmd_watchlist(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    from datetime import datetime, timedelta, timezone

    from src.memory.paper_trading_store import get_watchlist

    watchlist = await get_watchlist()
    if not watchlist:
        await interaction.followup.send("目前觀察名單是空的。")
        return

    tw_tz = timezone(timedelta(hours=8))
    lines = [f"**觀察名單**（共 {len(watchlist)} 檔，尚未買進）"]
    for w in watchlist:
        first_seen = datetime.fromtimestamp(w["first_seen"], tz=tw_tz).strftime("%m/%d %H:%M")
        checked = "尚未緊盯過" if w["last_checked"] == 0 else "已被緊盯過"
        lines.append(f"- {w['symbol']}（{first_seen} 加入，{checked}）")
    await interaction.followup.send("\n".join(lines))


@bot.tree.command(name="help", description="顯示使用說明")
async def cmd_help(interaction: discord.Interaction):
    help_text = (
        "**Market Agent 使用說明**\n"
        "```\n"
        "/brief          — 今日市場摘要，含新聞、技術面、籌碼面\n"
        "/stock <codes>  — 分析指定股票，例如: /stock 2330 2454\n"
        "/clear          — 清除對話記憶，開始新的對話\n"
        "/performance    — 查看 agent 過去建議的紙上交易績效\n"
        "/watchlist      — 查看紙上交易目前的觀察名單\n"
        "/help           — 顯示此說明\n"
        "```\n"
        "💡 也可以直接輸入問題，例如：\n"
        "- `台積電最近怎樣？`\n"
        "- `今天有什麼值得關注的科技股？`\n"
        "- `2330 的技術面分析`"
    )
    await interaction.response.send_message(help_text)


# ── Free-text message handler ─────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # Ignore messages that are slash commands
    if message.content.startswith("/"):
        return
    # Only respond when mentioned or in DM
    if bot.user in message.mentions or isinstance(message.channel, discord.DMChannel):
        content = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if content:
            await _process_and_reply(message, content, is_interaction=False)

    await bot.process_commands(message)


def run():
    if not settings.discord_bot_token:
        raise ValueError("DISCORD_BOT_TOKEN is not set in .env")
    bot.run(settings.discord_bot_token)
