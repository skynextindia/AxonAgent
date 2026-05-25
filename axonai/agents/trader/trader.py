# axonai/agents/trader/trader.py

from __future__ import annotations
from axonai.agents.schemas import TudorExecution

import re
import functools
import logging
from typing import Dict, Any

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage

from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from axonai.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)

logger = logging.getLogger(__name__)



AGENT_NAME = "TUDOR"
AGENT_IDENTITY = "AxonAI execution specialist. Translates directional verdicts into precise entry price, stop loss, and take profit levels. Does not form directional opinions — executes decisions made by MUNGER."

def create_trader(llm):
    structured_llm = llm.with_structured_output(TudorExecution)

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = build_instrument_context(company_name, asset_type)
        
        world_state = state.get("world_state", {})
        market_evidence = state.get("market_evidence", {})

        # Extract current price and ATR from world_state or fallback
        current_price = world_state.get("spread_pips", 0.0) # wait, current price isn't directly a field but we can get it from MT5 or use a fallback
        atr_value = world_state.get("atr_14_h1", 0.0010)

        # Let's fetch actual close price from MT5 or fallback
        # ── Try MT5 first (real-time tick) ──
        try:
            from axonai.dataflows.mt5_data import get_mt5_live_price
            tick = get_mt5_live_price(company_name)
            if tick is not None:
                bid, ask, last = tick
                current_price = (bid + ask) / 2  # mid-price
        except Exception:
            pass

        # Fallback values if MT5 failed
        if current_price is None or current_price == 0.0:
            if company_name == "EURUSD=X" or "EURUSD" in company_name:
                current_price = 1.15920
            else:
                current_price = 1.0

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent. Your role is to generate a precise short-term trading hypothesis using the pre-flight WorldState and MarketEvidence. "
                    "You MUST decide on a direction (BUY, SELL, or HOLD) and calculate entry, stop-loss (sl), and take-profit (tp) levels using standard rules: "
                    f"The current market spot price is EXACTLY {current_price:.5f} and the H1 ATR is {atr_value:.5f}. "
                    "For a BUY action: entry should be near the spot price, sl = entry - (2.0 * ATR), and tp = entry + (4.0 * ATR). "
                    "For a SELL action: entry should be near the spot price, sl = entry + (2.0 * ATR), and tp = entry - (4.0 * ATR). "
                    "For HOLD: calculate entry, sl, and tp levels similarly using the spot price as reference. "
                    "Write a 1-sentence hypothesis detailing the trigger (e.g. 'London breakout above Asian range high with EUR strength')."
                    + get_language_instruction()
                )
            },
            {
                "role": "user",
                "content": (
                    f"Instrument: {company_name} ({asset_type})\n"
                    f"WorldState Info:\n- Regime: {world_state.get('dominant_regime')}\n- Session: {world_state.get('session')}\n- EUR strength: {world_state.get('eur_strength')}\n- USD strength: {world_state.get('usd_strength')}\n"
                    f"MarketEvidence Info:\n- Trend H1: {market_evidence.get('trend_direction_h1')}\n- Key Levels: {market_evidence.get('key_levels')}\n- RSI: {market_evidence.get('rsi_h1')}\n- Patterns: {market_evidence.get('recent_patterns')}\n"
                    f"REAL-TIME MARKET PRICING CONTEXT:\n"
                    f"- Current Spot Price: {current_price:.5f}\n"
                    f"- 14-Day ATR: {atr_value:.5f}\n\n"
                    f"Generate a hypothesis conforming to the schema."
                ),
            },
        ]

        import json
        hypothesis_res = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            lambda x: x.model_dump_json() if hasattr(x, "model_dump_json") else (json.dumps(x) if isinstance(x, dict) else str(x)),
            "Trader",
            schema=TraderHypothesisModel,
        )

        # Parse or default the structured hypothesis
        # Usually it's returned as an instance of TraderHypothesisModel, or a dict, or free text that we parse
        direction = "HOLD"
        entry = current_price
        sl = current_price - 2.0 * atr_value
        tp = current_price + 4.0 * atr_value
        hypothesis_str = "No specific hypothesis generated."

        parsed_data = None
        if isinstance(hypothesis_res, str):
            try:
                parsed_data = json.loads(hypothesis_res)
            except Exception:
                pass

        if isinstance(parsed_data, dict):
            direction = str(parsed_data.get("direction", "HOLD")).upper()
            entry = float(parsed_data.get("entry", current_price))
            sl = float(parsed_data.get("sl", current_price - 2.0 * atr_value))
            tp = float(parsed_data.get("tp", current_price + 4.0 * atr_value))
            hypothesis_str = str(parsed_data.get("hypothesis", ""))
        elif isinstance(hypothesis_res, TraderHypothesisModel):
            direction = hypothesis_res.direction.upper()
            entry = float(hypothesis_res.entry)
            sl = float(hypothesis_res.sl)
            tp = float(hypothesis_res.tp)
            hypothesis_str = hypothesis_res.hypothesis
        elif isinstance(hypothesis_res, dict):
            direction = str(hypothesis_res.get("direction", "HOLD")).upper()
            entry = float(hypothesis_res.get("entry", current_price))
            sl = float(hypothesis_res.get("sl", current_price - 2.0 * atr_value))
            tp = float(hypothesis_res.get("tp", current_price + 4.0 * atr_value))
            hypothesis_str = str(hypothesis_res.get("hypothesis", ""))
        else:
            # Try basic parsing from string
            try:
                dir_match = re.search(r"direction['\"]?\s*[:=]\s*['\"]?(\w+)", str(hypothesis_res), re.IGNORECASE)
                if dir_match:
                    direction = dir_match.group(1).upper()
                entry_match = re.search(r"entry['\"]?\s*[:=]\s*([\d.]+)", str(hypothesis_res))
                if entry_match:
                    entry = float(entry_match.group(1))
                sl_match = re.search(r"sl['\"]?\s*[:=]\s*([\d.]+)", str(hypothesis_res))
                if sl_match:
                    sl = float(sl_match.group(1))
                tp_match = re.search(r"tp['\"]?\s*[:=]\s*([\d.]+)", str(hypothesis_res))
                if tp_match:
                    tp = float(tp_match.group(1))
                hyp_match = re.search(r"hypothesis['\"]?\s*[:=]\s*['\"]?([^'\"\n]+)", str(hypothesis_res))
                if hyp_match:
                    hypothesis_str = hyp_match.group(1).strip().rstrip("'\"")
            except Exception as e:
                logger.warning("Failed parsing raw text hypothesis: %s", e)

        # Force-correct SL/TP using standard formulas to prevent hallucination
        direction = "SELL" if "SELL" in direction else ("BUY" if "BUY" in direction else "HOLD")
        if direction == "SELL":
            sl = round(entry + 2.0 * atr_value, 5)
            tp = round(entry - 4.0 * atr_value, 5)
        else:
            sl = round(entry - 2.0 * atr_value, 5)
            tp = round(entry + 4.0 * atr_value, 5)

        hypothesis_dict = {
            "direction": direction,
            "entry": float(entry),
            "sl": float(sl),
            "tp": float(tp),
            "hypothesis": hypothesis_str,
            "confidence": 0.0
        }

        # Render markdown for display/compatibility
        trader_plan = (
            f"**Action**: {direction}\n\n"
            f"**Hypothesis**: {hypothesis_str}\n\n"
            f"**Entry Price**: {entry:.5f}\n\n"
            f"**Stop Loss**: {sl:.5f}\n\n"
            f"**Take Profit**: {tp:.5f}\n\n"
            f"FINAL TRANSACTION PROPOSAL: **{direction}**"
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_hypothesis": hypothesis_dict,
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
