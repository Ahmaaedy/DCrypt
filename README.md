---
title: Dcrypt
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 6.28.0
app_file: app.py
pinned: false
---

# Dcrypt — signal executer (paste) + momentum trader (mt)

Two paper-first Solana memecoin signal pipelines behind one Gradio UI.

## Secrets (Space settings)
- `TG_API_ID`, `TG_API_HASH`, `TG_CHANNELS`, `TG_SESSION_STRING`
- `RPC_URL` (required for Dcrypt-mt fail-closed on-chain checks)
- `DRY_RUN=false` to actually run the bots; default is UI-only
- `NOTIFY_BOT_TOKEN`, `NOTIFY_CHAT_ID` (optional)
- `DB_URL_PASTE`, `DB_URL_MT` (optional; defaults `sqlite+aiosqlite:///dcrypt_{paste,mt}.db`)

## Notes
- Free Spaces have ephemeral disks: `*.db` is wiped on restart.
- Paper trading only; live mode stays local.
- `dcrypt_paste/` and `dcrypt_mt/` are independent subprojects with their own tests.
