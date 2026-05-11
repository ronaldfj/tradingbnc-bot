import asyncio
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
from dotenv import load_dotenv
import logging

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
TESTNET = os.getenv('BINANCE_TESTNET', 'True').lower() == 'true'

class TradingExecutor:
    def __init__(self, order_queue):
        self.order_queue = order_queue
        if TESTNET:
            self.client = Client(API_KEY, API_SECRET, testnet=True)
            logger.info("Modo TESTNET (simulación)")
        else:
            self.client = Client(API_KEY, API_SECRET)
            logger.info("Modo REAL")

    async def start(self):
        logger.info("Executor esperando órdenes...")
        while True:
            order = await self.order_queue.get()
            logger.info(f"Procesando orden: {order}")
            await self.execute_order(order)

    async def execute_order(self, order):
        symbol = order['symbol']
        side = order['side']
        quantity = order['quantity']
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            entry_price = float(ticker['price'])
        except BinanceAPIException as e:
            logger.error(f"Error obteniendo precio: {e}")
            return

        # Orden de mercado
        try:
            if side == 'BUY':
                resp = self.client.order_market_buy(symbol=symbol, quantity=quantity)
            else:
                resp = self.client.order_market_sell(symbol=symbol, quantity=quantity)
            logger.info(f"Orden ejecutada: {resp}")
            await self.report(f"✅ Entrada {side} {symbol} a {entry_price}")
        except BinanceAPIException as e:
            logger.error(f"Error en orden: {e}")
            await self.report(f"❌ Error entrada {symbol}: {e}")
            return

        # Calcular TP y SL
        if side == 'BUY':
            tp_price = entry_price * (1 + order['tp_percent'] / 100)
            sl_price = entry_price * (1 - order['sl_percent'] / 100)
        else:
            tp_price = entry_price * (1 - order['tp_percent'] / 100)
            sl_price = entry_price * (1 + order['sl_percent'] / 100)

        try:
            # Take Profit
            self.client.create_order(
                symbol=symbol,
                side='SELL' if side == 'BUY' else 'BUY',
                type='TAKE_PROFIT_LIMIT',
                quantity=quantity,
                price=tp_price,
                stopPrice=tp_price,
                timeInForce='GTC'
            )
            # Stop Loss
            self.client.create_order(
                symbol=symbol,
                side='SELL' if side == 'BUY' else 'BUY',
                type='STOP_LOSS_LIMIT',
                quantity=quantity,
                price=sl_price,
                stopPrice=sl_price,
                timeInForce='GTC'
            )
            logger.info(f"TP {tp_price} / SL {sl_price} configurados")
            await self.report(f"🎯 TP {tp_price:.2f} / 🛑 SL {sl_price:.2f}")
        except BinanceAPIException as e:
            logger.error(f"Error TP/SL: {e}")
            await self.report(f"⚠️ Error configurando TP/SL: {e}")

    async def report(self, message):
        # Aquí puedes añadir envío a Telegram vía bot o webhook
        print(f"📢 Reporte: {message}")
