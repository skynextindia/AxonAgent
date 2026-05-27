from axonai.agents.utils.agent_utils import get_language_instruction


AGENT_NAME = "SOROS"
AGENT_IDENTITY = "AxonAI bear case researcher. Finds the strongest structural weaknesses and failure modes in the proposed trade. Argues specifically against the bull case."

def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        trader_hypothesis = state.get("trader_hypothesis", {})
        compressed_evidence = state.get("compressed_evidence", "")
        asset_type = state.get("asset_type", "stock")

        direction = trader_hypothesis.get("direction", "?") if isinstance(trader_hypothesis, dict) else "?"

        # Get BUFFETT's actual arguments to counter
        bull_history = state.get("investment_debate_state", {}).get("bull_history", "")

        prompt = f"""You are SOROS — AxonAI bear case researcher.

TRADER HYPOTHESIS: {direction}

BUFFETT BULL CASE:
{bull_history}

COMPRESSED EVIDENCE:
{compressed_evidence}

Rules:
- Attack BUFFETT's specific arguments above with counter-evidence
- Do not argue against trading in general — argue against THIS trade
- Find hidden risks and structural vulnerabilities
- Max 200 words total

Output ONLY this JSON, nothing else:
{{"position": "bear", "confidence": 0-100, "arguments": ["counter1", "counter2", "counter3"], "fatal_flaw": "one sentence"}}""" + get_language_instruction()

        response = llm.invoke(prompt)
        argument = f"Bear Analyst: {response.content}"

        return {
            "investment_debate_state": {
                "bear_history": argument,
                "history": argument,
                "current_response": argument,
                "count": 1
            }
        }

    return bear_node

