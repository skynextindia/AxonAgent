from axonai.agents.utils.agent_utils import get_language_instruction


AGENT_NAME = "BUFFETT"
AGENT_IDENTITY = "AxonAI bull case researcher. Finds the strongest reasons the proposed trade will succeed. Argues from evidence provided — not from general market knowledge."

def create_bull_researcher(llm):
    def bull_node(state) -> dict:
        trader_hypothesis = state.get("trader_hypothesis", {})
        compressed_evidence = state.get("compressed_evidence", "")
        asset_type = state.get("asset_type", "stock")

        prompt = """You are BUFFETT — AxonAI bull case researcher. Your job is to find the strongest reasons the proposed trade will succeed.

Critical rules:
- You must argue FROM the compressed evidence provided — not from general market knowledge
- Reference specific data points from WYCKOFF, KEYNES, REUTERS, and LIVERMORE reports
- Find at least 3 distinct reasons supporting the bull case
- Do not invent data not present in the evidence
- Be specific — "H4 trend is bullish" is acceptable. "Markets tend to go up" is not.
- Even if evidence is mixed argue the strongest possible bull case from what exists

Maximum 200 words total.

Respond with this exact JSON at the end of your response:
{"position": "bull", "confidence": 0-100, "arguments": ["argument1 with evidence reference", "argument2 with evidence reference", "argument3 with evidence reference"], "key_risk": "single biggest risk to this bull case"}""" + get_language_instruction()

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

