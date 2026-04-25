# Claude Code — Master Agreement
# FLARE DEFI MODEL
# Last updated: 2026-04-23
# Inherits from: ../master-template/CLAUDE_master_template.md

> This file overrides or extends the master template where noted. All
> sections not explicitly overridden follow the master template verbatim.

---

## SECTION 1 — PERMISSION & AUTONOMY

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 2 — PROJECT SCOPE

```
  Name:          Flare DeFi Model
  Path:          C:\Users\david\OneDrive\Desktop\Cowork\flare-defi-model
  Repo:          github.com/davidduraesdd1-blip/flare-defi-model
  Deploy:        https://flare-defi-model-mw8toxbjk5baae9zjbfrli.streamlit.app/
  User role:     builder / designer / reviewer
  Collaborators: 1 (user)

  Purpose: DeFi portfolio construction and monitoring for Flare-ecosystem
  and multichain DeFi positions. Agent-driven execution via agentkit.
  Holds the cleanest architectural template in the portfolio — folder
  structure (pages/, ui/, agents/, scanners/, models/) is the reference
  pattern for sibling projects.

  Primary framing (Framing A): Flare-focused DeFi portfolio tool.
  Optional framing (Framing B): multichain DeFi across EVM + XRPL + Flare,
  behind a feature flag.

  Foundation codebases (READ ONLY — do not modify):
    - crypto-signal-app  → signal engine patterns
    - rwa-infinity-model → portfolio construction patterns (5-tier)
```

---

## SECTION 3 — COMMIT & PUSH RULES

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 4 — UNIFIED AUDIT & TEST PROTOCOL

[VERBATIM FROM MASTER TEMPLATE.]

Project-specific emphasis:
- `agents/` — agent-driven execution. Every agent has a dry-run flag that
  defaults True. Audit every agent path with dry-run on + dry-run off.
- `scanners/` — scheduled scanners. Each scanner has a `--now` flag for
  manual trigger. Audit idempotency: running a scanner twice in quick
  succession must not produce duplicate rows.
- `scheduler.py` — apscheduler-based. Verify no silent scheduler crashes
  (sentry-sdk configured).

---

## SECTION 5 — RESEARCH STANDARDS

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 6 — BRANDING & IDENTITY

[MASTER TEMPLATE with project defaults:]

`BRAND_NAME = "Flare DeFi Model"` (placeholder).
`BRAND_LOGO_PATH = None`.

Tone: crypto-native. More energetic than rwa-infinity-model, calmer than
crypto-signal-app. Audience is DeFi users who understand basis points
and liquidation risk.

---

## SECTION 7 — USER LEVEL SYSTEM

[MASTER TEMPLATE with project-specific tier definitions:]

  Beginner: DeFi-curious users. Plain-English explanations of yield,
            impermanent loss, and liquidation risk. Tooltips ubiquitous.
  Intermediate: active DeFi users. Condensed metrics. Full APY breakdowns.
  Advanced: DeFi power users. Raw TVL deltas, LTV history, liquidation
            proximity in real time.

---

## SECTION 8 — DESIGN STANDARDS

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 9 — MATH MODEL ARCHITECTURE

LAYER 1 — TECHNICAL: on-chain price action. FTSO feeds on Flare, DEX
  mid-prices for spot assets, AMM LP-token price decomposition.

LAYER 2 — MACRO / FUNDAMENTAL: BTC/ETH regime, total DeFi TVL, stablecoin
  supply growth, risk-free DeFi rate proxy.

LAYER 3 — SENTIMENT: Fear & Greed, DeFi pulse, governance-forum activity
  on major protocols, liquidation volume (proxy for leverage flush).

LAYER 4 — ON-CHAIN: protocol-specific TVL, utilization, debt/collateral
  ratios, liquidation distance, oracle health, yield decomposition
  (base yield vs. incentive-driven yield).

PORTFOLIO LAYER — adapted from rwa-infinity-model's portfolio.py, with
DeFi-specific constraints (min liquidity, max protocol concentration,
min audit score).

---

## SECTION 10 — DATA SOURCES & FALLBACK CHAINS

Flare network data:
  Primary:   Flare Network public RPC (free, direct)
  Secondary: Ankr / QuickNode Flare endpoints (paid failover)
  Tertiary:  FTSO historical via Songbird testnet for development

Multichain DeFi TVL / metrics:
  Primary:   DefiLlama free API
  Secondary: Protocol-specific subgraphs (The Graph)
  Tertiary:  Direct RPC reads per protocol

Price data (spot):
  Primary:   CoinGecko free tier
  Secondary: Binance US public API
  Tertiary:  Kraken public API

Macro feeds:
  Primary:   macro_feeds.py internal module — compiled feeds
  Secondary: FRED for rates

