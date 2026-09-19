# Memecoin Bots — Deep Research Report (2026)

## 1. What Are Memecoin Bots?

Memecoin bots are **automated trading software** that buy and sell low-cap, highly volatile tokens (primarily on Solana) based on predefined rules — new pool detection, wallet-copy signals, price targets, or AI-scored probability — without manual order entry. They are built for one job: **get in and out of volatile, low-liquidity tokens within minutes or hours**.

Unlike generic Solana trading bots that might DCA into SOL, memecoin bots are purpose-built for one thing: catching the pump before humans can react. The category spans Telegram snipers, browser terminals, AI scanners, and copy-trading bots that mirror profitable wallets.

---

## 2. How They Work — The Core Pipeline

Every memecoin bot follows this flow:

```
Detect → Filter → Execute → Exit
```

### Detection Layer (latency is everything)

| Method | Speed | Description |
|--------|-------|-------------|
| RPC Polling | Slow (seconds) | `getProgramAccounts` polling — too slow for live sniping |
| WebSocket subscriptions | Medium | Push notifications after block processing |
| **Geyser gRPC (Yellowstone)** | **Fast (sub-second)** | Stream from the node as it processes — minimum for serious bots |
| **Shreds** | **Fastest** | Raw block fragments before any RPC processes them |

> The real stack in 2026: Shredstream-fed stream for the hot path, gRPC stream as structured data, WebSockets as fallback.

### Filtering (rejecting ~95% of tokens)

- Mint authority renounced or burned — otherwise deployer can mint at will
- Freeze authority renounced — otherwise wallet can be frozen
- Holder distribution not concentrated — top-10 holders owning 90% = dump risk
- Liquidity pool not concentrated in deployer wallets — deployer holding 50%+ LP = rug
- Honeypot detection — some bots fire a tiny test sell before opening a real position
- Bonding curve analysis (pump.fun) — is the curve volume genuine or manipulated?
- Social signal checks — Twitter rug/scam keyword search

### Execution

