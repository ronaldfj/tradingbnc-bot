import asyncio
import re
from datetime import datetime
import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters, ContextTypes

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # '@micanal' o -1001234567890


class TelegramBotListener:
    def __init__(self, order_queue):
        self.order_queue = order_queue
        self.app = Application.builder().token(TOKEN).build()

    # ── Arranque ──────────────────────────────────────────────────────────────
    async def start(self):
        # Mensajes de texto (canal)
        self.app.add_handler(
            MessageHandler(
                filters.Chat(chat_id=int(CHANNEL_ID) if str(CHANNEL_ID).lstrip("-").isdigit() else CHANNEL_ID)
                & filters.TEXT,
                self.message_handler,
            )
        )
        # Botones inline (callback_query) — sin filtro de chat, vienen del bot directamente
        self.app.add_handler(CallbackQueryHandler(self.button_handler))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=["message", "callback_query"])
        logger.info(f"🤖 Bot escuchando en {CHANNEL_ID}")

        while True:
            await asyncio.sleep(1)

    # ── Handler: mensajes de texto ────────────────────────────────────────────
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message.text
        logger.info(f"📩 Mensaje recibido: {message[:120]}")
        order_info = self.parse_signal(message)
        if order_info:
            await self.order_queue.put(order_info)
            logger.info(f"✅ Señal encolada: {order_info}")

    # ── Handler: botones inline ───────────────────────────────────────────────
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data  = query.data
        logger.info(f"🔘 Callback recibido: {data}")

        if data.startswith("ENTER:"):
            order_str  = data[6:]          # quita "ENTER:"
            order_info = self.parse_signal(order_str)
            if order_info:
                await self.order_queue.put(order_info)
                await query.answer(f"✅ Orden enviada: {order_info['symbol']} {order_info['side']}")
                logger.info(f"✅ Orden encolada desde botón: {order_info}")
            else:
                await query.answer("⚠️ No se pudo parsear la orden")
                logger.warning(f"⚠️ parse_signal falló para: {order_str}")

        elif data.startswith("ENTER_RAW:"):
            # Fallback: reconstruye la orden desde precios absolutos
            order_info = self._parse_raw_callback(data)
            if order_info:
                await self.order_queue.put(order_info)
                await query.answer(f"✅ Orden enviada: {order_info['symbol']} {order_info['side']}")
                logger.info(f"✅ Orden RAW encolada: {order_info}")
            else:
                await query.answer("⚠️ Error al procesar ENTER_RAW")

        elif data.startswith("REJECT:"):
            _, payload = data.split(":", 1)
            sym, side  = payload.split("|")
            await query.answer(f"❌ {sym} {side} rechazado")
            logger.info(f"❌ Señal rechazada: {sym} {side}")

        else:
            await query.answer("Acción desconocida")

        # Elimina los botones del mensaje para evitar doble-ejecución
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass  # El mensaje puede haber expirado

    # ── Parser principal ──────────────────────────────────────────────────────
    def parse_signal(self, message: str):
        """
        Acepta:
            LONG BTCUSDT 0.01184 4.11% TP 4.00% SL
            SHORT ETHUSDT 0.32143 1.5% TP 3% SL
        """
        pattern = r'(LONG|SHORT)\s+(\w+)\s+([\d.]+)\s+([\d.]+)%\s+TP\s+([\d.]+)%\s+SL'
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return {
                "side":       "BUY" if match.group(1).upper() == "LONG" else "SELL",
                "symbol":     match.group(2).upper(),
                "quantity":   float(match.group(3)),
                "tp_percent": float(match.group(4)),
                "sl_percent": float(match.group(5)),
                "timestamp":  datetime.now().isoformat(),
            }
        return None

    # ── Fallback ENTER_RAW ────────────────────────────────────────────────────
    def _parse_raw_callback(self, data: str):
        """
        Formato: ENTER_RAW:<sym>|<side>|<entry>|<stop>|<tp1>|<rm>
        Reconstruye qty = (RISK_PER_TRADE_USD × rm) / |entry - stop|
        """
        try:
            RISK_PER_TRADE_USD = float(os.getenv("RISK_PER_TRADE_USD", "10.0"))
            _, payload = data.split(":", 1)
            sym, side, entry, stop, tp1, rm = payload.split("|")
            entry, stop, tp1, rm = float(entry), float(stop), float(tp1), float(rm)
            distancia_sl = abs(entry - stop)
            if distancia_sl <= 0:
                return None
            qty      = (RISK_PER_TRADE_USD * rm) / distancia_sl
            tp_pct   = abs((tp1  - entry) / entry * 100)
            sl_pct   = abs((stop - entry) / entry * 100)
            usdt_sym = sym if sym.endswith("USDT") else f"{sym}USDT"
            order_str = f"{'LONG' if side == 'LONG' else 'SHORT'} {usdt_sym} {qty:.5f} {tp_pct:.2f}% TP {sl_pct:.2f}% SL"
            return self.parse_signal(order_str)
        except Exception as e:
            logger.error(f"❌ Error en _parse_raw_callback: {e}")
            return None

    # ── Parada ────────────────────────────────────────────────────────────────
    async def stop(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
