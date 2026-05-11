import asyncio
import re
from telethon import TelegramClient, events
import os
from dotenv import load_dotenv
import json
from datetime import datetime
import logging

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE_NUMBER = os.getenv('TELEGRAM_PHONE_NUMBER')
CHANNEL_USERNAME = os.getenv('TELEGRAM_CHANNEL_USERNAME')

class TelegramListener:
    def __init__(self, order_queue):
        self.client = TelegramClient('session_name', API_ID, API_HASH)
        self.order_queue = order_queue

    async def start(self):
        await self.client.start(phone=PHONE_NUMBER)
        logger.info(f"Cliente Telegram iniciado como: {(await self.client.get_me()).username}")
        
        @self.client.on(events.NewMessage(chats=CHANNEL_USERNAME))
        async def handler(event):
            message = event.message.text
            logger.info(f"Mensaje recibido: {message[:80]}")
            order_info = self.parse_signal(message)
            if order_info:
                await self.order_queue.put(order_info)
                logger.info(f"Señal parseada: {order_info}")
            else:
                logger.warning("No se pudo parsear la señal")
        
        await self.client.run_until_disconnected()

    def parse_signal(self, message):
        # ⚠️ AQUÍ DEBES ADAPTAR EL FORMATO REAL DE LAS SEÑALES DE TU BOT
        # Ejemplo: LONG BTCUSDT 0.01 2% TP 4% SL
        pattern = r'(LONG|SHORT)\s+(\w+)\s+([\d.]+)\s+(\d+)%\s+TP\s+(\d+)%\s+SL'
        match = re.search(pattern, message)
        if match:
            return {
                'side': 'BUY' if match.group(1) == 'LONG' else 'SELL',
                'symbol': match.group(2),
                'quantity': float(match.group(3)),
                'tp_percent': float(match.group(4)),
                'sl_percent': float(match.group(5)),
                'timestamp': datetime.now().isoformat()
            }
        return None

    async def stop(self):
        await self.client.disconnect()
