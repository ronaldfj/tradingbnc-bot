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
