from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict
from datetime import datetime
import uuid


class EngineState(str, Enum):
    """
    Represents the current state of the trading engine.
    Used by API + UI to decide allowed actions.

    """
    IDLE = "IDLE" #No active position or preview
    PREVIEWING = "PREVIEWING" #manual trade preview active
    OPEN = "OPEN" # position is live and open
    CLOSING = "CLOSING" # position is in the process of closing
    ERROR = "ERROR" # engine is in an error state   

#Leg model (single option leg)
@dataclass
class Leg:
    """
    Represents ONE option leg (CE or PE)
    """

    symbol: str     #underlying symbol, e.g. "NIFTY"
    option_type: str  # "CE" or "PE"
    side : str     # "BUY" or "SELL"
    entry_price: float # price at which leg was entered
    ltp: float    # current last traded price
    lots: int    # number of lots
    pnl: float = 0.0  # profit and loss for this leg

@dataclass
class Position:
    """
   Represents a full trade (what trader considers ONE position)
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    source: str = "manual"  # "manual" or "auto"
    structure: str = ""  # e.g. "straddle", ce, pe, etc"
    strike : Optional[float] = None # strike price for the position

    legs : Dict[str, Leg] = field(default_factory=dict)  # dictionary of legs, e.g. {"ce": Leg(...), "pe": Leg(...)}

    net_pnl: float = 0.0  # total profit and loss for the position
    entry_ts : str = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    exit_ts: Optional[str] = None

    monitoring: bool = False # True when bot is managing this position

    #adjustment_history: List[Adjustment] = field(default_factory=list)

    notes : Optional[str] = None  # any additional notes about the position


@dataclass
class EngineStatus: 
    """
    SnapShot returned by the engine status API
    """
    engine_state: EngineState
    mode : str
    position: Optional[Position] = None
    last_update_ts: Optional[datetime] = None 
    net_pnl: float = 0.0