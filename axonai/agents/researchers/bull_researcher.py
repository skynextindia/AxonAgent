from axonai.agents.utils.agent_utils import get_language_instruction


AGENT_NAME = "BUFFETT"
AGENT_IDENTITY = "AxonAI bull case researcher. Finds the strongest reasons the proposed trade will succeed. Argues from evidence provided — not from general market knowledge."

def create_bull_researcher(llm):
    def bull_node(state) -> dict:
        trader_hypothesis = state.get("trader_hypothesis", {})
        compressed_evidence = state.get("compressed_evidence", "")
        asset_type = state.get("asset_type", "stock")

        direction = trader_hypothesis.get("direction", "?") if isinstance(trader_hypothesis, dict) else "?"
        hypothesis_str = trader_hypothesis.get("hypothesis", "") if isinstance(trader_hypothesis, dict) else str(trader_hypothesis)

        prompt = f"""You are BUFFETT — AxonAI bull case researcher.

TRADER HYPOTHESIS: {direction} — {hypothesis_str}

COMPRESSED EVIDENCE:
{compressed_evidence}

Rules:
- Argue FROM the evidence above only — no general market knowledge
- Find 3 distinct bull reasons with specific data references
- Do not invent data
- Max 200 words total

Output ONLY this JSON, nothing else:
{{"position": "bull", "confidence": 0-100, "arguments": ["arg1", "arg2", "arg3"], "key_risk": "one sentence"}}""" + get_language_instruction()

        response = llm.invoke(prompt)
        argument = f"Bull Analyst: {response.content}"

        return {
            "investment_debate_state": {
                "bull_history": argument,
                "history": argument,
                "current_response": argument,
                "count": 1
            }
        }

    return bull_node

