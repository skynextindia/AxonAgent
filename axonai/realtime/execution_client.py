# File: axonai/realtime/execution_client.py
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
    async with websockets.connect(url, ping_interval=None) as ws:
        await ws.send(json.dumps(request_dict))
        response = await ws.recv()
        return json.loads(response)


def send_execution_command(config: dict, request_dict: dict) -> dict:
    """Send command to execution_bridge.py and return the response."""
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
        logger.error("Failed to send command to execution bridge at %s: %s", url, e)
        return {"success": False, "reason": f"bridge_connection_error: {e}"}
