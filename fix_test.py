import re

with open('tests/test_structured_agents.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('from axonai.agents.trader.trader import create_trader, TraderHypothesisModel', 'from axonai.agents.trader.trader import create_trader\nfrom axonai.agents.schemas import TudorExecution as TraderHypothesisModel')

with open('tests/test_structured_agents.py', 'w', encoding='utf-8') as f:
    f.write(content)
