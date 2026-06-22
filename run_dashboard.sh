#!/usr/bin/env bash
# Start the AxonAI dashboard server standalone (no daemon needed)
cd "$(dirname "$0")"

python3 -c "
import sys, time, signal, threading
sys.path.insert(0, '.')

from axonai.realtime.api_server import start_dashboard

server = start_dashboard(host='127.0.0.1', port=8000)
print('✅ AxonAI Dashboard running at http://127.0.0.1:8000/')
print('   Press Ctrl+C to stop')

# Keep the process alive
stop = threading.Event()
signal.signal(signal.SIGINT, lambda s, f: stop.set())
signal.signal(signal.SIGTERM, lambda s, f: stop.set())
try:
    stop.wait()
except KeyboardInterrupt:
    pass
print('Server stopped.')
" 2>&1
