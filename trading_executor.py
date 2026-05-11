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
RISK_PER_TRADE_USD = float(os.getenv('RISK_PER_TRADE_USD', '10.0'))


class TradingExecutor:
    def __init__(self, order_queue):
        self.order_queue = order_queue
        if TESTNET:
            self.client = Client(API_KEY, API_SECRET, testnet=True)
            logger.info("Modo TESTNET (simulación)")
        else:
            self.client = Client(API_KEY, API_SECRET)
            logger.info(f"Modo REAL | Riesgo por trade: ${RISK_PER_TRADE_USD}")

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

        # 2 — Cantidad recalculada al precio actual
        #     qty = RISK_PER_TRADE_USD / (entry_price × sl_pct / 100)
        #     Así si BTC sube, la cantidad baja automáticamente.
        sl_distance_usd = entry_price * (sl_pct / 100)
        raw_qty         = RISK_PER_TRADE_USD / sl_distance_usd

        step_size, min_qty = self._get_lot_size(symbol)
        quantity           = self._round_step(raw_qty, step_size)

        if quantity < min_qty:
            msg = (f"Cantidad calculada {quantity} < mínimo {min_qty} para {symbol}. "
                   f"Sube RISK_PER_TRADE_USD o revisa el par.")
            logger.error(msg)
            await self.report(f"⚠️ {msg}")
            return

        cost_usd = quantity * entry_price
        logger.info(
            f"Sizing: entry={entry_price} sl_pct={sl_pct}% "
            f"→ qty={quantity} (~${cost_usd:.2f} USDT)"
        )

        # 3 — Orden de mercado
        try:
            if side == 'BUY':
                resp = self.client.order_market_buy(symbol=symbol, quantity=quantity)
            else:
                resp = self.client.order_market_sell(symbol=symbol, quantity=quantity)
            logger.info(f"Orden ejecutada: {resp}")
            await self.report(f"✅ Entrada {side} {symbol} a ${entry_price:,.2f} | qty={quantity} | costo ~${cost_usd:.2f}")
        except BinanceAPIException as e:
            logger.error(f"Error en orden: {e}")
            await self.report(f"❌ Error entrada {symbol}: {e}")
            return

        # 4 — TP y SL al precio actual
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
            self.client.create_order(
                symbol=symbol,
                side=exit_side,
                type='TAKE_PROFIT_LIMIT',
                quantity=quantity,
                price=str(tp_price),
                stopPrice=str(tp_price),
                timeInForce='GTC',
            )
            self.client.create_order(
                symbol=symbol,
                side=exit_side,
                type='STOP_LOSS_LIMIT',
                quantity=quantity,
                price=str(sl_price),
                stopPrice=str(sl_price),
                timeInForce='GTC',
            )
            logger.info(f"TP ${tp_price} / SL ${sl_price} configurados")
            await self.report(f"🎯 TP ${tp_price:,.2f} / 🛑 SL ${sl_price:,.2f}")
        except BinanceAPIException as e:
            logger.error(f"Error TP/SL: {e}")
            await self.report(f"⚠️ Orden entrada OK pero error en TP/SL: {e}")

    # ── Reporte ───────────────────────────────────────────────────────────────
    async def report(self, message):
        print(f"📢 Reporte: {message}")