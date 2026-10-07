from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint, event, func, select, text, update)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, mapped_column

from .models import RawMessage, TokenInfo, utcnow


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_name: Mapped[str] = mapped_column(String(200))
    message_id: Mapped[int] = mapped_column(BigInteger)
    msg_ts: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    latency_s: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(40))
    is_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float)
    mints: Mapped[list] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (UniqueConstraint("channel_id", "message_id", "text_hash"),)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_pk: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    msg_ts: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    mint: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(40))
    side: Mapped[str | None] = mapped_column(String(8))
    entry_price: Mapped[float | None] = mapped_column(Float)  # as stated by channel (unit unknown)
    stop_price: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[list | None] = mapped_column(JSON)
    parse_status: Mapped[str] = mapped_column(String(32))
    llm_raw: Mapped[str | None] = mapped_column(Text)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer)


class DecisionRow(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(64))
    size_sol: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    entry_price: Mapped[float] = mapped_column(Float)  # SOL per token, fill price
    entry_liquidity_usd: Mapped[float] = mapped_column(Float, default=0.0)
    size_sol: Mapped[float] = mapped_column(Float)  # gross SOL spent on the buy
    token_amount: Mapped[float] = mapped_column(Float)  # remaining UI amount
    initial_tokens: Mapped[float] = mapped_column(Float, default=0.0)
    proceeds_sol: Mapped[float] = mapped_column(Float, default=0.0)
    fees_sol: Mapped[float] = mapped_column(Float, default=0.0)
    peak_price: Mapped[float] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float)
    scaled_out: Mapped[bool] = mapped_column(Boolean, default=False)
    price_misses: Mapped[int] = mapped_column(Integer, default=0)
    sell_failures: Mapped[int] = mapped_column(Integer, default=0)
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    realized_pnl_sol: Mapped[float | None] = mapped_column(Float)
    pnl_pct: Mapped[float | None] = mapped_column(Float)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    mode: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str | None] = mapped_column(String(32))
    sol_amount: Mapped[float] = mapped_column(Float, default=0.0)
    token_amount: Mapped[float] = mapped_column(Float, default=0.0)
    price_native: Mapped[float] = mapped_column(Float, default=0.0)
    fee_sol: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_bps: Mapped[float] = mapped_column(Float, default=0.0)
    tx_sig: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Snapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    mint: Mapped[str] = mapped_column(String(64))
    offset_s: Mapped[int] = mapped_column(Integer)
    price_native: Mapped[float | None] = mapped_column(Float)  # NULL = token gone / no pair
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    txns_m5: Mapped[int | None] = mapped_column(Integer)
    volume_m5: Mapped[float | None] = mapped_column(Float)
    txns_h1: Mapped[int | None] = mapped_column(Integer)
    volume_h1: Mapped[float | None] = mapped_column(Float)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("signal_id", "offset_s"),)


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine.sync_engine, "connect")
            def _pragmas(dbapi_conn, _):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
        self.sf = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._migrate(conn)

    async def _migrate(self, conn) -> None:
        """Idempotent ALTER TABLE for DBs created before snapshot columns existed."""
        if not str(self.engine.url).startswith("sqlite"):
            return
        cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(price_snapshots)"))).all()}
        adds = {
            "txns_m5": "ALTER TABLE price_snapshots ADD COLUMN txns_m5 INTEGER",
            "volume_m5": "ALTER TABLE price_snapshots ADD COLUMN volume_m5 FLOAT",
            "txns_h1": "ALTER TABLE price_snapshots ADD COLUMN txns_h1 INTEGER",
            "volume_h1": "ALTER TABLE price_snapshots ADD COLUMN volume_h1 FLOAT",
        }
        for name, ddl in adds.items():
            if name not in cols:
                await conn.execute(text(ddl))

    async def close(self) -> None:
        await self.engine.dispose()


