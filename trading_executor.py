import asyncio
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
import math
from dotenv import load_dotenv
import logging

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_KEY            = os.getenv('BINANCE_API_KEY')
API_SECRET         = os.getenv('BINANCE_API_SECRET')
TESTNET            = os.getenv('BINANCE_TESTNET', 'True').lower() == 'true'
TRADE_SIZE_USD = float(os.getenv('TRADE_SIZE_USD', '10.0'))


class TradingExecutor:
    def __init__(self, order_queue, notify_fn=None):
        self.order_queue = order_queue
        self.notify_fn = notify_fn
        if TESTNET:
            self.client = Client(API_KEY, API_SECRET, testnet=True)
            logger.info("Modo TESTNET (simulación)")
        else:
            self.client = Client(API_KEY, API_SECRET)
            logger.info(f"Modo REAL | Tamaño por trade: ${TRADE_SIZE_USD:.2f} USDT")

    # ── Arranque ──────────────────────────────────────────────────────────────
    async def start(self):
        logger.info("Executor esperando órdenes...")
        while True:
            order = await self.order_queue.get()
            logger.info(f"Procesando orden: {order}")
            await self.execute_order(order)

    # ── Helpers de precisión Binance ──────────────────────────────────────────
    def _get_lot_size(self, symbol: str):
        """
        Devuelve (step_size, min_qty) del LOT_SIZE filter para el símbolo.
        step_size define cuántos decimales acepta Binance para la cantidad.
        """
        try:
            info = self.client.get_symbol_info(symbol)
            for f in info['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    return float(f['stepSize']), float(f['minQty'])
        except Exception:
            pass
        return 0.00001, 0.00001  # fallback conservador

    def _round_step(self, qty: float, step: float) -> float:
        """Redondea qty al step_size correcto sin notación científica."""
        if step <= 0:
            return qty
        precision = max(0, round(-math.log10(step)))
        return round(math.floor(qty / step) * step, precision)

    def _get_price_precision(self, symbol: str) -> int:
        """Decimales permitidos para el precio (PRICE_FILTER → tickSize)."""
        try:
            info = self.client.get_symbol_info(symbol)
            for f in info['filters']:
                if f['filterType'] == 'PRICE_FILTER':
                    tick = float(f['tickSize'])
                    return max(0, round(-math.log10(tick)))
        except Exception:
            pass
        return 2

    def _get_min_notional(self, symbol: str) -> float:
        """Valor mínimo de la orden en USDT (MIN_NOTIONAL o NOTIONAL filter)."""
        try:
            info = self.client.get_symbol_info(symbol)
            for f in info['filters']:
                if f['filterType'] in ('MIN_NOTIONAL', 'NOTIONAL'):
                    return float(f.get('minNotional', f.get('minVal', 0)))
        except Exception:
            pass
        return 5.0  # fallback conservador

    # ── Ejecución ─────────────────────────────────────────────────────────────
    async def execute_order(self, order):
        symbol   = order['symbol']
        side     = order['side']           # 'BUY' | 'SELL'
        tp_pct   = order['tp_percent']     # porcentaje, ej: 4.11
        sl_pct   = order['sl_percent']     # porcentaje, ej: 4.00

        # 1 — Precio actual
        try:
            ticker      = self.client.get_symbol_ticker(symbol=symbol)
            entry_price = float(ticker['price'])
        except BinanceAPIException as e:
            logger.error(f"Error obteniendo precio: {e}")
            return

        # 2 — Sizing fijo en USDT
        raw_qty      = TRADE_SIZE_USD / entry_price

        step_size, min_qty = self._get_lot_size(symbol)
        quantity           = self._round_step(raw_qty, step_size)
        qty_precision      = max(0, round(-math.log10(step_size))) if step_size > 0 else 8
        qty_str            = f"{quantity:.{qty_precision}f}"

        if quantity < min_qty:
            msg = (
                f"Sizing fallido para {symbol}\n"
                f"  Trade size: ${TRADE_SIZE_USD:.2f} USDT | Precio entrada: ${entry_price:,.2f}\n"
                f"  qty calculada: {raw_qty:.8f} → redondeada: {quantity} (mínimo: {min_qty})\n"
                f"  Trade size mínimo necesario: ${min_qty * entry_price:.2f} USDT"
            )
            logger.error(msg)
            await self.report(f"⚠️ {msg}")
            return

        cost_usd     = quantity * entry_price
        min_notional = self._get_min_notional(symbol)
        logger.info(
            f"Sizing: trade=${TRADE_SIZE_USD:.2f} entry=${entry_price:,.2f}"
            f" → qty={qty_str} (~${cost_usd:.2f} USDT, mín notional=${min_notional:.2f})"
        )

        if cost_usd < min_notional:
            msg = (
                f"Notional insuficiente para {symbol}\n"
                f"  Orden: {qty_str} × ${entry_price:,.2f} = ${cost_usd:.2f} USDT\n"
                f"  Mínimo requerido: ${min_notional:.2f} USDT\n"
                f"  Subí TRADE_SIZE_USD a al menos ${min_notional:.2f} en el .env"
            )
            logger.error(msg)
            await self.report(f"⚠️ {msg}")
            return

        # 3 — Orden de mercado
        try:
            if side == 'BUY':
                resp = self.client.order_market_buy(symbol=symbol, quantity=qty_str)
            else:
                resp = self.client.order_market_sell(symbol=symbol, quantity=qty_str)

            # Precio real de ejecución (media ponderada de fills)
            fills = resp.get('fills', [])
            if fills:
                total_qty   = sum(float(f['qty']) for f in fills)
                entry_price = sum(float(f['price']) * float(f['qty']) for f in fills) / total_qty
                # Descuenta comisión cobrada en el activo base (ej. TON, BTC)
                base_asset  = symbol.replace('USDT', '')
                commission  = sum(float(f['commission']) for f in fills if f.get('commissionAsset') == base_asset)
                net_qty     = self._round_step(total_qty - commission, step_size)
                qty_str     = f"{net_qty:.{qty_precision}f}"
            # Si no hay fills (testnet), qty_str y entry_price quedan del cálculo previo

            cost_usd  = quantity * entry_price
            order_id  = resp.get('orderId', '?')
            status    = resp.get('status', '?')
            logger.info(f"Orden ejecutada: {resp}")
            await self.report(
                f"✅ Entrada ejecutada\n"
                f"  Par: {symbol} | Dir: {'LONG' if side == 'BUY' else 'SHORT'}\n"
                f"  ID: {order_id} | Estado: {status}\n"
                f"  Precio: ${entry_price:,.4f} | Qty: {qty_str}\n"
                f"  Costo: ~${cost_usd:.2f} USDT"
            )
        except BinanceAPIException as e:
            logger.error(f"Error en orden: {e}")
            await self.report(f"❌ Error entrada {symbol}: {e}")
            return

        # 4 — TP y SL al precio real de entrada
        price_precision = self._get_price_precision(symbol)
        if side == 'BUY':
            tp_price = round(entry_price * (1 + tp_pct / 100), price_precision)
            sl_price = round(entry_price * (1 - sl_pct / 100), price_precision)
            exit_side = 'SELL'
        else:
            tp_price = round(entry_price * (1 - tp_pct / 100), price_precision)
            sl_price = round(entry_price * (1 + sl_pct / 100), price_precision)
            exit_side = 'BUY'

        try:
            # Nueva API Binance: /api/v3/orderList/oco requiere aboveType/belowType
            if exit_side == 'SELL':
                # Salida LONG: TP arriba (LIMIT_MAKER), SL abajo (STOP_LOSS_LIMIT)
                oco_params = dict(
                    symbol=symbol, side='SELL', quantity=qty_str,
                    aboveType='LIMIT_MAKER', abovePrice=str(tp_price),
                    belowType='STOP_LOSS_LIMIT',
                    belowStopPrice=str(sl_price), belowPrice=str(sl_price),
                    belowTimeInForce='GTC',
                )
            else:
                # Salida SHORT: SL arriba (STOP_LOSS_LIMIT), TP abajo (LIMIT_MAKER)
                oco_params = dict(
                    symbol=symbol, side='BUY', quantity=qty_str,
                    aboveType='STOP_LOSS_LIMIT',
                    aboveStopPrice=str(sl_price), abovePrice=str(sl_price),
                    aboveTimeInForce='GTC',
                    belowType='LIMIT_MAKER', belowPrice=str(tp_price),
                )
            oco_resp   = self.client._post('orderList/oco', signed=True, data=oco_params)
            list_id    = oco_resp.get('orderListId', '?')
            oco_status = oco_resp.get('listStatusType', '?')
            orders     = oco_resp.get('orders', [])
            ids        = ' / '.join(str(o['orderId']) for o in orders) if orders else '?'
            logger.info(f"OCO colocado — TP ${tp_price} / SL ${sl_price} | resp: {oco_resp}")
            await self.report(
                f"🎯 OCO colocado\n"
                f"  ListID: {list_id} | Estado: {oco_status}\n"
                f"  IDs órdenes: {ids}\n"
                f"  TP: ${tp_price:,.2f} (+{tp_pct}%) | SL: ${sl_price:,.2f} (-{sl_pct}%)"
            )
        except BinanceAPIException as e:
            logger.error(f"Error OCO: {e}")
            await self.report(f"⚠️ Orden entrada OK pero error en OCO: {e}")

    # ── Reporte ───────────────────────────────────────────────────────────────
    async def report(self, message):
        logger.info(f"Reporte: {message}")
        if self.notify_fn:
            await self.notify_fn(message)