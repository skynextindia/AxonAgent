import sys
import re

content = open('d:/AXON.AI/AxonAgent-Agy/axonai/realtime/daemon.py', 'r', encoding='utf-8').read()

inline_types = ['"trigger_metrics"', '"trade_state"', '"location_context"', '"latency_metrics"', '"candle"', '"decision"', '"event"']
for t in inline_types:
    content = re.sub(r'("type": ' + t + r',)', r'\1\n                  "symbol": self.mt5_symbol,', content)

content = re.sub(r'(def _get_mode_payload\(self\) -> dict:[\s\S]*?return {)', r'\1\n            "symbol": self.mt5_symbol,', content)
content = re.sub(r'(def _get_regime_payload\(self\) -> dict:[\s\S]*?return {)', r'\1\n            "symbol": self.mt5_symbol,', content)
content = re.sub(r'(def _get_levels_payload\(self\) -> dict:[\s\S]*?return {)', r'\1\n            "symbol": self.mt5_symbol,', content)
content = re.sub(r'(def _get_candles_payload\(self, timeframe: str\) -> dict:[\s\S]*?return {)', r'\1\n            "symbol": self.mt5_symbol,', content)
content = re.sub(r'(def _get_account_payload\(self\) -> Optional\[dict\]:[\s\S]*?return {)', r'\1\n            "symbol": self.mt5_symbol,', content)

open('d:/AXON.AI/AxonAgent-Agy/axonai/realtime/daemon.py', 'w', encoding='utf-8').write(content)
print("Done")
