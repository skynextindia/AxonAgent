with open('axonai/agents/trader/trader.py', 'r', encoding='utf-8') as f:
    tr_content = f.read()

if tr_content.startswith('from axonai.agents.schemas import TudorExecution\n'):
    tr_content = tr_content.replace('from axonai.agents.schemas import TudorExecution\n', '')
    tr_content = tr_content.replace('from __future__ import annotations\n', 'from __future__ import annotations\nfrom axonai.agents.schemas import TudorExecution\n')

with open('axonai/agents/trader/trader.py', 'w', encoding='utf-8') as f:
    f.write(tr_content)
