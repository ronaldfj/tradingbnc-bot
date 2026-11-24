"""Entrypoint: conecta el listener de Telegram con el executor de Binance vía una cola compartida."""
import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from telegram_listener_bot import TelegramBotListener
from trading_executor import TradingExecutor

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# force=True: los módulos importados arriba ya llamaron basicConfig; esto lo reemplaza.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(os.path.join(LOG_DIR, "bot.log"), maxBytes=5_000_000, backupCount=5),
    ],
    force=True,
)
# httpx loguea cada poll de getUpdates a INFO y entierra los logs útiles.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

async def main():
    queue = asyncio.Queue()
    listener = TelegramBotListener(queue)
    executor = TradingExecutor(queue, notify_fn=listener.send_report)
    await asyncio.gather(listener.start(), executor.start())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario")