class Repo:
    def __init__(self, db: Database):
        self.db = db
        self.sf = db.sf

    # ---- ingestion
    async def add_message(self, raw: RawMessage, score: float, mints: list[str], passed: bool) -> int | None:
        row = Message(
            channel_id=raw.channel_id, channel_name=raw.channel_name, message_id=raw.message_id,
            msg_ts=raw.ts, received_at=raw.received_at,
            latency_s=(raw.received_at - raw.ts).total_seconds(),
            text=raw.text, text_hash=hashlib.sha1(raw.text.encode()).hexdigest(),
            is_edit=raw.is_edit, score=score, mints=mints, passed=passed,
        )
        async with self.sf() as s:
            s.add(row)
            try:
                await s.commit()
            except IntegrityError:  # same channel/message/text already stored
                await s.rollback()
                return None
            return row.id

    async def add_signal(self, message_pk: int, raw: RawMessage, *, mint: str | None, symbol: str | None,
                         side: str | None, entry: float | None, stop: float | None, targets: list[float] | None,
                         status: str, llm_raw: str | None, latency_ms: int | None) -> int:
        row = Signal(message_pk=message_pk, channel_id=raw.channel_id, msg_ts=raw.ts, mint=mint, symbol=symbol,
                     side=side, entry_price=entry, stop_price=stop, targets=targets, parse_status=status,
                     llm_raw=llm_raw, llm_latency_ms=latency_ms)
        async with self.sf() as s:
            s.add(row)
            await s.commit()
            return row.id

    async def update_signal_mint(self, signal_id: int, mint: str, symbol: str | None) -> None:
        async with self.sf() as s:
            await s.execute(update(Signal).where(Signal.id == signal_id).values(mint=mint, symbol=symbol))
            await s.commit()

    async def add_decision(self, signal_id: int, accepted: bool, reason: str, size_sol: float = 0.0,
                           details: dict | None = None) -> None:
        async with self.sf() as s:
            s.add(DecisionRow(signal_id=signal_id, accepted=accepted, reason=reason, size_sol=size_sol,
                              details=details))
            await s.commit()

    async def add_snapshot(self, signal_id: int, mint: str, offset_s: int, info: TokenInfo | None) -> None:
        async with self.sf() as s:
            s.add(Snapshot(signal_id=signal_id, mint=mint, offset_s=offset_s,
                           price_native=info.price_native if info else None,
                           liquidity_usd=info.liquidity_usd if info else None,
                           txns_m5=(info.buys_m5 + info.sells_m5) if info else None,
                           volume_m5=info.volume_m5 if info else None,
                           txns_h1=(info.buys_h1 + info.sells_h1) if info else None,
                           volume_h1=info.volume_h1 if info else None))
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()

    async def signals_needing_snapshot(self, offset_s: int, now: datetime, grace_s: int = 120) -> list[Signal]:
        s0, sx = aliased(Snapshot), aliased(Snapshot)
        q = (select(Signal)
             .join(s0, (s0.signal_id == Signal.id) & (s0.offset_s == 0))
             .outerjoin(sx, (sx.signal_id == Signal.id) & (sx.offset_s == offset_s))
             .where(sx.id.is_(None), Signal.mint.is_not(None),
                    Signal.created_at <= now - timedelta(seconds=offset_s),
                    Signal.created_at >= now - timedelta(seconds=offset_s + grace_s)))
        async with self.sf() as s:
            return list((await s.scalars(q)).all())

    # ---- risk queries
    async def open_positions(self) -> list[Position]:
        async with self.sf() as s:
            return list((await s.scalars(select(Position).where(Position.status == "open"))).all())

    async def count_open(self) -> int:
        async with self.sf() as s:
            return (await s.scalar(select(func.count()).select_from(Position).where(Position.status == "open"))) or 0

    async def open_exposure_sol(self) -> float:
        # remaining cost basis of open positions
        async with self.sf() as s:
            rows = (await s.scalars(select(Position).where(Position.status == "open"))).all()
        return sum(p.size_sol * (p.token_amount / p.initial_tokens if p.initial_tokens else 1.0) for p in rows)

    async def has_open_position(self, mint: str) -> bool:
        async with self.sf() as s:
            return (await s.scalar(select(func.count()).select_from(Position)
                                   .where(Position.mint == mint, Position.status == "open"))) > 0

    async def last_closed_at(self, mint: str) -> datetime | None:
        async with self.sf() as s:
            return await s.scalar(select(func.max(Position.closed_at)).where(Position.mint == mint))

    async def realized_pnl_since(self, since: datetime) -> float:
        async with self.sf() as s:
            return (await s.scalar(select(func.coalesce(func.sum(Position.realized_pnl_sol), 0.0))
                                   .where(Position.status == "closed", Position.closed_at >= since))) or 0.0

    async def unrealized_pnl_sol(self, prices_by_mint: dict[str, float]) -> float:
        """Unrealized PnL across open positions: remaining tokens * price - remaining cost basis - fees paid so far.
        Price falls back to the stored last_price, then to entry price (never stale-null)."""
        total = 0.0
        async with self.sf() as s:
            rows = list((await s.scalars(select(Position).where(Position.status == "open"))).all())
        for p in rows:
            px = prices_by_mint.get(p.mint) or p.last_price or p.entry_price
            remaining_cost = p.size_sol * (p.token_amount / p.initial_tokens) if p.initial_tokens else p.size_sol
            total += p.token_amount * px - remaining_cost - p.fees_sol
        return total

    async def channels_calling(self, mint: str, since: datetime) -> int:
        async with self.sf() as s:
            return (await s.scalar(select(func.count(func.distinct(Signal.channel_id)))
                                   .where(Signal.mint == mint, Signal.side == "buy",
                                          Signal.created_at >= since))) or 0

    async def channel_perf(self, channel_id: int) -> tuple[int, float]:
        async with self.sf() as s:
            row = (await s.execute(select(func.count(), func.coalesce(func.avg(Position.pnl_pct), 0.0))
                                   .where(Position.channel_id == channel_id, Position.status == "closed"))).one()
        return int(row[0]), float(row[1])

    # ---- positions
    async def record_entry(self, *, signal_id: int, channel_id: int, info: TokenInfo, mode: str, fill: Any) -> int:
        async with self.sf() as s:
            pos = Position(signal_id=signal_id, channel_id=channel_id, mint=info.mint, symbol=info.symbol,
                           mode=mode, entry_price=fill.price_native, entry_liquidity_usd=info.liquidity_usd,
                           size_sol=fill.sol_amount, token_amount=fill.token_amount, initial_tokens=fill.token_amount,
                           fees_sol=fill.fee_sol,
                           peak_price=fill.price_native, last_price=fill.price_native)
            s.add(pos)
            await s.flush()
            s.add(Order(signal_id=signal_id, position_id=pos.id, mode=mode, side="buy", status="filled",
                        reason="entry", sol_amount=fill.sol_amount, token_amount=fill.token_amount,
                        price_native=fill.price_native, fee_sol=fill.fee_sol, slippage_bps=fill.slippage_bps,
                        tx_sig=fill.tx_sig))
            await s.commit()
            return pos.id

    async def add_failed_order(self, *, signal_id: int, position_id: int | None, mode: str, side: str,
                               reason: str, error: str) -> None:
        async with self.sf() as s:
            s.add(Order(signal_id=signal_id, position_id=position_id, mode=mode, side=side, status="failed",
                        reason=reason, error=error))
            await s.commit()

    async def update_mark(self, position_id: int, last_price: float | None, peak: float, misses: int) -> None:
        vals: dict[str, Any] = {"peak_price": peak, "price_misses": misses}
        if last_price is not None:
            vals["last_price"] = last_price
        async with self.sf() as s:
            await s.execute(update(Position).where(Position.id == position_id).values(**vals))
            await s.commit()

    async def bump_sell_failures(self, position_id: int) -> int:
        async with self.sf() as s:
            await s.execute(update(Position).where(Position.id == position_id)
                            .values(sell_failures=Position.sell_failures + 1))
            await s.commit()
            return (await s.scalar(select(Position.sell_failures).where(Position.id == position_id))) or 0

    async def apply_sell(self, pos: Position, fill: Any, reason: str, *, closed: bool, scaled: bool) -> Position:
        """Idempotent-ish: operates on the DB row, so a retry after a crash can't double-count a closed position."""
        async with self.sf() as s:
            row = await s.get(Position, pos.id)
            if row is None or row.status != "open":
                return row or pos
            row.token_amount = 0.0 if closed else max(row.token_amount - fill.token_amount, 0.0)
            row.proceeds_sol += fill.sol_amount
            row.fees_sol += fill.fee_sol
            row.scaled_out = row.scaled_out or scaled
            row.sell_failures = 0
            s.add(Order(signal_id=row.signal_id, position_id=row.id, mode=row.mode, side="sell",
                        status="filled", reason=reason, sol_amount=fill.sol_amount,
                        token_amount=fill.token_amount, price_native=fill.price_native, fee_sol=fill.fee_sol,
                        slippage_bps=fill.slippage_bps, tx_sig=fill.tx_sig))
            if closed:
                row.status = "closed"
                row.closed_at = utcnow()
                row.exit_reason = reason
                row.exit_price = fill.price_native
                row.realized_pnl_sol = row.proceeds_sol - row.size_sol - row.fees_sol
                row.pnl_pct = row.realized_pnl_sol / row.size_sol * 100 if row.size_sol else 0.0
            await s.commit()
            return row
