from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import List
from dataclasses import asdict
from engine.engine import TradingEngine
from engine.models import Leg
from server.schemas import engine_status_to_response, EngineStatusResponse

def create_app(engine: TradingEngine) -> FastAPI:
    app = FastAPI(
        title="Trading Bot API",
        description="API for managing and monitoring trading bots.",
        version="0.1.0"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def root():
        return {"message": "Trading Bot API is running."}

   
    @app.get("/status", response_model=EngineStatusResponse)
    def get_status():
        status = engine.get_status()
        return engine_status_to_response(status)

    @app.post("/manual_trade/preview")
    def manual_trade_preview(legs: List[Leg]):
        return engine.preview_manual_trade(legs)

    @app.post("/manual_trade/execute")
    def manual_trade_execute(legs: List[Leg]):
        return engine.execute_manual_trade(legs)

    @app.post("/position/close")
    def close_position(reason: str = "manual"):
        return engine.close_position(reason)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        await ws.send_json(engine.get_status().model_dump())

        try:
            while True:
                await ws.receive_text()
                await ws.send_json(engine.get_status().model_dump())
        except Exception:
            await ws.close()

    return app
