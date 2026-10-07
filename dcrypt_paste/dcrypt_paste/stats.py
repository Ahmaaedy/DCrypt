"""Per-channel report: does any channel actually have an edge net of costs?"""
from __future__ import annotations

import statistics

from sqlalchemy import text

from .db import Database

TRADES = text("""
SELECT channel_id, COUNT(*) n, SUM(CASE WHEN realized_pnl_sol > 0 THEN 1 ELSE 0 END) wins,
       AVG(pnl_pct) avg_pct, SUM(realized_pnl_sol) total_sol,
       SUM(CASE WHEN exit_reason IN ('liquidity_drop','no_price_writeoff') THEN 1 ELSE 0 END) rugs
FROM positions WHERE status = 'closed' GROUP BY channel_id""")

SIGNALS = text("""
SELECT channel_id, COUNT(*) n, SUM(CASE WHEN d.accepted THEN 1 ELSE 0 END) accepted
FROM signals sg LEFT JOIN decisions d ON d.signal_id = sg.id
WHERE sg.parse_status = 'ok' GROUP BY channel_id""")

MOVES = text("""
SELECT sg.channel_id, sn.offset_s, COUNT(*) n,
  AVG(CASE WHEN sn.price_native IS NULL THEN -100.0 ELSE (sn.price_native / s0.price_native - 1) * 100 END) avg_move,
  AVG(CASE WHEN sn.price_native IS NOT NULL AND sn.price_native > s0.price_native THEN 1.0 ELSE 0.0 END) pct_up
FROM signals sg
JOIN price_snapshots s0 ON s0.signal_id = sg.id AND s0.offset_s = 0 AND s0.price_native > 0
JOIN price_snapshots sn ON sn.signal_id = sg.id AND sn.offset_s > 0
GROUP BY sg.channel_id, sn.offset_s ORDER BY sg.channel_id, sn.offset_s""")

NAMES = text("SELECT channel_id, MAX(channel_name) FROM messages GROUP BY channel_id")
LAT = text("SELECT channel_id, latency_s FROM messages WHERE latency_s IS NOT NULL")


async def report(db: Database) -> str:
    async with db.engine.connect() as c:
        names = {r[0]: r[1] for r in (await c.execute(NAMES)).all()}
        sigs = {r[0]: r for r in (await c.execute(SIGNALS)).all()}
        trades = {r[0]: r for r in (await c.execute(TRADES)).all()}
        moves = (await c.execute(MOVES)).all()
        lat: dict[int, list[float]] = {}
        for cid, v in (await c.execute(LAT)).all():
            lat.setdefault(cid, []).append(v)

    out = ["== Execution (closed positions) =="]
    out.append(f"{'channel':28} {'signals':>7} {'taken':>5} {'closed':>6} {'win%':>5} {'avg%':>7} {'SOL':>8} "
               f"{'rugs':>4} {'med.lat(s)':>10}")
    for cid in sorted(set(sigs) | set(trades)):
        sg, tr = sigs.get(cid), trades.get(cid)
        med = statistics.median(lat[cid]) if lat.get(cid) else float("nan")
        n = tr[1] if tr else 0
        out.append(f"{names.get(cid, str(cid))[:28]:28} {sg[1] if sg else 0:>7} {int(sg[2] or 0) if sg else 0:>5} "
                   f"{n:>6} {(tr[2] / n * 100 if n else 0):>5.0f} {(tr[3] or 0 if tr else 0):>7.1f} "
                   f"{(tr[4] or 0 if tr else 0):>8.4f} {int(tr[5] or 0) if tr else 0:>4} {med:>10.1f}")

    out += ["", "== Move after the call, ALL signals incl. untraded (missing pair counted as -100%) =="]
    out.append(f"{'channel':28} {'after':>6} {'n':>5} {'avg move%':>10} {'%up':>5}")
    for cid, off, n, avg, up in moves:
        out.append(f"{names.get(cid, str(cid))[:28]:28} {off // 60:>5}m {n:>5} {avg:>10.1f} {up * 100:>5.0f}")
    return "\n".join(out)