- Swap via **Jupiter** (established tokens) or **direct AMM calls** (new pools Jupiter hasn't indexed)
- **Jito bundles** with SOL tips for guaranteed slot ordering
- Priority fees on Solana are cheap (fractions of a cent) but Jito tips on hot launches hit 0.01–0.1 SOL
- **Anti-MEV routing is non-negotiable** — every memecoin transaction is a sandwich target

### Exit Strategies

- Take-profit ladders (sell 30% at 2x, 30% at 5x)
- Trailing stops (6-level graduated schedules: 20% stop at 0% gain → 4% stop at 500%+ gain)
- Time-based exits (force-close after 20 min if no +15%)
- Dev-sell triggers
- Re-entry engine (triggers on 25-35% dip from partial-exit + volume spike + low scam probability)

---

## 3. Bot Categories (4 Types in 2026)

| Category | Examples | Fee | Custody | Best For |
|----------|----------|-----|---------|----------|
| **Telegram Snipers** | Trojan, BonkBot, Maestro, Banana Gun | 0.5–1% per trade | **Custodial** (key in bot) | Fastest setup (30 sec) |
| **Browser Terminals** | Axiom, BullX, Photon, GMGN | 0.75–1% per trade | Non-custodial | Manual trading + charts |
| **Standalone Snipers** | Open-source GitHub projects, self-hosted | Varies | Self-hosted | Custom edge, full control |
| **Copy Trading Bots** | uwuu.ai, GMGN (copy mode) | Performance-based or 1% | Non-custodial | Mirroring proven wallets |

### Key Comparison: GMGN vs Trojan (Top 2 Platforms)

| Metric | GMGN | Trojan |
|--------|------|--------|
| Fee | 1% flat | 0.9% (0.81% with referral) |
| Cashback | None | Up to 45% (Arena program) |
| Copy Trading | Best-in-class wallet analytics | Limited |
| Execution Speed | Good | Sub-2s (BOLT) |
| Chains | Multi-chain (SOL, BSC, Base, ETH, TRON, Monad...) | Solana only |
| Interface | Web + Telegram | Telegram-first |
| Lifetime Volume | $112.66M (24h leaderboard) | $4.74M |

### Other Notable Platforms

| Bot | Type | Fee | Custody | Copy Trading |
|-----|------|-----|---------|--------------|
| Axiom | Web terminal | 0.75-0.95% | Non-custodial | No |
| BullX | Web terminal | 1% + 1% pump.fun fee | Non-custodial | Limited |
| Photon | Web terminal | 1% | Non-custodial | No |
| BonkBot | Telegram | 1% | Custodial | No |
| Banana Gun | Telegram | 0.5-1% | Custodial | No |
| uwuu.ai | Copy trading | Performance-based | Non-custodial | Yes (core) |

---

## 4. Technical Architecture (Production Bots)

### Solana Sniper Bot Stack

```
┌─────────────────────────────────────────┐
│  Data Layer                              │
│  Yellowstone gRPC / Shredstream feeds    │
│  Helius, QuickNode, Triton providers     │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│  Hot Path (single-threaded event loop)   │
│  Decode → Filter → Size → Build → Sign   │
│  No allocations, no network requests     │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│  Execution Layer                         │
│  Jito bundles / Nozomi / ZeroSlot        │
│  Stake-weighted QoS / Region-aware       │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│  Post-Trade (separate process)           │
│  Position monitoring, exits, logging     │
└─────────────────────────────────────────┘
```

### Architecture Principles

1. **Nothing on the hot path waits for a network request** — all data pre-cached
2. **Parse in memory, never fetch** — decode instruction data from the transaction itself
3. **Pre-calculated constants** — max SOL per trade, max slippage, max pool share set before events
4. **Region-aware submission** — submit from region closest to scheduled leader
5. **Retry strategy** — send to current + next leader, then stop (no blind resending)

### Languages Used

- **Rust** — Most common for production snipers (zero-cost abstractions, memory safety)
- **Go** — Second most popular (good concurrency, fast compilation)
- **TypeScript/Node.js** — Common for Telegram bots and copy trading
- **Python** — Used in AI/ML-focused bots (Spectrolite)

### Key Infrastructure Providers

| Provider | Service | Latency |
|----------|---------|---------|
| Helius | RPC + gRPC + Sender API | Sub-second |
| QuickNode | RPC + Yellowstone gRPC | Sub-second |
| Triton | RPC + gRPC | Sub-second |
| Jito | MEV bundles + block engine | ~200ms |
| Nozomi | Fast confirmation | Alternative to Jito |
| ZeroSlot | Zero-slot inclusion | Maximum speed |

---

## 5. AI-Powered Memecoin Bots

### Spectrolite (Open Source — Python)

The most sophisticated open-source memecoin bot:

- **3-Model Ensemble**: BiLSTM-Transformer + XGBoost + IsolationForest
- **18 input features**: wash trade ratio, bonding curve velocity, insider wallet pre-position, RSI (5m/15m), token age, dev wallet sell %, top-10 holder concentration, cross-chain arb spread, telegram mention velocity, volume acceleration
- **CLIP Visual Analysis**: Zero-shot classification on token icons (rocket/moon, pepe, dog, celeb, fire, boring)
- **5-layer scam shield**: Static analysis → Dynamic simulation → Social scanning → MEV detection → LP monitoring
- **Redis signal bus** for pub/sub across workers
- **Kelly Criterion sizing** with quarter-Kelly for conservative positions (1% floor, 20% cap)
- **Gamification layer**: Adaptive personality modes (APE / SNIPER / ZEN / CASINO / SOBRIETY)
- **Persistent WebSocket** to pump.fun firehose with auto-reconnect and exponential backoff

### AI Agent Approach (TypeScript)

A production-quality memecoin trading agent in ~200 lines:

```
LLM Decision → Swap API Quote → Wallet Signature → Execution with Retry
```

- LLM picks what to trade (narrow schema, not execution)
- Swap API returns executable calldata (swapapi.dev — no API key required)
- **Slippage retry pattern**: 1% → 5% → 10% → 15% escalation
- ~95% success rate with retry pattern vs ~50% with fixed 1%
- Safety guardrails: max position size, token blacklist, wallet balance check

### Token Scoring (AI Sniper Bots)

| Factor | Weight | Description |
|--------|--------|-------------|
| Creator Age | 15% | Wallet history length |
| Mint Renounced | 20% | Can't mint more tokens |
| Social Presence | 10% | Twitter/Telegram links |
| Name Quality | 10% | Not suspicious patterns |
| Market Timing | 15% | Peak trading hours |
| Early Buyers | 15% | Initial buy pressure |
| Liquidity | 15% | Pool depth |

---

## 6. MEV & Sandwich Attacks

### How Sandwich Attacks Work

1. Bot sees pending DEX trade in public mempool
2. **Front-run**: Bot buys token before victim → pushes price up
3. **Victim trade**: Executes at worse price
4. **Back-run**: Bot sells after victim → captures profit

The whole sequence is submitted as an **atomic bundle** — either all three land in one block or none of it lands.

### JaredFromSubway (Notorious Example)

- **$40M+ extracted** via sandwich attacks on Ethereum
- Present in **60%+ of all Ethereum blocks** at peak
- Spent **522 ETH ($1M+) on gas in a single day**
- Uses **5-layer and 7-layer sandwich attacks** (multiple victims per bundle)
- Holds memecoin inventory (unique strategy — most bots avoid this risk)
- Tips builders **99.9% of expected profit** to win inclusion
- Also profitable from **token sniping** — buys within 10 minutes of launch

### Multi-Layer Sandwich Mechanics

**5-layer**: front-run → victim A → center swap → victim B → back-run
- Two pending victims sandwiched together
- Center swap pushes price further between them

**7-layer**: three victims, two center swaps, one front-run, one back-run
- Capital efficiency ~2x a traditional sandwich per gas unit

### Swap-JIT Combination

JaredFromSubway combines sandwiching with JIT liquidity:
1. Front-run swap moves pool price
2. Bot adds concentrated-liquidity position (via direct `UniswapV3Pool.mint()`)
3. Victim swap executes → bot earns LP fees
4. Bot removes liquidity + back-runs

**Three revenue sources**: price manipulation profit + LP fees + arbitrage on residual pool shift.

### Defenses Against MEV

| Defense | How It Works |
|---------|--------------|
| Flashbots Protect | Private RPC bypasses public mempool |
| CowSwap / 1inch Fusion | Intent-based batching, no mempool exposure |
| MEV-Share | Selective data reveal with rebate |
| Tight slippage | 0.1–0.5% leaves no room for sandwich profit |
| Smaller trades | Below threshold, gas cost exceeds sandwich profit |

---

## 7. The Brutal Profitability Reality

### Key Statistics

| Finding | Source |
|---------|--------|
| **43.4% of tokens that reach $250K market cap end as rugs** | SwapHunt (2,380 tokens, 5.5 months) |
| **Only 6.25% of Solana memecoin traders profitable over 90 days** | HTX/Dune Analytics (304,161 traders) |
| **97% of memecoins lose nearly all value** | Binance Research |
| **$2.8 billion lost to rug pulls in 2025** | Chainalysis |
| **78% transaction failure rate** on live Solana bot (96 trades) | AI Co-Founder Stack |
| **Best backtested strategy: +0.3% per trade** (statistically zero) | SwapHunt |
| **$1.26 billion in collective realized losses** across Solana memecoin traders | Dune Analytics |

### Pump.fun Stats

| Metric | Value |
|--------|-------|
| Tokens created | ~12 million |
| Graduation rate | **0.26%** (June 2026) |
| Cumulative revenue | $1.08 billion |
| Peak daily revenue | $4.4 million |
| Monthly active wallets (peak) | 5.2 million → 1.8M trough → 3.3M recovery |
| Profitable traders (April 2026) | 73.3% (up from 30.1% in June 2025) |

> The 73.3% profitability is largely due to **survivor selection** — unprofitable traders left, leaving a more experienced cohort. Of those profitable, 65% earned between $1–$500.

### Why Most Retail Bots Lose

1. **Slippage eats margins**: 8-12% round-trip in low-liquidity tiers
2. **Bimodal outcome distribution**: Tokens either rug (-95%) or moon (+100-5000%), with thin middle ground
3. **Latency disadvantage**: Retail bots on home machines compete against co-located infrastructure
4. **Fee accumulation**: 1% in + 1% out + Jito tips = need 2%+ move just to break even
5. **MEV/sandwich attacks**: Every memecoin trade is a sandwich target without protection
6. **Selection bias**: By the time a token is visible on DexScreener at $250K, it has already passed through algorithmic discovery, copy-trading bots, and first-wave traders

### Exit Timing Problem

- Median peak for tokens reaching +50%: **9.4 hours** after entry
- Median peak for tokens reaching +100%: **12.5 hours** after entry
- Only **15-24% peaked within the first 3 hours**
- Quick-flip strategies miss most actual runs; holding longer walks into the dump (24-48 hours)

---

## 8. Risks & Scams

### Risk Categories

| Risk | Prevalence |
|------|------------|
| Rug pulls | $2.8B in 2025 losses, ~$510K average per incident |
| Honeypots | Buy works, sell fails — position locked |
| Pump-and-dump schemes | 74,037 tokens flagged in 2024 (3.59% of all launches) |
| Telegram bot compromises | Keys stored on servers — multiple incidents |
| Copy-trading manipulation | Bots front-run copiers, fabricate sentiment |
| Market-Manipulation-as-a-Service | Third-party tools for non-technical scammers |

### Academic Research on Manipulation (arXiv 2026)

Five manipulation classes identified on pump.fun from 15 million coins:

1. **Wash trading** — Artificial volume to create illusion of demand
2. **Creator address obfuscation** — Hiding deployer identity across multiple wallets
3. **Coordinated sell** — Synchronized dumps by insider groups
4. **Copycat coins** — Cloning successful tokens to fragment liquidity
5. **Social media manipulation** — Fabricated hype via bots and paid promotion

> Strategic actors bypass the platform interface and implement these strategies in a highly automated and low-latency fashion, interacting directly with the blockchain.

### Copy-Trading Attack Surface

Manipulative bots exploit copy-trading:
- **Front-running copiers**: Bot buys before the target wallet, sells into copier-driven pump
- **Position concealment**: Hide true exposure while inflating prices
- **Wash trading + fabricated activity**: Create illusion of demand to attract copiers
- **Exit liquidity**: Use copiers as exit liquidity when dumping

### Security Best Practices

- **Never keep more in any bot wallet than you can afford to lose**
- Use a **dedicated hot wallet** segregated from main holdings
- Use a **CEX as a buffer**: Main wallet → CEX → trading wallet (breaks on-chain link)
- Prefer **non-custodial** tools (Axiom, Photon, GMGN, uwuu)
- **Burner wallets** — generate fresh wallets, never use main wallet
- **Auto-sweep** profits to cold wallet
- Rotate tokens/keys regularly

---

## 9. Open Source Bot Projects

| Project | Language | Focus | Key Feature |
|---------|----------|-------|-------------|
| **Spectrolite** | Python | AI-driven trading | 3-model ensemble, CLIP analysis, Redis signal bus |
| **sniper-bot-solana-grpc** | Rust | Pump.fun sniping | Yellowstone gRPC, Helius Sender, <150ms |
| **Solana-Sniper-Rust-Bot** | Rust | Multi-DEX sniping | Pumpfun/Pumpswap/Raydium, Jito+Nozomi+ZeroSlot |
| **PumpFun-Sniper-Bot** | Go | Pump.fun sniping | Multi-wallet, volume bot, REST API |
| **memecoin-trading-bot (Openwired)** | TypeScript | Strategy-first | Plug-in strategies, config-driven |
| **curve-pipeline-2672** | TypeScript | Meteora sniping | Jito bundles, multi-wallet parallel |
| **ai-memecoin-trading-bot** | Go | AI-powered | Multi-agent, honeypot detection, web dashboard |
| **Solana-Memecoin-Sniper-Suite** | JS/Rust | Full suite | Sniper, bundler, volume, copy-trader, arbitrage |

---

## 10. Production Bot Features Checklist

### Must-Have
- [ ] Real-time event detection (gRPC or Shreds)
- [ ] Hard safety filters (mint/freeze authority, holder distribution)
- [ ] Anti-MEV routing (Jito bundles)
- [ ] Position sizing based on pool liquidity (<5% of pool depth)
- [ ] Per-signature telemetry (know exactly why swaps fail)
- [ ] Sane retry logic (max 2 retries, refresh blockhash)

### Nice-to-Have
- [ ] Plug-in strategy system
- [ ] Multi-wallet orchestration
- [ ] Graduated trailing stops
- [ ] Kelly Criterion sizing
- [ ] CLIP visual analysis
- [ ] Social sentiment pipeline
- [ ] Re-entry engine
- [ ] LP liquidity watcher (post-entry)
- [ ] Adaptive priority fees

---

## 11. Key Takeaways

1. **Most retail memecoin bot users lose money** — the data is overwhelming (6% profitability over 90 days, 97% token failure rate)

2. **The edge lives in pre-graduation seconds** that public data doesn't show — not in rule-based entry filters

3. **Copy trading is gaining share** because it outsources token selection to wallets that have already proven an edge

4. **Infrastructure matters more than strategy** — latency, Jito bundles, gRPC streaming, region-aware submission

5. **Fees compound devastatingly** — 1% in/out + tips + slippage = 3-5% round trip cost

6. **AI-powered bots** (ensemble models, sentiment analysis) are the frontier but not a guaranteed edge

7. **The market is a negative-sum game** — Pump.fun takes $4.4M/day in fees, MEV extractors take more, leaving retail with scraps

8. **Survivor selection inflates profitability stats** — the 73.3% profitable figure comes from unprofitable traders leaving, not from improved odds

9. **95% of bot value is in rejection** — the most valuable thing a memecoin bot does is correctly reject tokens it shouldn't touch

10. **Treat this like gambling, not investing** — bring only money you can lose entirely

---

*Research compiled: September 18, 2026*
*Sources: SwapHunt, HTX Insights, Chainalysis, CoinGecko, Dune Analytics, arXiv, OpenChainBench, multiple GitHub repositories, platform documentation*
