import asyncio
from unittest.mock import MagicMock

import pytest

from trading_executor import TradingExecutor

SYMBOL = "BTCUSDT"

LOT_SIZE_FILTERS = {
    "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5.0"},
    ]
}


def make_executor(fake_client):
    executor = TradingExecutor(order_queue=asyncio.Queue())
    executor.client = fake_client
    executor.report = MagicMock(side_effect=_async_noop)
    return executor


async def _async_noop(*args, **kwargs):
    return None


def make_order(side="BUY"):
    return {"symbol": SYMBOL, "side": side, "tp_percent": 4.0, "sl_percent": 2.0}


@pytest.mark.asyncio
async def test_buy_blocked_when_usdt_balance_insufficient():
    client = MagicMock()
    client.get_symbol_ticker.return_value = {"price": "20000.0"}  # cost ~= 10 USDT
    client.get_symbol_info.return_value = LOT_SIZE_FILTERS
    client.get_account.return_value = {
        "balances": [{"asset": "USDT", "free": "1.0"}]  # menos de lo necesario
    }

    executor = make_executor(client)
    await executor.execute_order(make_order("BUY"))

    client.order_market_buy.assert_not_called()
    assert any(
        "Saldo insuficiente" in call.args[0] for call in executor.report.call_args_list
    )


@pytest.mark.asyncio
async def test_sell_blocked_when_base_asset_balance_insufficient():
    client = MagicMock()
    client.get_symbol_ticker.return_value = {"price": "20000.0"}
    client.get_symbol_info.return_value = LOT_SIZE_FILTERS
    client.get_account.return_value = {
        "balances": [{"asset": "BTC", "free": "0.0001"}]  # menos que la qty calculada
    }

    executor = make_executor(client)
    await executor.execute_order(make_order("SELL"))

    client.order_market_sell.assert_not_called()
    assert any(
        "Saldo insuficiente" in call.args[0] for call in executor.report.call_args_list
    )


@pytest.mark.asyncio
async def test_buy_proceeds_when_balance_is_sufficient():
    client = MagicMock()
    client.get_symbol_ticker.return_value = {"price": "20000.0"}
    client.get_symbol_info.return_value = LOT_SIZE_FILTERS
    client.get_account.side_effect = [
        {"balances": [{"asset": "USDT", "free": "1000.0"}]},  # chequeo previo
        {"balances": [{"asset": "BTC", "free": "0.0005"}]},   # saldo post-compra
    ]
    client.order_market_buy.return_value = {
        "orderId": 1,
        "status": "FILLED",
        "fills": [{"price": "20000.0", "qty": "0.0005", "commission": "0", "commissionAsset": "BTC"}],
    }
    client._post.return_value = {"orderListId": 1, "listStatusType": "EXEC_STARTED", "orders": []}

    executor = make_executor(client)
    await executor.execute_order(make_order("BUY"))

    client.order_market_buy.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_error_does_not_block_order():
    from binance.exceptions import BinanceAPIException

    client = MagicMock()
    client.get_symbol_ticker.return_value = {"price": "20000.0"}
    client.get_symbol_info.return_value = LOT_SIZE_FILTERS
    client.get_account.side_effect = BinanceAPIException(MagicMock(status_code=500, text="{}"), 500, "{}")
    client.order_market_buy.return_value = {
        "orderId": 1,
        "status": "FILLED",
        "fills": [{"price": "20000.0", "qty": "0.0005", "commission": "0", "commissionAsset": "BTC"}],
    }
    client._post.return_value = {"orderListId": 1, "listStatusType": "EXEC_STARTED", "orders": []}

    executor = make_executor(client)
    await executor.execute_order(make_order("BUY"))

    client.order_market_buy.assert_called_once()