Token unlocks for DeFi governance tokens (Layer 4 sell-pressure signal):
  Primary:   protocol-specific governance-forum + tokenomics doc
  Secondary: cryptorank.io /token-unlock endpoints for DeFi-category tokens
  Tertiary:  TokenUnlocks.app

XRPL (optional):
  Installed separately: `pip install xrpl-py`. Imports guarded with
  try/except ImportError. Feature gracefully disables if not installed.

web3 (optional):
  Installed separately: `pip install web3>=6`. Same pattern.

---

## SECTION 11 — DEPLOYMENT ENVIRONMENTS

[MASTER TEMPLATE. Project-specific:]

Streamlit Cloud URL: https://flare-defi-model-mw8toxbjk5baae9zjbfrli.streamlit.app/

Private keys for on-chain interactions: NEVER in repo. NEVER in
Streamlit Cloud Secrets if the key controls real funds. Use read-only
public addresses for dashboard displays; any write path routes through
user's own wallet connection.

---

## SECTION 12 — DATA REFRESH RATES

[MASTER TEMPLATE. Project-specific windows:]

- On-chain TVL / utilization:  5 min cache
- Prices (spot):                1 min cache
- FTSO feeds:                   30 s cache (fast-moving by design)
- Protocol governance:          1 hour cache
- Scheduler scanners:           configurable per-scanner, in scheduler.py

---

## SECTION 13 — DATA UNIVERSE

FLARE UNIVERSE (starting — expands via scanner):
  FLR, WFLR, SGB, FXRP, FBTC, FETH, FUSD, FXD, FXAU

MULTICHAIN (when enabled):
  Major EVM chains: Ethereum, Arbitrum, Optimism, Base, Polygon
  Cosmos: Osmosis, Kava
  Solana: (optional, SDK-guarded)

RISK TIERS (5-tier, DeFi-calibrated):
  Tier 1: stablecoin-only positions, audited lending markets only
  Tier 2: stablecoin + LSTs, no leverage
  Tier 3: blue-chip DeFi (AAVE, Compound, Lido)
  Tier 4: blue-chip + adjacent protocols, modest LP positions
  Tier 5: full DeFi breadth incl. newer protocols, leveraged positions

---

## SECTION 14 — BACKUP & RESTORE PROTOCOL

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 15 — SPRINT TASK LIST

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 16 — SESSION CONTINUITY & RESUME PROTOCOL

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 17 — PARALLEL AGENT MONITORING & TAKEOVER

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 18 — STREAMLIT-SPECIFIC PATTERNS

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 19 — CROSS-APP MODULE DISCIPLINE

[MASTER TEMPLATE. This project is the architectural reference:]

The folder structure here (pages/, ui/, agents/, scanners/, models/) is
the canonical template. Sibling projects adopting it should mirror the
layout; divergence requires a note in this section and Section 11.

---

## SECTION 20 — GIT HYGIENE ON SHARED DEV MACHINES

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 21 — TONE & STYLE DURING COLLABORATION

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 22 — PROJECT-SPECIFIC CONSTRAINTS

- `agents/xrpl_executor.py` imports xrpl conditionally. No Python 3.14
  wheel for xrpl-py as of 2026-04; feature is off by default.
- `web3` is optional. Real wallet actions require manual opt-in via
  `ENABLE_WALLET_ACTIONS=true` in `.env` + a separate runtime confirmation.
- Sentry-sdk configured for error reporting; check sentry DSN is only the
  development one during demo phases.
- Docker build (`Dockerfile` + `docker-compose.yml`) for local full-stack
  testing, not used for Streamlit Cloud deploy.

---

## SECTION 23 — TOKEN EFFICIENCY (PROGRESS-PRESERVING)

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 24 — POST-CHANGE FULL REVIEW PROTOCOL (WHEN)

[VERBATIM FROM MASTER TEMPLATE.]

Project-specific notes:
- Fast-test suite target: under 30s locally.
- Hot paths for perf check: scanner results page, agent dashboard,
  Flare-specific FTSO feed page.
- Agents have a dry-run flag that defaults True — every agent path
  gets audited in both dry-run-on and dry-run-off modes.
- Scheduler idempotency: re-running a scanner must not duplicate rows.

---

## SECTION 25 — DEPLOYMENT VERIFICATION PROTOCOL

[VERBATIM FROM MASTER TEMPLATE.]

Project-specific:
- Deploy URL: https://flare-defi-model-mw8toxbjk5baae9zjbfrli.streamlit.app/
- Checklist: `shared-docs/deployment-checklists/flare-defi-model.md`
- Wallet-action gate: verify `ENABLE_WALLET_ACTIONS=false` in deploy
  secrets. Real on-chain actions should NEVER be on in production
  demos without explicit user approval.
- Fallback-chain test: swap Flare RPC URL to invalid; confirm Ankr
  failover activates within 10s.
- Optional XRPL / web3 paths: verify they gracefully disable if the
  deps aren't installed (no crash on import).
