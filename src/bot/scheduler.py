"""Scheduled market reports — 盤前 / 盤中 / 收盤後.

Uses discord.ext.tasks (bundled with discord.py) to fire at fixed wall-clock
times in Asia/Taipei timezone. No extra dependencies required.

Times:
  08:30 — pre_market  (盤前)
  12:00 — mid_session (盤中)
  14:30 — post_market (收盤後)
"""

from __future__ import annotations
import datetime
import zoneinfo

from discord.ext import tasks
from loguru import logger

from src.config import settings
from src.agents.graph import run_agent
from src.bot.discord_bot import chunk_message

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


async def _send_scheduled_report(slot: str) -> None:
    if not settings.schedule_report_channel_id:
        logger.warning(f"Scheduler [{slot}]: no channel configured, skipping")
        return

    channel = _bot.get_channel(int(settings.schedule_report_channel_id))
    if channel is None:
        logger.error(f"Scheduler [{slot}]: channel {settings.schedule_report_channel_id} not found")
        return

    logger.info(f"Scheduler: running {slot} report")
    try:
        result = await run_agent(
            user_message=SLOT_PROMPTS[slot],
            user_id=settings.schedule_user_id,
            channel_id=settings.schedule_report_channel_id,
        )
        report = (
            result.get("final_report", "")
            if isinstance(result, dict)
            else getattr(result, "final_report", "")
        )
        if not report:
            report = "⚠️ 無法生成排程報告，請稍後再試。"
    except Exception as exc:
        logger.error(f"Scheduler [{slot}] agent error: {exc}", exc_info=True)
        report = f"⚠️ 排程報告錯誤：{exc}"

    for chunk in chunk_message(report):
        await channel.send(chunk)

    logger.info(f"Scheduler: {slot} report sent to channel {settings.schedule_report_channel_id}")


@tasks.loop(time=datetime.time(8, 30, tzinfo=_TZ))
async def pre_market_report():
    await _send_scheduled_report("pre_market")


@tasks.loop(time=datetime.time(12, 0, tzinfo=_TZ))
async def mid_session_report():
    await _send_scheduled_report("mid_session")


@tasks.loop(time=datetime.time(14, 30, tzinfo=_TZ))
async def post_market_report():
    await _send_scheduled_report("post_market")


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
    logger.info("Scheduled tasks started: 08:30 / 12:00 / 14:30 TST")
