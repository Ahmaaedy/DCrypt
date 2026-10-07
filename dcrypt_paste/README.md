# dcrypt — Telegram signal → memecoin trade pipeline (paper-first)

```
Telethon (1 client, N channels)
  → pre-filter (mint regex + score)        filter.py
  → Ollama normalizer (JSON schema)        normalizer.py   (LLM never sees/emits addresses)
  → resolve token + price snapshot @0      prices.py (DexScreener)
  → validator: global risk, dedupe, locks  validator.py
  → executor: paper | live (Jupiter)       executors/
  → position manager (exits, PnL)          positions.py
  → SQLite, everything tagged by channel   db.py
  + snapshot worker: price at +1m/5m/15m/1h for EVERY signal, traded or not
```

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env        # fill TG_API_ID / TG_API_HASH / TG_CHANNELS
ollama pull llama3.1:8b     # or any instruct model; set OLLAMA_MODEL
python -m dcrypt_paste dialogs   # prints channel ids you have joined
python -m dcrypt_paste run       # first run asks for phone + login code
python -m dcrypt_paste stats     # per-channel report
pytest                      # 17 offline tests
```
Use a **dedicated Telegram account**. Create the file `KILL` in the working dir to halt new entries
(exits keep running). Remove it to resume.

## Recommended path
1. **Run in paper mode for days/weeks** (default). Don't trade anything yet; read `stats`.
2. Look at the second table in `stats` (move after the call for *all* signals, including ones we rejected):
   it shows whether a channel's calls go up at all after you could realistically act on them.
3. Tune filters/exits from data. Block channels with no edge (auto-block exists via `CHANNEL_MIN_*`).
4. Only then consider `MODE=live`, with the guards below.

## What's tested vs not
- **Tested offline (17 tests):** filter/mint extraction, Ollama normalizer (mocked), validator gates, global caps
  across channels, cross-channel dedupe under concurrency, kill switch, exits (hard stop, scale-out, breakeven,
  liquidity drop, no-price write-off), snapshot recording incl. dead tokens, stats SQL.
- **NOT tested here (no network access in the build sandbox):** real Telegram, real Ollama, live DexScreener
  responses, Jupiter, mainnet RPC. DexScreener/Jupiter endpoints were checked against their docs
  (`/tokens/v1/solana/{addrs}`, `/latest/dex/pairs/solana/{pair}`, `{base}/quote`, `{base}/swap`), but field
  parsing is unverified against live payloads. Expect small fixes on first run.
- **`executors/live.py` has never touched mainnet.** Treat it as a draft.

## Live-mode guards
`MODE=live` refuses to start unless `LIVE_CONFIRM=I_UNDERSTAND_THE_RISK`, `WALLET_SECRET_KEY` and `RPC_URL`
are set, and `POSITION_SIZE_SOL <= MAX_LIVE_POSITION_SOL` (default 0.05). Use a fresh wallet funded with an
amount you can afford to lose entirely. Do one buy+sell by hand-sized test first.

## Known gaps / next steps
- No holder-concentration or LP-lock checks (needs `getTokenLargestAccounts`). Mint/freeze authority and
  dangerous Token-2022 extensions *are* checked when `RPC_URL` is set.
- A Jupiter quote proves routing, not sellability. Honeypots can still pass.
- No Jito/bundle submission or MEV protection; sends via plain RPC with priority fee.
- Paper fills use a pessimistic model (slippage + constant-product impact + exit penalty + fees) but real
  fills on thin memecoin pools can be worse. Treat paper PnL as an upper bound.
- Channel-stated entry/stop/targets are stored but not used; exits are the bot's own.
- Devnet stage was dropped: there's no meaningful memecoin liquidity or Jupiter on devnet. Paper → tiny live.
- Single process (asyncio). Move to Postgres + separate processes only if you outgrow it.

Nothing here guarantees profit. Most memecoin signal channels lose money for followers.
