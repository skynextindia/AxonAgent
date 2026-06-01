import sys

file_path = r'd:\work\TradingAgents\axonai\realtime\daemon.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

daemon_end = '''    @property
    def is_running(self) -> bool:
        return self._running
'''
daemon_insert = '''    @property
    def is_running(self) -> bool:
        return self._running

    def _log_dry_run_event(self, event_type: str, details: dict):
        \"\"\"Append an event to the dry run session log.\"\"\"
        if not self.config.get('realtime_dry_run'):
            return
        import json, os
        from datetime import datetime
        os.makedirs('reports', exist_ok=True)
        log_path = os.path.join('reports', 'dry_run_session.jsonl')
        entry = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'details': details
        }
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\\n')
'''
if daemon_end in content:
    content = content.replace(daemon_end, daemon_insert, 1)
else:
    print('Failed to find insertion point!')
    sys.exit(1)

summary_func = '''
def generate_session_summary():
    \"\"\"Read reports/dry_run_session.jsonl and print a formatted summary.\"\"\"
    import json, os
    from datetime import datetime

    log_path = os.path.join('reports', 'dry_run_session.jsonl')
    if not os.path.exists(log_path):
        print('No dry run session log found.')
        return

    first_time = last_time = None
    ticks = 0
    events_detected = confluence_passes = confluence_fails = graph_fires = 0
    decisions_approved = decisions_rejected = errors = sr_breaches = 0
    rejection_reasons = {}
    level_counts = {}

    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line)
                dt = datetime.fromisoformat(entry['timestamp'])
                if first_time is None: first_time = dt
                last_time = dt

                etype = entry['event_type']
                details = entry.get('details', {})

                if etype == 'error':
                    errors += 1
                elif etype == 'confluence_pass':
                    confluence_passes += 1
                elif etype == 'confluence_fail':
                    confluence_fails += 1
                elif etype == 'graph_fire':
                    graph_fires += 1
                elif etype == 'decision':
                    if details.get('execute'):
                        decisions_approved += 1
                    else:
                        decisions_rejected += 1
                        reason = details.get('abort_reason') or details.get('reason') or 'Unknown'
                        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                elif etype == 'event_detected':
                    events_detected += 1
                    if details.get('event_type') == 'LEVEL_BREACH':
                        sr_breaches += 1
                        lvl_type = details.get('details', {}).get('level_type', 'UNKNOWN')
                        price = details.get('price', 0.0)
                        key = f"{lvl_type} at {price}"
                        level_counts[key] = level_counts.get(key, 0) + 1
            except Exception:
                continue

    duration_str = '0 hours 0 minutes'
    if first_time and last_time:
        dur = last_time - first_time
        hours, rem = divmod(dur.total_seconds(), 3600)
        minutes, _ = divmod(rem, 60)
        duration_str = f"{int(hours)} hours {int(minutes)} minutes"

    most_active = max(level_counts.items(), key=lambda x: x[1])[0] if level_counts else 'None'

    print('\\nDRY RUN SESSION SUMMARY')
    print('========================')
    print(f'Duration: {duration_str}')
    print(f'Ticks processed: {ticks} (Not tracked in this log)')
    print(f'Events detected: {events_detected}')
    print(f'Confluence gate: {confluence_passes} passed / {confluence_fails} failed')
    print(f'Graph fires: {graph_fires}')
    print('DRUCKENMILLER decisions:')
    print(f'  - APPROVED: {decisions_approved}')
    print(f'  - REJECTED: {decisions_rejected}')
    print(f'  - Top rejection reasons:')
    for reason, count in sorted(rejection_reasons.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f'      {count}x: {reason}')
    print(f'Errors: {errors}')
    print(f'SR level breaches: {sr_breaches}')
    print(f'Most active level: {most_active}\\n')
'''
content += '\n' + summary_func

# Injections
content = content.replace(
    'return met_count >= min_conditions',
    '''is_pass = met_count >= min_conditions
        if hasattr(self, '_log_dry_run_event'):
            self._log_dry_run_event('confluence_pass' if is_pass else 'confluence_fail', {'conditions_met': conditions_met, 'min': min_conditions, 'event': event.event_type.value})
        return is_pass''', 1)

content = content.replace(
    'logger.info("="*50)',
    '''logger.info("="*50)
            if hasattr(self, '_log_dry_run_event'):
                self._log_dry_run_event('event_detected', {'event_type': event.event_type.value, 'price': event.price, 'details': event.details})''', 1)

content = content.replace(
    'logger.info("FIRING GRAPH #%d for event: %s",\n                        self._events_fired, event.event_type.value)',
    '''logger.info("FIRING GRAPH #%d for event: %s",
                        self._events_fired, event.event_type.value)
            if hasattr(self, '_log_dry_run_event'):
                self._log_dry_run_event('graph_fire', {'event_type': event.event_type.value})''', 1)

content = content.replace(
    'logger.info("*"*50 + "\\n")',
    '''logger.info("*"*50 + "\\n")
                if hasattr(self, '_log_dry_run_event'):
                    decision_obj = final_state.get('final_trade_decision', {}) if isinstance(final_state, dict) else getattr(final_state, 'final_trade_decision', {})
                    if not isinstance(decision_obj, dict) and hasattr(decision_obj, 'dict'):
                        decision_obj = decision_obj.dict()
                    elif not isinstance(decision_obj, dict) and hasattr(decision_obj, '__dict__'):
                        decision_obj = decision_obj.__dict__
                    elif not isinstance(decision_obj, dict):
                        decision_obj = {}
                    self._log_dry_run_event('decision', {
                        'execute': decision_obj.get('execute', signal in ['Buy', 'Sell', 'Overweight', 'Underweight']),
                        'direction': decision_obj.get('direction', signal),
                        'confidence': decision_obj.get('confidence', 0),
                        'reason': decision_obj.get('reason', ''),
                        'abort_reason': decision_obj.get('abort_reason', None)
                    })''', 1)

content = content.replace(
    'logger.error("Graph execution failed: %s", e, exc_info=True)',
    '''logger.error("Graph execution failed: %s", e, exc_info=True)
                if hasattr(self, '_log_dry_run_event'):
                    self._log_dry_run_event('error', {'error': str(e)})''', 1)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Done!')
