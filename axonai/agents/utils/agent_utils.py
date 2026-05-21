from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from axonai.agents.utils.core_stock_tools import (
    get_stock_data
)
from axonai.agents.utils.technical_indicators_tools import (
    get_indicators
)
from axonai.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from axonai.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from axonai.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(ticker: str, asset_type: str = "stock") -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    if asset_type == "forex":
        instrument_label = "forex pair"
        extra_hint = " Treat it as a currency pair rather than a company. Focus on macroeconomic metrics (GDP, CPI, central bank interest rates) and central bank policy divergences. Use the `get_fundamentals` tool to retrieve the sovereign G10 macroeconomic profiles and the Interest Rate Differential."
    elif asset_type == "crypto":
        instrument_label = "asset"
        extra_hint = " Treat it as a crypto asset rather than a company, and do not assume company fundamentals are available."
    else:
        instrument_label = "instrument"
        extra_hint = ""
    return (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`, `=X`)."
        + extra_hint
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages node - no-op for parallel execution to avoid RemoveMessage conflicts."""
        return {}

    return delete_messages


        
