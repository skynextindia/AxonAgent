"""Risk management and drawdown circuit breaker for AxonAI.

Monitors daily profit/loss and disables order placement if limits are exceeded.
"""

import os
import json
import logging
from datetime import date

logger = logging.getLogger(__name__)


class RiskGuard:
    """Drawdown Circuit Breaker.
    
    Tracks daily profit/loss and halts all execution if daily drawdown exceeds threshold.
    """

    def __init__(self, config: dict):
        self.config = config
        self.max_daily_drawdown_pct = config.get("risk_max_daily_drawdown_pct", 5.0)  # default 5%
        self.max_daily_loss_amount = config.get("risk_max_daily_loss_amount", 500.0) # default $500
        self.risk_pnl_log_file = "reports/daily_pnl.json"
        
        # Load daily PnL
        self.daily_pnl = self._load_daily_pnl()

    def _load_daily_pnl(self) -> dict:
        if os.path.exists(self.risk_pnl_log_file):
            try:
                with open(self.risk_pnl_log_file, "r") as f:
                    data = json.load(f)
                    if data.get("date") == str(date.today()):
                        return data
            except Exception as e:
                logger.error("RiskGuard: Failed to load daily PnL log: %s", e)
        return {"date": str(date.today()), "start_equity": 0.0, "realized_pnl": 0.0}

    def _save_daily_pnl(self):
        os.makedirs(os.path.dirname(self.risk_pnl_log_file), exist_ok=True)
        try:
            with open(self.risk_pnl_log_file, "w") as f:
                json.dump(self.daily_pnl, f)
        except Exception as e:
            logger.error("RiskGuard: Failed to save daily PnL: %s", e)

    def update_equity(self, current_equity: float, current_balance: float):
        """Seed daily starting equity on first call of the day."""
        if self.daily_pnl["date"] != str(date.today()):
            self.daily_pnl = {"date": str(date.today()), "start_equity": current_equity, "realized_pnl": 0.0}
            self._save_daily_pnl()
        elif self.daily_pnl["start_equity"] == 0.0:
            self.daily_pnl["start_equity"] = current_equity
            self._save_daily_pnl()

    def record_trade_result(self, pnl: float):
        """Update realized PnL for the day."""
        if self.daily_pnl["date"] != str(date.today()):
            self.daily_pnl = {"date": str(date.today()), "start_equity": 0.0, "realized_pnl": 0.0}
            
        self.daily_pnl["realized_pnl"] += pnl
        self._save_daily_pnl()

    def is_halted(self, current_equity: float) -> tuple[bool, str]:
        """Check if circuit breaker has tripped."""
        if self.daily_pnl["date"] != str(date.today()):
            return False, ""
            
        start_eq = self.daily_pnl["start_equity"]
        if start_eq == 0.0:
            return False, ""

        # Drawdown calculation (unrealized + realized)
        floating_loss = start_eq - current_equity
        realized_loss = -self.daily_pnl["realized_pnl"]
        
        # Absolute drawdown pct
        drawdown_pct = (floating_loss / start_eq) * 100.0 if start_eq > 0 else 0.0
        
        # Check against daily percentage limit
        if drawdown_pct >= self.max_daily_drawdown_pct:
            msg = f"Daily drawdown ({drawdown_pct:.2f}%) exceeds limit ({self.max_daily_drawdown_pct}%)"
            logger.warning("RiskGuard: %s", msg)
            return True, msg
            
        # Check against daily amount limit (combined floating + realized)
        total_loss = floating_loss + realized_loss
        if total_loss >= self.max_daily_loss_amount:
            msg = f"Daily loss (${total_loss:.2f}) exceeds limit (${self.max_daily_loss_amount:.2f})"
            logger.warning("RiskGuard: %s", msg)
            return True, msg
            
        return False, ""
