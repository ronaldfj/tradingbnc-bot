import asyncio
import re
from datetime import datetime
import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')  # Ej: '@micanal' o -1001234567890

class TelegramBotListener:
    def __init__(self, order_queue):
        self.order_queue = order_queue
        self.app = Application.builder().token(TOKEN).build()

    async def start(self):
        self.app.add_handler(MessageHandler(filters.Chat(chat_id=CHANNEL_ID) & filters.TEXT, self.handler))
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        logger.info(f"🤖 Bot de Telegram escuchando en {CHANNEL_ID}")
        # Mantener el bot vivo
        while True:
            await asyncio.sleep(1)

    async def handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message.text
        logger.info(f"📩 Mensaje recibido: {message[:100]}")
        order_info = self.parse_signal(message)
        if order_info:
            await self.order_queue.put(order_info)
            logger.info(f"✅ Señal encolada: {order_info}")

    def parse_signal(self, message):
        # 🔧 AJUSTA ESTE PATRÓN SEGÚN EL FORMATO REAL DE TUS SEÑALES
        pattern = r'(LONG|SHORT)\s+(\w+)\s+([\d.]+)\s+(\d+)%\s+TP\s+(\d+)%\s+SL'
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return {
                'side': 'BUY' if match.group(1).upper() == 'LONG' else 'SELL',
                'symbol': match.group(2).upper(),
                'quantity': float(match.group(3)),
                'tp_percent': float(match.group(4)),
                'sl_percent': float(match.group(5)),
                'timestamp': datetime.now().isoformat()
            }
        return None

    async def stop(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
