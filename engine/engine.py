from typing import Optional, Dict, List
from datetime import datetime

from engine.models import (
    EngineState,
    Position,
    Leg,
    EngineStatus,
)

class TradingEngine:
    """
    Core trading engine.
    Manual-first, single-position, rule-enforced.
    """

    def __init__(self):
        self.state: EngineState = EngineState.IDLE
        self.active_position: Optional[Position] = None
        self.history: List[Position] = []
        self.last_update_ts: Optional[datetime] = None

    # ENGINE STATUS
    def get_status(self) -> EngineStatus:
        """
        Returns a snapshot of current engine state.
        Used by /status endpoint and WebSocket.
        """
        return EngineStatus(
            engine_state=self.state,
            mode="manual-only",
            position=self.active_position,
            last_update_ts=self.last_update_ts,
            net_pnl= self.active_position.net_pnl if self.active_position else 0
        )

    # MANUAL TRADE FLOW
    def preview_manual_trade(self, legs: List[Leg]) -> Dict:
        """
        Dry-run a manual trade.
        Does NOT change engine state.

        Used by /manual_trade/preview
        """
        self.assert_can_enter_trade()

        risk_report = self.evaluate_risk(legs)

        return {
            "allowed": risk_report["allowed"],
            "reason": risk_report.get("reason"),
            "estimated_margin": risk_report.get("estimated_margin"),
            "max_loss": risk_report.get("max_loss"),
        }

    def execute_manual_trade(self, legs: List[Leg]) -> Position:
        """
        Executes a manually approved trade.
        This is the ONLY way a position can be created.
        """
        self.assert_can_enter_trade()

        risk_report = self.evaluate_risk(legs)
        if not risk_report["allowed"]:
            raise RuntimeError(f"Trade rejected: {risk_report['reason']}")

        position = Position(
            id=self.generate_position_id(),
            legs=legs,
            entry_time=datetime.utcnow(),
        )

        self.active_position = position
        self.state = EngineState.IN_POSITION
        self.last_update_ts = datetime.utcnow()

        return position 
    # POSITION MANAGEMENT

    def on_market_update(self, market_snapshot: Dict):
        """
        Called periodically (or via stream).
        Updates P&L and checks exit / adjustment rules.
        """
        if self.state != EngineState.IN_POSITION:
            return

        if not self.active_position:
            return

        self.update_pnl(market_snapshot)
        self.evaluate_position_rules(market_snapshot)

        self.last_update_ts = datetime.utcnow()

    def close_position(self, reason: str = "manual") -> Position:
        """
        Closes the active position.
        Used by /position/close or risk triggers.
        """
        if not self.active_position:
            raise RuntimeError("No active position to close")

        position = self.active_position
        position.exit_time = datetime.utcnow()
        position.exit_reason = reason

        self.history.append(position)

        self.active_position = None
        self.state = EngineState.IDLE
        self.last_update_ts = datetime.utcnow()

        return position

    # INTERNAL HELPERS (NO SIDE EFFECTS OUTSIDE ENGINE)
    def assert_can_enter_trade(self):
        if self.state != EngineState.IDLE:
            raise RuntimeError("Engine already has an active position")

    def evaluate_risk(self, legs: List[Leg]) -> Dict:
        """
        Central risk gate.
        Same logic used for preview AND execution.
        """
        # Placeholder — logic will be added later
        return {
            "allowed": True,
            "estimated_margin": None,
            "max_loss": None,
        }

    def update_pnl(self, market_snapshot: Dict):
        """
        Updates unrealized P&L for the active position.
        """
        # Placeholder
        pass

    def evaluate_position_rules(self, market_snapshot: Dict):
        """
        Checks SL, profit target, adjustments, expiry, etc.
        """
        # Placeholder
        pass

    def generate_position_id(self) -> str:
        return f"POS-{int(datetime.utcnow().timestamp())}"
