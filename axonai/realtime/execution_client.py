# File: axonai/realtime/execution_client.py
import os
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

def run_coroutine(coro):
    """Helper to run a coroutine in both synchronous and asynchronous contexts safely."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        import threading
        from concurrent.futures import Future
        fut = Future()
        
        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            try:
                res = new_loop.run_until_complete(coro)
                fut.set_result(res)
            except Exception as e:
                fut.set_exception(e)
            finally:
                new_loop.close()
                
        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join()
        return fut.result()
    else:
        return loop.run_until_complete(coro)


async def _ws_send_cmd(url, request_dict):
    import websockets
    async def _send_and_recv():
        async with websockets.connect(url, ping_interval=None) as ws:
            await ws.send(json.dumps(request_dict))
            response = await ws.recv()
            return json.loads(response)
    return await asyncio.wait_for(_send_and_recv(), timeout=0.25)


def send_execution_command(config: dict, request_dict: dict) -> dict:
    """Send command to execution_bridge.py and return the response."""
    # Attach the shared-secret token when configured, so the bridge's opt-in auth
    # accepts the request. No-op when neither side sets a token (backward compatible).
    token = config.get("realtime_execution_bridge_token") or os.environ.get("AXON_BRIDGE_TOKEN")
    if token and "token" not in request_dict:
        request_dict = {**request_dict, "token": token}
    # Try to auto-detect Windows host IP if running in WSL
    host = config.get("realtime_execution_bridge_host")
    if not host:
        import platform
        if platform.system() == "Windows":
            host = "127.0.0.1"
        else:
            # Detect WSL gateway
            import subprocess
            try:
                result = subprocess.run(
                    ["ip", "route", "show", "default"],
                    capture_output=True, text=True, timeout=2
                )
                host = result.stdout.strip().split()[2]
            except Exception:
                host = "127.0.0.1"
                
    port = config.get("realtime_execution_bridge_port", 8766)
    url = f"ws://{host}:{port}"
    
    try:
        return run_coroutine(_ws_send_cmd(url, request_dict))
    except Exception as e:
        # If connection refused, attempt to auto-start the bridge in the background
        err_msg = str(e).lower()
        if "refused" in err_msg or "1225" in err_msg or "timeout" in err_msg:
            import subprocess
            import sys
            import platform
            import os
            try:
                if platform.system() == "Windows":
                    # Spawning execution_bridge.py directly on native Windows.
                    # CRITICAL: pass --path so the bridge attaches to the TRADE terminal.
                    # Without it, mt5.initialize() grabs whichever terminal it finds first —
                    # with the Exness data terminal running, account_info AND order execution
                    # could silently route to the data account.
                    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "windows", "execution_bridge.py")
                    cmd = [sys.executable, script_path, "--port", str(port)]
                    trade_path = config.get("mt5_trade_terminal_path")
                    if trade_path:
                        cmd += ["--path", trade_path]
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    # Spawning start_bridge.bat via cmd.exe in WSL
                    subprocess.Popen(["cmd.exe", "/c", "start", "windows\\start_bridge.bat"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        logger.error("Failed to send command to execution bridge at %s: %s", url, e)
        return {"success": False, "reason": f"bridge_connection_error: {e}"}
