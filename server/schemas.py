from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime
from engine.models import Leg, Position, EngineStatus

class LegRequest(BaseModel):
    symbol: str = Field(..., example="NIFTY")
    option_type: str = Field(..., example="CE")
    side: str = Field(..., example="SELL")
    entry_price: float = Field(..., gt=0)
    lots: int = Field(..., gt=0)

class LegResponse(BaseModel):
    symbol: str
    option_type: str
    side: str
    entry_price: float
    ltp: float
    lots: int
    pnl: float

class ManualTradePreviewRequest(BaseModel):
    legs: List[LegRequest]

class ManualTradePreviewResponse(BaseModel):
    ok: bool
    reason: Optional[str] = None
    estimated_margin: Optional[float] = None
    max_loss: Optional[float] = None

class ManualTradeExecuteRequest(BaseModel):
    legs: List[LegRequest]
    preview_id: Optional[str] = None  # future-proofing

class ManualTradeExecuteResponse(BaseModel):
    message: str
    position_id: str

class PositionResponse(BaseModel):
    id: str
    source: str
    structure: str
    strike: Optional[float]
    net_pnl: float
    entry_ts: str
    exit_ts: Optional[str]
    monitoring: bool
    legs: Dict[str, LegResponse]

class EngineStatusResponse(BaseModel):
    engine_state: str
    mode: str
    position: Optional[PositionResponse]
    last_update_ts: Optional[datetime]
    net_pnl: float

def leg_request_to_model(req: LegRequest) -> Leg:
    return Leg(
        symbol=req.symbol,
        option_type=req.option_type,
        side=req.side,
        entry_price=req.entry_price,
        ltp=req.entry_price,  # initial LTP = entry
        lots=req.lots,
    )

def position_to_response(position: Position) -> PositionResponse:
    return PositionResponse(
        id=position.id,
        source=position.source,
        structure=position.structure,
        strike=position.strike,
        net_pnl=position.net_pnl,
        entry_ts=position.entry_ts,
        exit_ts=position.exit_ts,
        monitoring=position.monitoring,
        legs={
            k: LegResponse(**vars(v))
            for k, v in position.legs.items()
        },
    )

def engine_status_to_response(status: EngineStatus) -> EngineStatusResponse:
    return EngineStatusResponse(
        engine_state=status.engine_state.value,
        mode=status.mode,
        position=position_to_response(status.position)
        if status.position else None,
        last_update_ts=status.last_update_ts,
        net_pnl=status.net_pnl,
    )