@pytest.mark.asyncio
async def test_order_blocked_when_below_min_qty():
    client = MagicMock()
    # Precio altísimo -> qty calculada redondea a 0, por debajo de minQty=0.00001
    client.get_symbol_ticker.return_value = {"price": "50000000.0"}
    client.get_symbol_info.return_value = LOT_SIZE_FILTERS

    executor = make_executor(client)
    await executor.execute_order(make_order("BUY"))

    client.order_market_buy.assert_not_called()
    assert any(
        "Sizing fallido" in call.args[0] for call in executor.report.call_args_list
    )


@pytest.mark.asyncio
async def test_order_blocked_when_below_min_notional():
    client = MagicMock()
    client.get_symbol_ticker.return_value = {"price": "20000.0"}  # cost ~= 10 USDT
    client.get_symbol_info.return_value = {
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "50.0"},  # > costo de la orden
        ]
    }

    executor = make_executor(client)
    await executor.execute_order(make_order("BUY"))

    client.order_market_buy.assert_not_called()
    assert any(
        "Notional insuficiente" in call.args[0] for call in executor.report.call_args_list
    )


@pytest.mark.asyncio
async def test_full_success_flow_reports_entry_and_oco():
    client = MagicMock()
    client.get_symbol_ticker.return_value = {"price": "20000.0"}
    client.get_symbol_info.return_value = LOT_SIZE_FILTERS
    client.get_account.side_effect = [
        {"balances": [{"asset": "USDT", "free": "1000.0"}]},
        {"balances": [{"asset": "BTC", "free": "0.0005"}]},
    ]
    client.order_market_buy.return_value = {
        "orderId": 1,
        "status": "FILLED",
        "fills": [{"price": "20000.0", "qty": "0.0005", "commission": "0", "commissionAsset": "BTC"}],
    }
    client._post.return_value = {
        "orderListId": 42,
        "listStatusType": "EXEC_STARTED",
        "orders": [{"orderId": 101}, {"orderId": 102}],
    }

    executor = make_executor(client)
    await executor.execute_order(make_order("BUY"))

    reports = [call.args[0] for call in executor.report.call_args_list]
    assert any("Entrada ejecutada" in r for r in reports)
    assert any("OCO colocado" in r and "42" in r for r in reports)


@pytest.mark.asyncio
async def test_oco_failure_is_reported_after_successful_entry():
    from binance.exceptions import BinanceAPIException

    client = MagicMock()
    client.get_symbol_ticker.return_value = {"price": "20000.0"}
    client.get_symbol_info.return_value = LOT_SIZE_FILTERS
    client.get_account.side_effect = [
        {"balances": [{"asset": "USDT", "free": "1000.0"}]},
        {"balances": [{"asset": "BTC", "free": "0.0005"}]},
    ]
    client.order_market_buy.return_value = {
        "orderId": 1,
        "status": "FILLED",
        "fills": [{"price": "20000.0", "qty": "0.0005", "commission": "0", "commissionAsset": "BTC"}],
    }
    client._post.side_effect = BinanceAPIException(MagicMock(status_code=400, text="{}"), 400, "{}")

    executor = make_executor(client)
    await executor.execute_order(make_order("BUY"))

    client.order_market_buy.assert_called_once()
    reports = [call.args[0] for call in executor.report.call_args_list]
    assert any("error en OCO" in r for r in reports)


@pytest.mark.asyncio
async def test_start_loop_survives_unexpected_exception_in_one_order():
    # Una excepción no relacionada con Binance (p. ej. un KeyError por una orden
    # mal formada, o un error de red) no debe tumbar el loop de start().
    client = MagicMock()
    client.get_symbol_ticker.side_effect = RuntimeError("network blew up")

    executor = make_executor(client)
    await executor.order_queue.put(make_order("BUY"))

    with pytest.raises(asyncio.TimeoutError):
        # start() es un while True; si sigue vivo tras la orden fallida, se queda
        # esperando la próxima y el wait_for expira en vez de propagar el error.
        await asyncio.wait_for(executor.start(), timeout=0.1)

    assert any(
        "Error inesperado" in call.args[0] for call in executor.report.call_args_list
    )
