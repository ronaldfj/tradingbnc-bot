import asyncio
import logging
from telegram_listener_bot import TelegramBotListener   # <--- nuevo nombre
from trading_executor import TradingExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    queue = asyncio.Queue()
    listener = TelegramBotListener(queue)   # <--- clase correcta
    executor = TradingExecutor(queue)
    await asyncio.gather(listener.start(), executor.start())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario")
