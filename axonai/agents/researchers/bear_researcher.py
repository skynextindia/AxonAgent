from axonai.agents.utils.agent_utils import get_language_instruction


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        trader_hypothesis = state.get("trader_hypothesis", {})
        compressed_evidence = state.get("compressed_evidence", "")
        asset_type = state.get("asset_type", "stock")

        prompt = f"""You are a Bear Analyst. Your task is to build the strongest possible case OPPOSING the Trader's proposed hypothesis using the compressed market evidence.

## Proposed Trader Hypothesis:
- **Direction**: {trader_hypothesis.get('direction')}
- **Entry**: {trader_hypothesis.get('entry')}
- **Stop Loss**: {trader_hypothesis.get('sl')}
- **Take Profit**: {trader_hypothesis.get('tp')}
- **Hypothesis**: {trader_hypothesis.get('hypothesis')}

## Compressed Analyst Evidence:
{compressed_evidence}

Provide a robust, data-backed bearish argument challenging this hypothesis. Focus on downside risks, technical invalidation levels, macro headwinds, and potential pitfalls. Counter potential bullish points explicitly.
""" + get_language_instruction()

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

