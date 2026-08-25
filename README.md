# BNB Meme Scanner Bot

A Telegram bot that scans the BNB Smart Chain for meme-coin pools, scores them using transparent on-chain signals, checks sellability with honeypot.is, and sends qualifying alerts to Telegram.

## What it checks

- Market-cap range
- Liquidity / market-cap ratio
- 24h volume / market-cap ratio
- Buy/sell transaction pressure
- Recent price momentum
- Pool age
- Optional BscScan contract verification and holder concentration
- Honeypot/sellability simulation

The score is a research/screening heuristic, not a price prediction or financial recommendation. Meme coins are extremely high risk; always perform your own due diligence.

## Project layout

```text
.
├── .github/workflows/scan.yml   # Scheduled GitHub Actions scanner
├── data/.gitkeep                # Runtime SQLite database directory
├── logs/.gitkeep                # Runtime logs directory
├── alert_sender.py              # One-shot Telegram sender
├── bot.py                       # Interactive Telegram bot
├── bscscan.py                   # Optional BscScan safety checks
├── config.py                    # Environment/config loader
├── database.py                  # SQLite persistence
├── datasource.py                # GeckoTerminal BSC data client
├── formatting.py                # Telegram alert formatting
├── honeypot.py                  # Honeypot/sellability checks
├── main.py                      # Persistent VPS entry point
├── ratelimit.py                 # API rate limiter
├── scan_once.py                 # One scan pass for GitHub Actions
├── scanner.py                   # Scan/filter/score/alert orchestration
├── scoring.py                   # 0–100 scoring engine
├── .env.example
├── .gitignore
└── requirements.txt
```

## GitHub Actions setup

The workflow runs one scan every 30 minutes and can also be started manually from the Actions tab.

In **Settings → Secrets and variables → Actions**, add these secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `BSCSCAN_API_KEY` (optional, recommended)

Optional Actions variables:

- `MIN_MARKET_CAP_USD`
- `MAX_MARKET_CAP_USD`
- `MIN_LIQUIDITY_USD`
- `MIN_SCORE_TO_ALERT`

The workflow executes `python scan_once.py`, which performs one scan and exits.

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

VPS mode supports `/start`, `/setmcap`, `/setscore`, `/status`, `/scan`, and `/watchlist`.

## Configuration

Example values are provided in `.env.example`. Never commit real Telegram or BscScan credentials. `.env` is ignored by Git.

## Data sources

- GeckoTerminal public API: pool discovery and market data.
- honeypot.is public API: sellability/honeypot simulation.
- BscScan API: optional contract verification and holder data.

## License / risk notice

This project is provided for research and screening purposes. Nothing in this repository guarantees returns or token performance. You are responsible for your own trading decisions and due diligence.
