"""Discord bot adapter.

Commands:
  /brief          — Today's market daily brief
  /stock <code>   — Analyze specific stock(s)
  /clear          — Clear current session memory
  /help           — Show available commands

Free-text messages trigger the orchestrator automatically.
"""

import asyncio
from loguru import logger
import discord
from discord import app_commands
from discord.ext import commands

from src.config import settings
from src.agents.graph import run_agent
from src.memory.session_store import get_session_messages, append_message, clear_session
from src.memory.database import init_db, AsyncSessionFactory
from src.memory.conversation_repo import (
    get_or_create_user,
    get_or_create_conversation,
    save_message,
)

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
        await init_db()
        if settings.discord_guild_id:
            guild = discord.Object(id=int(settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        logger.info("Discord bot ready, slash commands synced")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (id: {self.user.id})")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="市場動態 | /brief /stock /help"
        ))


bot = MarketAgentBot()


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
        await ctx.response.defer(thinking=True)
        async def send(text: str):
            await ctx.followup.send(text)
    else:
        msg = interaction_or_message
        user_id = str(msg.author.id)
        username = str(msg.author)
        channel_id = str(msg.channel.id)
        async def send(text: str):
            await msg.channel.send(text)

    # Load session history from Redis — best-effort, empty on failure
    try:
        history = await get_session_messages(channel_id, user_id)
    except Exception as exc:
        logger.warning(f"Redis read failed, proceeding with empty history: {exc}")
        history = []

    try:
        result = await run_agent(
            user_message=user_message,
            user_id=user_id,
            channel_id=channel_id,
            conversation_history=history,
        )
        report = result.get("final_report", "") if isinstance(result, dict) else getattr(result, "final_report", "")
        if not report:
            report = "⚠️ 無法生成報告，請稍後再試。"
        intent = result.get("intent", "") if isinstance(result, dict) else getattr(result, "intent", "")
        target_symbols = result.get("target_symbols", []) if isinstance(result, dict) else getattr(result, "target_symbols", [])
        conclusion = result.get("conclusion", "") if isinstance(result, dict) else getattr(result, "conclusion", "")
    except Exception as exc:
        logger.error(f"Agent pipeline error: {exc}", exc_info=True)
        report = f"⚠️ 系統錯誤：{exc}"

    # Send response first — cache/persistence failures must not block the reply
    for chunk in chunk_message(report):
        await send(chunk)

    # Persist to Redis session — best-effort
    try:
        await append_message(channel_id, user_id, "user", user_message)
        await append_message(
            channel_id, user_id, "assistant",
            content=conclusion or report[:500],
            meta={"symbols": target_symbols, "intent": intent},
        )
    except Exception as exc:
        logger.warning(f"Redis write failed (session not saved): {exc}")

    # Persist to PostgreSQL async — errors logged inside the task
    asyncio.create_task(_persist_to_db(user_id, username, channel_id, user_message, report))


async def _persist_to_db(
    user_id: str, username: str, channel_id: str, user_msg: str, assistant_msg: str
) -> None:
    try:
        async with AsyncSessionFactory() as session:
            user = await get_or_create_user(session, int(user_id), username)
            conv = await get_or_create_conversation(session, user.id, channel_id)
            await save_message(session, conv.id, "user", user_msg)
            await save_message(session, conv.id, "assistant", assistant_msg)
    except Exception as exc:
        logger.error(f"DB persistence failed for user {user_id}: {exc}")


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


@bot.tree.command(name="help", description="顯示使用說明")
async def cmd_help(interaction: discord.Interaction):
    help_text = (
        "**Market Agent 使用說明**\n"
        "```\n"
        "/brief          — 今日市場摘要，含新聞、技術面、籌碼面\n"
        "/stock <codes>  — 分析指定股票，例如: /stock 2330 2454\n"
        "/clear          — 清除對話記憶，開始新的對話\n"
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
