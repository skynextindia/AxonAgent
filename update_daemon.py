import re

with open('axonai/realtime/daemon.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacement = '''                  AGENT_NAME_MAP = {
                      "Market Analyst": "WYCKOFF",
                      "Fundamentals Analyst": "KEYNES",
                      "News Analyst": "REUTERS",
                      "Sentiment Analyst": "LIVERMORE",
                      "Bull Researcher": "BUFFETT",
                      "Bear Researcher": "SOROS",
                      "Research Manager": "MUNGER",
                      "Trader": "TUDOR",
                      "Aggressive Analyst": "SIMONS",
                      "Conservative Analyst": "DALIO",
                      "Neutral Analyst": "MARKS",
                      "Portfolio Manager": "DRUCKENMILLER"
                  }
                  
                  for node, content_val in chunk.items():'''
content = re.sub(r'for node,\s*content\s*in\s*chunk\.items\(\):', replacement, content)

content = content.replace('"agent_name": node.replace("_", " ").title(),', '"agent_name": AGENT_NAME_MAP.get(node, node),')

with open('axonai/realtime/daemon.py', 'w', encoding='utf-8') as f:
    f.write(content)
