import sys
import types

# python-binance no está instalado en este entorno de tests; el executor sólo
# necesita `Client` (nunca instanciado de verdad, se reemplaza por un mock) y
# `BinanceAPIException` como clase de excepción, así que se stubean si falta.
try:
    import binance  # noqa: F401
except ImportError:
    binance_module = types.ModuleType("binance")
    client_module = types.ModuleType("binance.client")
    exceptions_module = types.ModuleType("binance.exceptions")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

    class BinanceAPIException(Exception):
        pass

    client_module.Client = Client
    exceptions_module.BinanceAPIException = BinanceAPIException
    binance_module.client = client_module
    binance_module.exceptions = exceptions_module

    sys.modules["binance"] = binance_module
    sys.modules["binance.client"] = client_module
    sys.modules["binance.exceptions"] = exceptions_module

# python-telegram-bot tampoco está instalado en este entorno; telegram_listener_bot
# sólo necesita poder construir un Application sin conectarse a nada para que sus
# métodos de parseo (puro código, sin red) sean testeables de forma aislada.
try:
    import telegram  # noqa: F401
except ImportError:
    telegram_module = types.ModuleType("telegram")
    telegram_ext_module = types.ModuleType("telegram.ext")

    class Update:
        pass

    class _ApplicationBuilder:
        def token(self, *args, **kwargs):
            return self

        def build(self):
            return object()

    class Application:
        @staticmethod
        def builder():
            return _ApplicationBuilder()

    class MessageHandler:
        def __init__(self, *args, **kwargs):
            pass

    class CallbackQueryHandler:
        def __init__(self, *args, **kwargs):
            pass

    class _Filters:
        TEXT = object()

        @staticmethod
        def Chat(*args, **kwargs):
            return object()

    class ContextTypes:
        DEFAULT_TYPE = None

    telegram_module.Update = Update
    telegram_ext_module.Application = Application
    telegram_ext_module.MessageHandler = MessageHandler
    telegram_ext_module.CallbackQueryHandler = CallbackQueryHandler
    telegram_ext_module.filters = _Filters
    telegram_ext_module.ContextTypes = ContextTypes

    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.ext"] = telegram_ext_module
