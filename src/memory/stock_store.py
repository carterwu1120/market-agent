"""Persistence layer for daily stock snapshots (price/chip/fundamental)."""

from datetime import date, datetime, timezone
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.database import AsyncSessionFactory
from src.memory.models import StockDailyChip, StockDailyFundamental, StockDailyPrice


def _today_tw() -> date:
    from datetime import timedelta
    return datetime.now(timezone(timedelta(hours=8))).date()


async def upsert_daily_price(technical_data: list[dict]) -> None:
    """Upsert technical indicator rows from technical_agent output."""
    if not technical_data:
        return
    today = _today_tw()
    rows = []
    for item in technical_data:
        sym = item.get("symbol", "")
        price = item.get("price", {})
        ind = item.get("indicators", {})
        if not sym:
            continue

        # Resolve the date from the data or fall back to today
        data_date = today
        if price.get("fetched_at"):
            try:
                data_date = datetime.fromisoformat(price["fetched_at"]).date()
            except Exception:
                pass

        rows.append({
            "symbol": sym,
            "company_name": price.get("company_name") or "",
            "date": data_date,
            "close": price.get("last_price"),
            "change_pct": price.get("change_pct"),
            "volume": price.get("volume"),
            "sma_20": ind.get("sma_20"),
            "sma_60": ind.get("sma_60"),
            "rsi_14": ind.get("rsi_14"),
            "macd": ind.get("macd"),
            "macd_signal": ind.get("macd_signal"),
            "bb_upper": ind.get("bb_upper"),
            "bb_lower": ind.get("bb_lower"),
            "bias_20": ind.get("bias_20"),
            "bias_60": ind.get("bias_60"),
        })

    if not rows:
        return
    try:
        async with AsyncSessionFactory() as session:
            stmt = pg_insert(StockDailyPrice).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_stock_daily_price_symbol_date",
                set_={c: stmt.excluded[c] for c in [
                    "company_name", "close", "change_pct", "volume",
                    "sma_20", "sma_60", "rsi_14", "macd", "macd_signal",
                    "bb_upper", "bb_lower", "bias_20", "bias_60", "fetched_at",
                ]},
            )
            await session.execute(stmt)
            await session.commit()
        logger.info(f"StockStore: upserted {len(rows)} price rows")
    except Exception as exc:
        logger.warning(f"StockStore: price upsert failed: {exc}")


async def upsert_daily_chip(chip_data: list[dict]) -> None:
    """Upsert chip rows from chip_agent output."""
    if not chip_data:
        return
    today = _today_tw()
    rows = []
    for item in chip_data:
        sym = item.get("symbol", "")
        inst = item.get("institutional", {})
        margin = item.get("margin", {})
        if not sym or inst.get("error"):
            continue

        data_date = today
        if inst.get("date"):
            try:
                data_date = date.fromisoformat(inst["date"])
            except Exception:
                pass

        rows.append({
            "symbol": sym,
            "date": data_date,
            "foreign_net": inst.get("foreign_net"),
            "trust_net": inst.get("trust_net"),
            "dealer_net": inst.get("dealer_net"),
            "total_3_institutions": inst.get("total_3_institutions"),
            "margin_buy_balance": margin.get("margin_buy_balance") if not margin.get("error") else None,
            "short_sell_balance": margin.get("short_sell_balance") if not margin.get("error") else None,
        })

    if not rows:
        return
    try:
        async with AsyncSessionFactory() as session:
            stmt = pg_insert(StockDailyChip).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_stock_daily_chip_symbol_date",
                set_={c: stmt.excluded[c] for c in [
                    "foreign_net", "trust_net", "dealer_net",
                    "total_3_institutions", "margin_buy_balance",
                    "short_sell_balance", "fetched_at",
                ]},
            )
            await session.execute(stmt)
            await session.commit()
        logger.info(f"StockStore: upserted {len(rows)} chip rows")
    except Exception as exc:
        logger.warning(f"StockStore: chip upsert failed: {exc}")


async def upsert_daily_fundamental(fundamental_data: list[dict]) -> None:
    """Upsert fundamental rows from fundamental_agent output."""
    if not fundamental_data:
        return
    today = _today_tw()
    rows = []
    for item in fundamental_data:
        sym = item.get("symbol", "")
        if not sym or item.get("error"):
            continue
        rows.append({
            "symbol": sym,
            "company_name": item.get("company_name") or "",
            "date": today,
            "pe_ratio": item.get("pe_ratio"),
            "pb_ratio": item.get("pb_ratio"),
            "eps_ttm": item.get("eps_ttm"),
            "roe": item.get("roe"),
            "gross_margin": item.get("gross_margin"),
            "revenue_growth": item.get("revenue_growth"),
            "analyst_target": item.get("analyst_target"),
            "analyst_recommendation": item.get("analyst_recommendation"),
        })

    if not rows:
        return
    try:
        async with AsyncSessionFactory() as session:
            stmt = pg_insert(StockDailyFundamental).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_stock_daily_fundamental_symbol_date",
                set_={c: stmt.excluded[c] for c in [
                    "company_name", "pe_ratio", "pb_ratio", "eps_ttm", "roe",
                    "gross_margin", "revenue_growth", "analyst_target",
                    "analyst_recommendation", "fetched_at",
                ]},
            )
            await session.execute(stmt)
            await session.commit()
        logger.info(f"StockStore: upserted {len(rows)} fundamental rows")
    except Exception as exc:
        logger.warning(f"StockStore: fundamental upsert failed: {exc}")


async def query_stock_history(
    symbol: str,
    days: int = 7,
) -> dict:
    """Query historical data for a symbol across all three tables."""
    from datetime import timedelta
    cutoff = _today_tw() - timedelta(days=days)

    async with AsyncSessionFactory() as session:
        price_rows = (await session.execute(
            select(StockDailyPrice)
            .where(StockDailyPrice.symbol == symbol, StockDailyPrice.date >= cutoff)
            .order_by(StockDailyPrice.date.desc())
        )).scalars().all()

        chip_rows = (await session.execute(
            select(StockDailyChip)
            .where(StockDailyChip.symbol == symbol, StockDailyChip.date >= cutoff)
            .order_by(StockDailyChip.date.desc())
        )).scalars().all()

        fund_rows = (await session.execute(
            select(StockDailyFundamental)
            .where(StockDailyFundamental.symbol == symbol, StockDailyFundamental.date >= cutoff)
            .order_by(StockDailyFundamental.date.desc())
        )).scalars().all()

    return {
        "symbol": symbol,
        "price_history": [
            {"date": str(r.date), "close": r.close, "change_pct": r.change_pct,
             "sma_20": r.sma_20, "sma_60": r.sma_60, "rsi_14": r.rsi_14}
            for r in price_rows
        ],
        "chip_history": [
            {"date": str(r.date), "foreign_net": r.foreign_net,
             "trust_net": r.trust_net, "dealer_net": r.dealer_net,
             "total_3_institutions": r.total_3_institutions}
            for r in chip_rows
        ],
        "fundamental_history": [
            {"date": str(r.date), "pe_ratio": r.pe_ratio, "pb_ratio": r.pb_ratio,
             "eps_ttm": r.eps_ttm, "roe": r.roe, "gross_margin": r.gross_margin}
            for r in fund_rows
        ],
    }
