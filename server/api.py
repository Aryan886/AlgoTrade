from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import uuid

app = FastAPI(
    titl = "Trading Bot API",
    description="API for managing and monitoring trading bots.",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # for local dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ENGINE_STATE = "IDLE" #IDLE | PREVIEWING | OPEN
CURRENT_POSITION = None
LAST_PREVIEW = None

#Mock trade data

def mock_position():
    return {
        "id": str(uuid.uuid4()),
        "source": "manual",
        "structure": "straddle",
        "strike" : 22000,
        "legs": {
            "ce": {
                "symbol": "NIFTY22000CE",
                "entry_price": 150.0,
                "ltp": 200.0,
                "lots": 1,
                "pnl" : 20
            },
            "pe": {
                "symbol": "NIFTY22000PE",
                "entry_price": 130.0,
                "ltp": 180.0,
                "lots": 1,
                "pnl" : 25
            }
        },
        "net_pnl": 45,
        "entry_ts": datetime.now().isoformat(),
        "monitoring" : True,
        "adjustment_history": []
    }

@app.get("/")
def root():
    return {"message": "Trading Bot API is running."}

@app.get("/status")
def get_status():
    return {
        "engine_state": ENGINE_STATE,
        "position": CURRENT_POSITION,
        "mode": "manual-only",
        "net_pnl": CURRENT_POSITION["net_pnl"] if CURRENT_POSITION else 0,
        "last_update_ts": datetime.now().isoformat()
    }

@app.post("/manual_trade/preview")
def manual_trade_preview(payload: dict):
    global ENGINE_STATE, LAST_PREVIEW

    if ENGINE_STATE != "IDLE":
        return{
            "ok": False,
            "reason": "Active position exists"
        }
    
    ENGINE_STATE = "PREVIEWING"
    LAST_PREVIEW = {
        "preview_id": str(uuid.uuid4()),
        "strike": 22000,
        "ce_ltp": 45.2,
        "pe_ltp": 38.5,
        "combined_premium": 83.7,
        "estimated_margin": 50000,
        "risk_ok": True,
    }

    return {
        "ok": True,
        "preview_id": LAST_PREVIEW["preview_id"],
        "summary": LAST_PREVIEW
    }

@app.post("/manual_trade/execute")
def manual_trade_execute(payload: dict):
    global ENGINE_STATE, CURRENT_POSITION, LAST_PREVIEW

    if ENGINE_STATE != "PREVIEWING" or not LAST_PREVIEW:
        return {
            "ok": False,
            "reason": "No active preview"
        }
    
    if payload.get("preview_id") != LAST_PREVIEW["preview_id"]:
        return {
            "ok": False,
            "reason": "Preview ID mismatch"
        }
    
    ENGINE_STATE = "OPEN"
    CURRENT_POSITION = mock_position()
    LAST_PREVIEW = None

    return {
        "ok": True,
        "position": CURRENT_POSITION,
        "position_id": CURRENT_POSITION["id"]
    }

@app.post("/position/close")
def close_position(payload: dict):
    global ENGINE_STATE, CURRENT_POSITION

    if ENGINE_STATE != "OPEN" or not CURRENT_POSITION:
        return {
            "ok": False,
            "reason": "No active position to close"
        }
    
    position_id = payload.get("position_id")
    if position_id != CURRENT_POSITION["id"]:
        return {
            "ok": False,
            "reason": "Position ID mismatch"
        }
    
    closing_pnl = CURRENT_POSITION["net_pnl"]
    ENGINE_STATE = "IDLE"
    CURRENT_POSITION = None

    return {
        "ok": True,
        "closing_pnl": closing_pnl
    }

@app.get("/history")
def trade_history():
    return [
        {
            "position_id": str(uuid.uuid4()),
            "source": "manual",
            "structure": "straddle",
            "strike" : 21500,
            "entry_ts": "2025-12-12T10:15:00",
            "exit_ts": "2025-12-12T11:05:00",
            "realized_pnl": 1200
        }
    ]

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    await ws.send_json({
        "type": "INITIAL_STATE",
        "payload": {
            "engine_state": ENGINE_STATE,
            "position": CURRENT_POSITION,
        }
    })

    try:
        while True:
            await ws.receive_text()

            #send fake PnL updates 
            if CURRENT_POSITION:
                await ws.send_json({
                    "type": "POSITION_UPDATE",
                    "payload": {
                        "net_pnl": CURRENT_POSITION["net_pnl"] + 5,  #mock increment
                        "ts": datetime.now().isoformat()
                    }
                })
    except Exception as e:
        await ws.close()