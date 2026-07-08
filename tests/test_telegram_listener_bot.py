import asyncio

import pytest

from telegram_listener_bot import TelegramBotListener


def make_listener():
    return TelegramBotListener(order_queue=asyncio.Queue())


def test_parse_signal_long():
    order = make_listener().parse_signal("LONG BTCUSDT 0.01184 4.11% TP 4.00% SL")
    assert order["side"] == "BUY"
    assert order["symbol"] == "BTCUSDT"
    assert order["tp_percent"] == 4.11
    assert order["sl_percent"] == 4.00


def test_parse_signal_short_is_case_insensitive():
    order = make_listener().parse_signal("short ethusdt 0.32143 1.5% tp 3% sl")
    assert order["side"] == "SELL"
    assert order["symbol"] == "ETHUSDT"
    assert order["tp_percent"] == 1.5
    assert order["sl_percent"] == 3.0


def test_parse_signal_returns_none_for_unrelated_message():
    assert make_listener().parse_signal("Buenos días a todos!") is None


def test_parse_signal_returns_none_when_missing_sl():
    assert make_listener().parse_signal("LONG BTCUSDT 0.01184 4.11% TP") is None


def test_parse_raw_callback_reconstructs_order_from_prices():
    listener = make_listener()
    # ENTER_RAW:<sym>|<side>|<entry>|<stop>|<tp1>|<rm>  con RISK_PER_TRADE_USD default = 10.0
    data = "ENTER_RAW:BTC|LONG|20000|19500|21000|1"
    order = listener._parse_raw_callback(data)

    assert order["symbol"] == "BTCUSDT"
    assert order["side"] == "BUY"
    assert order["quantity"] == pytest.approx(0.02)       # (10 * 1) / |20000-19500|
    assert order["tp_percent"] == pytest.approx(5.0)       # |21000-20000|/20000 * 100
    assert order["sl_percent"] == pytest.approx(2.5)       # |19500-20000|/20000 * 100


def test_parse_raw_callback_returns_none_when_entry_equals_stop():
    listener = make_listener()
    data = "ENTER_RAW:BTC|LONG|20000|20000|21000|1"
    assert listener._parse_raw_callback(data) is None


def test_parse_raw_callback_returns_none_on_malformed_payload():
    listener = make_listener()
    assert listener._parse_raw_callback("ENTER_RAW:esto-no-tiene-el-formato-esperado") is None
