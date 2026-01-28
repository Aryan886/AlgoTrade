from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from engine.engine import TradingEngine, PreviewExpiredError, PositionAlreadyOpenError

from server.schemas import (
    EngineStatusResponse,
    engine_status_to_response,
    ManualTradePreviewRequest,
    ManualTradeExecuteRequest,
    ManualTradePreviewResponse,
    leg_request_to_model,
)


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

    # STATUS

    @app.get("/status", response_model=EngineStatusResponse)
    def get_status():
        status = engine.get_status()
        return engine_status_to_response(status)

    # MANUAL TRADE – PREVIEW
    @app.post("/manual_trade/preview", response_model=ManualTradePreviewResponse)
    def manual_trade_preview(req: ManualTradePreviewRequest):
        try:
            legs = [leg_request_to_model(l) for l in req.legs]
            return engine.preview_manual_trade(legs)
        except PositionAlreadyOpenError as e:  
            raise HTTPException(status_code=409, detail=str(e))  # 409 Conflict
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    

    # MANUAL TRADE – EXECUTE
    @app.post("/manual_trade/execute")
    def manual_trade_execute(req: ManualTradeExecuteRequest):
        #legs = [leg_request_to_model(l) for l in req.legs]
        try:
            position = engine.execute_manual_trade()
            return {
                "message": "Trade executed",
                "position_id": position.id,
            }
        except PreviewExpiredError as e:
            raise HTTPException(status_code=410, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # MANUAL TRADE – CANCEL PREVIEW
    @app.post("/manual_trade/cancel_preview")
    def manual_trade_cancel():
        engine.cancel_preview()
        return {
            "message": "Manual trade preview cancelled",
        }

    # POSITION CLOSE
    @app.post("/position/close")
    def close_position(reason: str = "manual"):
        position = engine.close_position(reason)
        return {
            "message": "Position closed",
            "position_id": position.id,
        }

    # WEBSOCKET
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()

        await ws.send_json(
            engine_status_to_response(engine.get_status()).model_dump()
        )

        try:
            while True:
                await ws.receive_text()
                await ws.send_json(
                    engine_status_to_response(engine.get_status()).model_dump()
                )
        except Exception:
            await ws.close()

    return app
