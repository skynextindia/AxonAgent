from axonai.agents.utils.agent_utils import get_language_instruction


AGENT_NAME = "SOROS"
AGENT_IDENTITY = "AxonAI bear case researcher. Finds the strongest structural weaknesses and failure modes in the proposed trade. Argues specifically against the bull case."

def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        trader_hypothesis = state.get("trader_hypothesis", {})
        compressed_evidence = state.get("compressed_evidence", "")
        asset_type = state.get("asset_type", "stock")

        prompt = """You are SOROS — AxonAI bear case researcher. Your job is to find the strongest structural weaknesses and failure modes in the proposed trade.

Critical rules:
- You must argue specifically AGAINST the bull case BUFFETT presented
- Do not argue against trading in general — argue against THIS specific trade
- Reference specific weaknesses in BUFFETT's arguments
- Find hidden risks, conflicting signals, and structural vulnerabilities
- Even if evidence leans bullish find the strongest possible bear case
- Be specific — attack BUFFETT's specific claims with counter-evidence

Maximum 200 words total.

Respond with this exact JSON at the end of your response:
{"position": "bear", "confidence": 0-100, "arguments": ["counter to buffett arg1", "counter to buffett arg2", "counter to buffett arg3"], "fatal_flaw": "single most likely reason this trade fails"}""" + get_language_instruction()

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

