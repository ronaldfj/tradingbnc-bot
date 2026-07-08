"""
Consume órdenes de una cola y ejecuta el ciclo de vida completo en Binance:
sizing en USDT fijo -> validación de saldo -> orden de mercado -> OCO de TP/SL.
Ver CLAUDE.md para el detalle de arquitectura y el porqué de cada decisión.
"""
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
    """
    Un TradingExecutor por bot. `notify_fn` es la función async usada para
    reportar progreso/errores hacia afuera (normalmente TelegramBotListener.send_report).
    """

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
            try:
                await self.execute_order(order)
            except Exception as e:
                # Una orden individual mal formada o un error inesperado no debe
                # tumbar el loop entero (y con él, todo el bot vía asyncio.gather).
                logger.exception(f"Error inesperado procesando orden {order}: {e}")
                await self.report(f"❌ Error inesperado procesando orden: {e}")

    # ── Helpers de precisión Binance ──────────────────────────────────────────
    def _precision_from_step(self, step: float, default: int = 8) -> int:
        """Decimales que admite un step/tick size de Binance (ej. 0.00001 → 5)."""
        if step <= 0:
            return default
        return max(0, round(-math.log10(step)))

    def _round_step(self, qty: float, step: float) -> float:
        """Redondea qty al step_size correcto sin notación científica."""
        if step <= 0:
            return qty
        precision = self._precision_from_step(step)
        return round(math.floor(qty / step) * step, precision)

    def _get_symbol_filters(self, symbol: str) -> dict:
        """
        Lee de una sola vez los filtros de Binance necesarios para dimensionar
        y validar una orden (antes se llamaba get_symbol_info por separado para
        step_size, tick_size y min_notional: 3 requests por orden). Si Binance
        no responde o cambia el formato de filtros, cae a valores conservadores
        y deja constancia en el log en vez de fallar en silencio.
        """
        try:
            info = self.client.get_symbol_info(symbol)
            filters = {f['filterType']: f for f in info['filters']}

            step_size = float(filters['LOT_SIZE']['stepSize'])
            min_qty = float(filters['LOT_SIZE']['minQty'])
            tick_size = float(filters['PRICE_FILTER']['tickSize'])

            notional_filter = filters.get('MIN_NOTIONAL') or filters.get('NOTIONAL') or {}
            min_notional = float(notional_filter.get('minNotional', notional_filter.get('minVal', 0))) or 5.0

            return {
                'step_size': step_size,
                'min_qty': min_qty,
                'price_precision': self._precision_from_step(tick_size, default=2),
                'min_notional': min_notional,
                'base_asset': info.get('baseAsset') or symbol.replace('USDT', ''),
            }
        except Exception as e:
            logger.warning(f"No se pudieron leer los filtros de {symbol}, usando fallback conservador: {e}")
            return {
                'step_size': 0.00001,
                'min_qty': 0.00001,
                'price_precision': 2,
                'min_notional': 5.0,
                'base_asset': symbol.replace('USDT', ''),
            }

    # ── Ejecución ─────────────────────────────────────────────────────────────
    async def execute_order(self, order):
        """
        Ciclo de vida completo de una orden:
        1) precio actual, 2) sizing en TRADE_SIZE_USD + validación de mínimos,
        3) verificación de saldo, 4) orden de mercado, 5) OCO de TP/SL al
        precio real de fill. Cualquier fallo reporta y corta ahí (no reintenta).
        """
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
        raw_qty = TRADE_SIZE_USD / entry_price

        filters       = self._get_symbol_filters(symbol)
        step_size     = filters['step_size']
        min_qty       = filters['min_qty']
        base_asset    = filters['base_asset']
        quantity      = self._round_step(raw_qty, step_size)
        qty_precision = self._precision_from_step(step_size)
        qty_str       = f"{quantity:.{qty_precision}f}"

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
        min_notional = filters['min_notional']
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

        # 3 — Verificar saldo antes de operar
        try:
            account = self.client.get_account()
            balances = {b['asset']: float(b['free']) for b in account['balances'] if float(b['free']) > 0}
            usdt_free = balances.get('USDT', 0.0)
            base_free = balances.get(base_asset, 0.0)
            logger.info(f"Saldo disponible — USDT: {usdt_free:.2f} | {base_asset}: {base_free:.6f}")

            if side == 'BUY' and usdt_free < cost_usd:
                msg = (
                    f"Saldo insuficiente para {symbol}\n"
                    f"  Necesario: ${cost_usd:.2f} USDT | Disponible: ${usdt_free:.2f} USDT"
                )
                logger.error(msg)
                await self.report(f"⚠️ {msg}")
                return
            if side == 'SELL' and base_free < quantity:
                msg = (
                    f"Saldo insuficiente de {base_asset} para {symbol}\n"
                    f"  Necesario: {quantity} | Disponible: {base_free:.6f}"
                )
                logger.error(msg)
                await self.report(f"⚠️ {msg}")
                return
        except BinanceAPIException as e:
            logger.warning(f"No se pudo verificar saldo: {e}")

        # 4 — Orden de mercado
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

            # Saldo real disponible después de la orden (fuente de verdad para la OCO)
            try:
                acct      = self.client.get_account()
                available = next(
                    (float(b['free']) for b in acct['balances'] if b['asset'] == base_asset), 0.0
                )
                safe_qty  = self._round_step(available, step_size)
                qty_str   = f"{safe_qty:.{qty_precision}f}"
                logger.info(f"Saldo real post-compra: {available:.8f} {base_asset} → OCO qty: {qty_str}")
            except Exception as e:
                logger.warning(f"No se pudo leer saldo post-compra: {e}")
                # fallback: descontar comisión de fills
                if fills:
                    commission = sum(float(f['commission']) for f in fills if f.get('commissionAsset') == base_asset)
                    net_qty    = self._round_step(total_qty - commission, step_size)
                    qty_str    = f"{net_qty:.{qty_precision}f}"

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

        # 5 — TP y SL al precio real de entrada
        price_precision = filters['price_precision']
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