from engine.engine import TradingEngine
from server.api import create_app

engine = TradingEngine()

app = create_app(engine)