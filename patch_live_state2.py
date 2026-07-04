import sys

file_path = r'd:\work\TradingAgents\axonai\realtime\live_state.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

target = '''        # Force session penalty to respect daemon config immediately
        from datetime import datetime
        self._update_session(datetime.utcnow())
        
        self._initialized = True'''

replacement = '''        # Force session penalty to respect daemon config immediately
        if self._state.session == "asian":
            self._state.session_penalty = 0.25 if self.config.get("realtime_suppress_asian", True) else 1.0
            # Also recompute belief and should_run_graph manually!
            gated = self._state.belief_score * self._state.session_penalty
            base_threshold = self.config.get("realtime_belief_threshold", 0.60)
            self._state.should_run_graph = gated > (base_threshold - 0.05) # approximation
        
        self._initialized = True'''

if target in content:
    content = content.replace(target, replacement, 1)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Success")
else:
    print("Target not found")
