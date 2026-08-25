# BNB Meme Scanner Bot

A Telegram research scanner for the BNB Smart Chain focused on **tokens below $50,000 market cap**.

The scanner discovers new/trending BSC pools, runs the available market and security audits, calculates a transparent 0–100 score, blocks confirmed honeypots, and sends the audit report to Telegram.

## Product rule

**Hard market-cap ceiling: $50,000.** No alert may exceed this ceiling.

Liquidity, volume, buy/sell pressure, momentum, pool age, contract verification, holder concentration and sellability are scored/reported rather than silently hiding a token before the audit.

## What an alert contains

- Market cap and liquidity
- 24h volume
- 1h / 6h / 24h price movement
- Buy/sell transaction counts
- Pool age
- 0–100 score and component breakdown
- Honeypot/sellability status when available
- Buy/sell tax when available
- BscScan contract verification when available
- Top-holder concentration when available
- Owner status when available
- Risk flags and warnings
- GeckoTerminal chart link

A confirmed honeypot is a hard block and is never alerted.

## Important data-source limitation

Some BscScan holder/contract fields can be unavailable depending on API access. The bot reports **UNKNOWN** rather than pretending an audit passed.

## Project layout

```text
.
├── .github/workflows/scan.yml
├── data/.gitkeep
├── logs/.gitkeep
├── alert_sender.py
├── bot.py
├── bscscan.py
├── config.py
├── database.py
├── datasource.py
├── formatting.py
├── honeypot.py
├── main.py
├── ratelimit.py
├── scan_once.py
├── scanner.py
├── scoring.py
├── .env.example
├── .gitignore
└── requirements.txt
```

## GitHub Actions setup

The workflow runs a scan every 30 minutes and can also be started manually.

In **Settings → Secrets and variables → Actions**, add:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `BSCSCAN_API_KEY` (recommended)

Optional variables are documented in `.env.example`. The hard $50K ceiling is enforced by application code even if an Actions variable is changed incorrectly.

## VPS mode

```bash
git clone https://github.com/atlasglobalcareer-dev/Bnb-Bot.git
cd Bnb-Bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## Risk notice

This project is a research/screening tool. It does not predict prices or guarantee returns. BNB meme tokens are extremely high risk. Always perform independent due diligence before trading.
