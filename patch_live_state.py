import sys
from datetime import datetime

file_path = r'd:\work\TradingAgents\axonai\realtime\live_state.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

target = '''        self._state = build_world_state(self.symbol)
        self._initialized = True'''

replacement = '''        self._state = build_world_state(self.symbol)
        
        # Force session penalty to respect daemon config immediately
        from datetime import datetime
        self._update_session(datetime.utcnow())
        
        self._initialized = True'''

if target in content:
    content = content.replace(target, replacement, 1)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Success")
else:
    print("Target not found")
