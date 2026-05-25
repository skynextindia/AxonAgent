# axonai/graph/evidence_compressor.py
"""Pure-Python evidence compression — no LLM call.

Extracts final summary paragraphs from each analyst report, strips raw
data tables, and surfaces critical macro-event sentences.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

# Keywords that indicate a critical macro event
_CRITICAL_KEYWORDS = re.compile(
    r"\b(rate|CPI|GDP|ECB|Fed|cut|hike|inflation|employment|NFP|FOMC|PMI|"
    r"tariff|recession|central\s+bank|interest\s+rate)\b",
    re.IGNORECASE,
)


def _extract_summary_paragraph(text: str) -> str:
    """Return the last non-empty paragraph of *text*.

    Analyst prompts are structured so the final paragraph is always a
    summary or conclusion.  If the text is too short to split, return it
    verbatim.
    """
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return text.strip()
    return paragraphs[-1]


def _strip_data_lines(text: str, max_line_len: int = 50) -> str:
    """Remove lines that look like raw CSV / price-table data.

    Any line whose non-whitespace content exceeds *max_line_len* and
    contains two or more commas or pipe separators is dropped.
    """
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) > max_line_len and (stripped.count(",") >= 2 or stripped.count("|") >= 2):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _truncate_to_words(text: str, max_words: int) -> str:
    """Truncate *text* to at most *max_words* words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"


def _extract_critical_events(text: str) -> List[str]:
    """Pull out sentences containing macro-event keywords."""
    events: list[str] = []
    # Split on sentence-ending punctuation
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        if _CRITICAL_KEYWORDS.search(sentence):
            clean = sentence.strip()
            if clean and clean not in events:
                events.append(clean)
    return events


def _approx_tokens(text: str) -> int:
    """Rough token count ≈ words × 1.3 (GPT-family average)."""
    return int(len(text.split()) * 1.3)


def compress_evidence(
    agent_state: dict,
    max_words_per_analyst: int = 150,
) -> dict:
    """Compress analyst reports into a compact evidence bundle.

    Takes full AgentState after parallel analysts complete.
    Extracts only the final summary paragraph from each analyst output.
    Preserves critical_events as a separate list.

    Returns
    -------
    dict with keys:
        market_summary, fundamental_summary, news_summary,
        sentiment_summary, critical_events,
        total_input_tokens_approx, total_output_tokens_approx,
        compression_ratio
    """
    report_map = {
        "market_summary": "market_report",
        "fundamental_summary": "fundamentals_report",
        "news_summary": "news_report",
        "sentiment_summary": "sentiment_report",
    }

    total_input_words = 0
    total_output_words = 0
    all_critical: list[str] = []
    summaries: Dict[str, str] = {}

    for out_key, state_key in report_map.items():
        raw = agent_state.get(state_key) or ""
        input_words = len(raw.split())
        total_input_words += input_words

        # Step 1: strip data tables
        cleaned = _strip_data_lines(raw)
        # Step 2: extract summary paragraph
        summary = _extract_summary_paragraph(cleaned)
        # Step 3: truncate to word limit
        summary = _truncate_to_words(summary, max_words_per_analyst)

        output_words = len(summary.split())
        total_output_words += output_words

        summaries[out_key] = summary

        # Step 4: harvest critical events from full text
        all_critical.extend(_extract_critical_events(raw))

    # De-duplicate critical events while preserving order
    seen: set[str] = set()
    unique_critical: list[str] = []
    for ev in all_critical:
        if ev not in seen:
            seen.add(ev)
            unique_critical.append(ev)

    input_tokens = _approx_tokens(" " * total_input_words)  # rough proxy
    output_tokens = _approx_tokens(" " * total_output_words)
    # Correct token estimation: use actual word counts
    input_tokens = int(total_input_words * 1.3)
    output_tokens = int(total_output_words * 1.3)
    ratio = 1.0 - (total_output_words / max(total_input_words, 1))

    result = {
        **summaries,
        "critical_events": unique_critical,
        "total_input_tokens_approx": input_tokens,
        "total_output_tokens_approx": output_tokens,
        "compression_ratio": round(ratio, 3),
    }

    logger.info(
        "Evidence compressed: %d → %d words (%.0f%% reduction), %d critical events",
        total_input_words, total_output_words, ratio * 100, len(unique_critical),
    )

    return result
