"""AxonAI — Pure-math market-state-aware execution engine.

Agy branch: all LLM/AI third-party dependencies removed.
"""

import warnings

# Load .env files at package import for config overlay.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass
