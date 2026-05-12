from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from datetime import datetime, timedelta
from engine.engine import TradingEngine, PreviewExpiredError, PositionAlreadyOpenError
from utils.db_func import DB_PATH

from engine.models import EngineState, Leg, Preview
from server.schemas import (
    EngineStatusResponse,
    engine_status_to_response,
    ManualTradePreviewRequest,
    ManualTradeExecuteRequest,
    ManualTradePreviewResponse,
    leg_request_to_model,
)
from server.dashboard_router import create_dashboard_router, initialize_dashboard_store


def create_app(engine: TradingEngine, db_path: str = DB_PATH) -> FastAPI:
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

    dashboard_router = create_dashboard_router(engine=engine, db_path=db_path)
    app.include_router(dashboard_router)

    @app.on_event("startup")
    def startup_dashboard_store() -> None:
        initialize_dashboard_store(dashboard_router)

    @app.get("/")
    def root():
        return {"message": "Trading Bot API is running."}

    # STATUS

    @app.get("/status", response_model=EngineStatusResponse)
    def get_status():
        status = engine.get_status()
        return engine_status_to_response(status)

    # MANUAL TRADE – PREVIEW
    """
    
    @app.post("/manual_trade/preview")
    def manual_trade_preview(req: Optional[ManualTradePreviewRequest]):
        try:
            legs = []

            if req and req.legs:
                legs = [leg_request_to_model(l) for l in req.legs]
            engine.preview_manual_trade(legs)
            return { "ok" : True}
        except PositionAlreadyOpenError as e:  
            raise HTTPException(status_code=409, detail=str(e))  # 409 Conflict
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    
    """
    @app.post("/manual_trade/preview")
    def preview_manual_trade(self, legs: list[Leg]) -> None:
        if self.status.engine_state != EngineState.IDLE:
            raise RuntimeError("Cannot preview unless engine is IDLE")

        # ---- SAFE DEFAULTS (always defined) ----
        estimated_margin = 0.0
        max_loss = 0.0

        # ---- ONLY run risk logic if legs exist ----
        if legs:
            risk_report = self._run_risk_checks(legs)

            estimated_margin = risk_report.get("estimated_margin", 0.0)
            max_loss = risk_report.get("max_loss", 0.0)

        # ---- CREATE PREVIEW (always) ----
        preview = Preview(
            legs=legs,
            estimated_margin=estimated_margin,
            max_loss=max_loss,
            expires_at=datetime.now() + timedelta(minutes=2),
        )

        # ---- ATOMIC STATE UPDATE ----
        self.status.preview = preview
        self.status.engine_state = EngineState.PREVIEWING


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
