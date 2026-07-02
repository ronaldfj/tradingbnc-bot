# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A bot that reads trade signals from a Telegram channel and executes them on Binance: market entry sized in fixed USDT, followed by an OCO (TP/SL) exit order. No package manifest exists yet (no `requirements.txt`/`pyproject.toml`) — dependencies must be installed manually.

## Commands

Install dependencies (no lockfile/manifest in repo):
```bash
pip3 install python-binance python-telegram-bot telethon python-dotenv pytest pytest-asyncio
```

Run the bot:
```bash
python3 main.py
```

Run tests:
```bash
python3 -m pytest tests/ -v
python3 -m pytest tests/test_trading_executor.py::test_buy_blocked_when_usdt_balance_insufficient -v  # single test
```
`tests/conftest.py` stubs the `binance` package with fakes if `python-binance` isn't installed, so the test suite runs without Binance credentials or network access. All Binance interaction in tests goes through a mocked `client` attribute on `TradingExecutor`.

## Configuration (`.env`)

- `BINANCE_API_KEY`, `BINANCE_API_SECRET` — Binance credentials.
- `BINANCE_TESTNET` — `True`/`False`; selects testnet vs. real trading (`trading_executor.py`).
- `TRADE_SIZE_USD` — fixed USDT size per trade (default `10.0`). The quantity parsed from the Telegram signal is **ignored** for entry sizing — `execute_order` always recomputes quantity from `TRADE_SIZE_USD / entry_price`.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` — used by `telegram_listener_bot.py` (the listener actually wired up in `main.py`).
- `RISK_PER_TRADE_USD` — used only by the `ENTER_RAW` inline-button fallback path in `telegram_listener_bot.py`, to derive quantity from entry/stop distance and risk multiple.
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE_NUMBER`, `TELEGRAM_CHANNEL_USERNAME` — used only by `telegram_listener.py`, a Telethon-based userbot listener that is **not** wired into `main.py` (legacy/alternate implementation, kept for reference).

## Architecture

Producer/consumer over an `asyncio.Queue` of order dicts (`{symbol, side, tp_percent, sl_percent, ...}`), run concurrently via `asyncio.gather`:

- **Listener** (`telegram_listener_bot.py::TelegramBotListener`) — polls a Telegram channel via the Bot API. Parses raw text signals (`LONG BTCUSDT 0.01184 4.11% TP 4.00% SL`) with a regex, or reconstructs an order from inline-button callback data (`ENTER:`, `ENTER_RAW:`, `REJECT:`). Pushes parsed orders onto the queue. Also exposes `send_report()`, wired as `TradingExecutor`'s `notify_fn` so execution results get posted back to the same Telegram chat.
- **Executor** (`trading_executor.py::TradingExecutor`) — consumes the queue and drives the full trade lifecycle per order:
  1. Fetch current price, size the order in `TRADE_SIZE_USD`, round to the symbol's `LOT_SIZE` step and validate against `minQty`/`MIN_NOTIONAL` (fetched from `get_symbol_info` filters).
  2. Verify account balance (`get_account`) covers the order before submitting — blocks on insufficient USDT (BUY) or base-asset balance (SELL). If the balance check itself errors, it logs and proceeds rather than blocking the trade.
  3. Submit the market order (`order_market_buy`/`order_market_sell`), then re-read the **actual post-trade balance** as the source of truth for OCO quantity (falls back to fills-minus-commission if that re-read fails).
  4. Compute TP/SL prices off the real average fill price, then place an OCO exit via a direct `client._post('orderList/oco', ...)` call — the installed `python-binance` client wrapper doesn't yet support the newer `aboveType`/`belowType` OCO API shape, so this bypasses the library's helper method and hits the endpoint directly.
  5. Reports progress/errors at each stage via `self.report()` → `notify_fn`.

`main.py` is the entrypoint wiring `TelegramBotListener` + `TradingExecutor` together; it does not use `telegram_listener.py`.
