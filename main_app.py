from server.api import create_app
from engine.engine import TradingEngine

engine = TradingEngine()
app = create_app(engine)