from typing import Optional, Dict, List
from datetime import datetime, timedelta

from engine.models import (
    EngineState,
    Position,
    Leg,
    EngineStatus,
    Preview,
)

VALID_TRANSITIONS = {
    EngineState.IDLE:        {EngineState.PREVIEWING},
    EngineState.PREVIEWING: {EngineState.IDLE, EngineState.OPEN},
    EngineState.OPEN:       {EngineState.CLOSING},
    EngineState.CLOSING:    {EngineState.IDLE},
    EngineState.ERROR:      {EngineState.IDLE},
}

class InvalidStateTransition(Exception):
    """Raised when an illegal state transition is attempted"""
    pass

class PreviewExpiredError(Exception):  # 
    """Raised when attempting to execute an expired preview"""
    pass

class PositionAlreadyOpenError(Exception):
    """Raised when attempting to open a position when one is already active"""
    pass    

class RiskCheckFailedError(Exception):
    """Raised when a risk check fails"""
    pass

class TradingEngine:
    """
    Core trading engine.
    Manual-first, single-position, rule-enforced.
    """

    PREVIEW_TTL = 30 #seconds

    def __init__(self):
        self.state: EngineState = EngineState.IDLE
        self.active_position: Optional[Position] = None
        self.history: List[Position] = []
        self.last_update_ts: Optional[datetime] = None
        self.preview_legs : Optional[List[Leg]] = None
        self._preview_created_at: Optional[datetime] = None
        self.status = EngineStatus(
            engine_state=self.state,
            mode="manual-only",
            position=self.active_position,
            preview=None,
            last_update_ts=self.last_update_ts,
            net_pnl=0.0,
        )

    def _transition(self, new_state: EngineState):
        allowed = VALID_TRANSITIONS[self.state]
        if new_state not in allowed:
            raise InvalidStateTransition(
                f"{self.state.value} -> {new_state.value} not allowed"
            )
        self.state = new_state
        self.last_update_ts = datetime.utcnow()

    def _ensure_no_open_position(self): 
        """
        Ensures no position is currently open.
        This is the single source of truth for the one-position-only rule.
        """
        if self.active_position is not None:
            raise PositionAlreadyOpenError(
                f"Cannot proceed: position {self.active_position.id} is already open"
            )

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
        Stores preview and transitions to PREVIEWING state.
        """
        if self.status.engine_state != EngineState.IDLE:
            raise RuntimeError(f"Cannot preview: engine in {self.state.value} state")

        preview = Preview(
            legs=legs,
            max_loss=risk_report.get("max_loss"),
            estimated_margin=risk_report.get("estimated_margin"),
            expires_at=datetime.utcnow() + timedelta(seconds=self.PREVIEW_TTL)
        )

        self._ensure_no_open_position()

        risk_report = self._run_risk_checks(legs)

        # Store the preview
        self.status.preview = preview
        self.status.engine_state = EngineState.PREVIEWING
        self._preview_created_at = datetime.utcnow()
        self._transition(EngineState.PREVIEWING)

        return {
            "allowed": risk_report["allowed"],
            "reason": risk_report.get("reason"),
            "estimated_margin": risk_report.get("estimated_margin"),
            "max_loss": risk_report.get("max_loss"),
            "expires_at": self._preview_created_at + timedelta(seconds=self.PREVIEW_TTL) if self._preview_created_at else None,
            "ttl_seconds": self.PREVIEW_TTL,
        }
    def execute_manual_trade(self) -> Position:  #  NO LEGS PARAMETER
        """
        Executes the previewed trade.
        Must be in PREVIEWING state with stored preview.
        """
        if self.state != EngineState.PREVIEWING:
            raise RuntimeError(f"Cannot execute: no preview (state: {self.state.value})")
        
        if not self.preview_legs:
            raise RuntimeError("No preview stored")

        
        # ADD EXPIRY CHECK
        now = datetime.utcnow()
        preview_age = (now - self._preview_created_at).total_seconds() if self._preview_created_at else None

        if preview_age and preview_age > self.PREVIEW_TTL:
            #clear the expired preview
            self.preview_legs = None
            self._preview_created_at = None
            self._transition(EngineState.IDLE)
            raise PreviewExpiredError("Preview has expired. Please create a new preview.")

        # Use the stored preview
        legs = self.preview_legs
        
        risk_report = self._run_risk_checks(legs)
        if not risk_report["allowed"]:
            raise RuntimeError(f"Trade rejected: {risk_report['reason']}")

        position = Position(
            id=self.generate_position_id(),
            legs=legs,
            entry_time=datetime.utcnow(),
        )

        self.active_position = position
        
        # Clear the preview
        self.status.preview = None
        self.status.engine_state = EngineState.IDLE
        self._preview_created_at = None
        
        self._transition(EngineState.OPEN)
        self.last_update_ts = datetime.utcnow()

        return position
    
    
    def cancel_preview(self):
        """
        Cancels the current preview and returns to IDLE.
        """
        if self.state != EngineState.PREVIEWING:
            raise RuntimeError(f"No preview to cancel (state: {self.state.value})")
        
        self.status.preview = None
        self.status.engine_state = EngineState.IDLE
        self._transition(EngineState.IDLE)
        self.last_update_ts = datetime.utcnow()
        self._preview_created_at = None


    # POSITION MANAGEMENT

    def on_market_update(self, market_snapshot: Dict):
        """
        Called periodically (or via stream).
        Updates P&L and checks exit / adjustment rules.
        """
        if self.state != EngineState.OPEN:
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
        self._transition(EngineState.IDLE)
        self.last_update_ts = datetime.utcnow()

        return position

    """
    def evaluate_risk(self, legs: List[Leg]) -> Dict:
        
        # Central risk gate.
        # Same logic used for preview AND execution.

        # Placeholder — logic will be added later
        return {
            "allowed": True,
            "estimated_margin": None,
            "max_loss": None,
        }
    
    """

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

    # RISK GATE (SINGLE SOURCE OF TRUTH)

    def _run_risk_checks(self, legs: List[Leg]) -> Dict:
        """
        Central risk gate - ONE method for ALL trades.
        Used by both manual and (future) automated trades.
        
        Returns risk report with validation results.
        Raises RiskCheckFailedError if any check fails.
        """
        try:
            # Run all risk checks
            self._check_leg_structure(legs)
            max_loss = self._check_max_loss(legs)
            margin = self._check_margin(legs)
            
            return {
                "allowed": True,
                "estimated_margin": margin,
                "max_loss": max_loss,
                "reason": None,
            }
        
        except RiskCheckFailedError as e:
            return {
                "allowed": False,
                "estimated_margin": None,
                "max_loss": None,
                "reason": str(e),
            }

    def _check_leg_structure(self, legs: List[Leg]):
        """
        Validates the structure of the legs.
        - At least one leg
        - All legs have required fields
        - Valid option types, strikes, expiries
        """
        if not legs:
            raise RiskCheckFailedError("Trade must have at least one leg")
        
        for i, leg in enumerate(legs):
            if not leg.symbol:
                raise RiskCheckFailedError(f"Leg {i}: missing symbol")
            if leg.lots <= 0:
                raise RuntimeError(f"Leg {i}: lots must be > 0")
            # Add more validations as needed
            # - Strike > 0 for options
            # - Valid expiry date
            # - Supported option types
        
        # Placeholder for more complex validations
        pass

    def _check_max_loss(self, legs: List[Leg]) -> Optional[float]:
        """
        Calculates and validates maximum possible loss.
        Returns the max loss value.
        Raises RiskCheckFailedError if loss exceeds limits.
        """
        # Placeholder - implement actual max loss calculation
        # For now, return None to indicate calculation not implemented
        
        # Example implementation structure:
        # max_loss = calculate_max_loss_for_strategy(legs)
        # MAX_LOSS_LIMIT = 10000  # example
        # if max_loss > MAX_LOSS_LIMIT:
        #     raise RiskCheckFailedError(f"Max loss ${max_loss} exceeds limit ${MAX_LOSS_LIMIT}")
        # return max_loss
        
        return None

    def _check_margin(self, legs: List[Leg]) -> Optional[float]:
        """
        Calculates and validates margin requirements.
        Returns the estimated margin.
        Raises RiskCheckFailedError if margin exceeds limits.
        """
        # Placeholder - implement actual margin calculation
        # For now, return None to indicate calculation not implemented
        
        # Example implementation structure:
        # margin = calculate_margin_requirement(legs)
        # MARGIN_LIMIT = 50000  # example
        # if margin > MARGIN_LIMIT:
        #     raise RiskCheckFailedError(f"Margin ${margin} exceeds limit ${MARGIN_LIMIT}")
        # return margin
        
        return None