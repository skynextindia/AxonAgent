import re

# 1. research_manager.py
with open('axonai/agents/managers/research_manager.py', 'r', encoding='utf-8') as f:
    rm_content = f.read()

# Replace structured_llm assignment
rm_content = re.sub(r'structured_llm\s*=\s*bind_structured\(llm,\s*ResearchPlan,\s*"Research Manager"\)', 
                    r'structured_llm = llm.with_structured_output(MungerVerdict)', rm_content)

# Replace the invoke_structured_or_freetext call
invoke_pattern = r'investment_plan\s*=\s*invoke_structured_or_freetext\(.*?\)'
replacement = '''try:
            investment_plan = structured_llm.invoke(prompt)
            investment_plan_dict = investment_plan.dict() if hasattr(investment_plan, "dict") else investment_plan
        except Exception as e:
            investment_plan_dict = {
                "direction": "HOLD",
                "confidence": 0,
                "bull_score": 0,
                "bear_score": 0,
                "key_conflict": "Error: structured output failed",
                "missing_assumption": str(e),
                "supporting_arguments": [],
                "opposing_arguments": [],
                "overall_confidence": 0
            }
        investment_plan = investment_plan_dict'''
rm_content = re.sub(invoke_pattern, replacement, rm_content, flags=re.DOTALL)
rm_content = rm_content.replace('from axonai.agents.schemas import ResearchPlan, render_research_plan', 'from axonai.agents.schemas import MungerVerdict')

with open('axonai/agents/managers/research_manager.py', 'w', encoding='utf-8') as f:
    f.write(rm_content)

# 2. trader.py
with open('axonai/agents/trader/trader.py', 'r', encoding='utf-8') as f:
    tr_content = f.read()

tr_content = re.sub(r'structured_llm\s*=\s*bind_structured\(llm,\s*TraderHypothesisModel,\s*"Trader"\)', 
                    r'structured_llm = llm.with_structured_output(TudorExecution)', tr_content)

invoke_pattern = r'hypothesis_data\s*=\s*invoke_structured_or_freetext\(.*?\)'
replacement = '''try:
            hypothesis_data = structured_llm.invoke(messages)
            hypothesis_data = hypothesis_data.dict() if hasattr(hypothesis_data, "dict") else hypothesis_data
        except Exception as e:
            hypothesis_data = {
                "direction": "HOLD",
                "entry": 0.0,
                "sl": 0.0,
                "tp": 0.0,
                "lot_size": 0.0,
                "sl_pips": 0.0,
                "tp_pips": 0.0,
                "rr_ratio": 0.0,
                "hypothesis": "Structured output failed"
            }'''
tr_content = re.sub(invoke_pattern, replacement, tr_content, flags=re.DOTALL)
tr_content = "from axonai.agents.schemas import TudorExecution\n" + tr_content
# Remove TraderHypothesisModel definition
tr_content = re.sub(r'class TraderHypothesisModel\(BaseModel\):.*?hypothesis: str.*?\"\)\n', '', tr_content, flags=re.DOTALL)

with open('axonai/agents/trader/trader.py', 'w', encoding='utf-8') as f:
    f.write(tr_content)


# 3. portfolio_manager.py
with open('axonai/agents/managers/portfolio_manager.py', 'r', encoding='utf-8') as f:
    pm_content = f.read()

pm_content = re.sub(r'structured_llm\s*=\s*bind_structured\(llm,\s*PortfolioDecision,\s*"Portfolio Manager"\)', 
                    r'structured_llm = llm.with_structured_output(DruckenmillerDecision)', pm_content)

invoke_pattern = r'final_trade_decision\s*=\s*invoke_structured_or_freetext\(.*?\)'
replacement = '''try:
            final_trade_decision = structured_llm.invoke(prompt)
            final_trade_decision = final_trade_decision.dict() if hasattr(final_trade_decision, "dict") else final_trade_decision
        except Exception as e:
            final_trade_decision = {
                "execute": False,
                "direction": "HOLD",
                "final_lot_size": 0.0,
                "confidence": 0,
                "reason": "Structured output failed",
                "abort_reason": "system_error"
            }'''
pm_content = re.sub(invoke_pattern, replacement, pm_content, flags=re.DOTALL)
pm_content = pm_content.replace('from axonai.agents.schemas import PortfolioDecision, render_pm_decision', 'from axonai.agents.schemas import DruckenmillerDecision')

with open('axonai/agents/managers/portfolio_manager.py', 'w', encoding='utf-8') as f:
    f.write(pm_content)

print("Updated structured outputs")
