"""Scheduled market reports — 盤前 / 盤中 / 收盤後 / 假日消息摘要.

Uses discord.ext.tasks (bundled with discord.py) to fire at fixed wall-clock
times in Asia/Taipei timezone. No extra dependencies required.

Times:
  08:30 — pre_market     (盤前，平日)
  12:00 — mid_session    (盤中，平日)
  14:30 — post_market    (收盤後，平日)
  20:00 — weekend_digest (假日消息摘要，只在週日送出)

Taiwan stock market is closed Sat/Sun, so pre/mid/post_market skip weekends
outright — their technical/chip data wouldn't have changed since Friday's
close. weekend_digest replaces them on Sunday with a news-only summary
that flags events (macro, geopolitical) that could move Monday's open —
see run_weekend_digest() in src/agents/daily_brief.py.
"""

from __future__ import annotations

import datetime
import zoneinfo

from discord.ext import tasks
from loguru import logger

from src.agents.daily_brief import run_weekend_digest
from src.agents.pipeline import run_agent
from src.bot.discord_bot import chunk_message
from src.config import settings

SLOT_PROMPTS = {
    "pre_market": (
        "請給我今日盤前報告：昨日台股收盤摘要、隔夜美股主要指數表現、"
        "三大法人昨日買賣超動向，以及今日開盤需要關注的重點和潛在機會。"
    ),
    "mid_session": (
        "請給我今日盤中報告：目前台股主要指數價格與成交量狀況、"
        "今日盤勢強弱研判，以及下午盤可能的方向與注意事項。"
    ),
    "post_market": (
        "請給我今日收盤後報告：今日台股各指數與個股漲跌幅統計、"
        "三大法人今日買賣超明細、市場總結，以及明日操作策略建議。"
    ),
}

_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
_bot = None


async def _dispatch_report(label: str, should_run: bool, skip_reason: str, generate) -> None:
    """Shared logic behind every scheduled slot: day-check, channel lookup,
    generate-and-send with a consistent error fallback. _send_scheduled_report
    and _send_weekend_digest differ only in their day predicate and their
    report generator, so a fix here (e.g. rate-limit handling) applies to
    every slot at once instead of needing to be copied per function."""
    if not should_run:
        logger.info(f"Scheduler [{label}]: {skip_reason}, skipping")
        return
    if not settings.schedule_report_channel_id:
        logger.warning(f"Scheduler [{label}]: no channel configured, skipping")
        return

    channel = _bot.get_channel(int(settings.schedule_report_channel_id))
    if channel is None:
        logger.error(f"Scheduler [{label}]: channel {settings.schedule_report_channel_id} not found")
        return

    logger.info(f"Scheduler: running {label} report")
    try:
        result = await generate()
        report = result.get("final_report", "") or f"⚠️ 無法生成{label}報告，請稍後再試。"
    except Exception as exc:
        logger.error(f"Scheduler [{label}] error: {exc}", exc_info=True)
        report = f"⚠️ {label}報告錯誤：{exc}"

    for chunk in chunk_message(report):
        await channel.send(chunk)

    logger.info(f"Scheduler: {label} report sent to channel {settings.schedule_report_channel_id}")


async def _send_scheduled_report(slot: str) -> None:
    is_weekday = datetime.datetime.now(_TZ).weekday() < 5

    async def generate():
        return await run_agent(
            user_message=SLOT_PROMPTS[slot],
            user_id=settings.schedule_user_id,
            channel_id=settings.schedule_report_channel_id,
        )

    await _dispatch_report(slot, is_weekday, "weekend, market closed", generate)


async def _send_weekend_digest() -> None:
    is_sunday = datetime.datetime.now(_TZ).weekday() == 6
    await _dispatch_report("weekend_digest", is_sunday, "not Sunday", run_weekend_digest)


@tasks.loop(time=datetime.time(8, 30, tzinfo=_TZ))
async def pre_market_report():
    await _send_scheduled_report("pre_market")


@tasks.loop(time=datetime.time(12, 0, tzinfo=_TZ))
async def mid_session_report():
    await _send_scheduled_report("mid_session")


@tasks.loop(time=datetime.time(14, 30, tzinfo=_TZ))
async def post_market_report():
    await _send_scheduled_report("post_market")


@tasks.loop(time=datetime.time(20, 0, tzinfo=_TZ))
async def weekend_digest_report():
    await _send_weekend_digest()


def start_scheduled_tasks(bot_instance) -> None:
    global _bot
    _bot = bot_instance

    if not settings.schedule_enabled:
        logger.info("Scheduler disabled (SCHEDULE_ENABLED=false), skipping")
        return
    if not settings.schedule_report_channel_id:
        logger.info("Scheduler: SCHEDULE_REPORT_CHANNEL_ID not set, skipping")
        return

    pre_market_report.start()
    mid_session_report.start()
    post_market_report.start()
    weekend_digest_report.start()
    logger.info("Scheduled tasks started: 08:30 / 12:00 / 14:30 TST (平日) + 20:00 週日假日摘要")
