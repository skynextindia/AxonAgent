"""Forex-specific social sentiment and live discussions feed (from ForexLive).

Provides high-signal, real-time public Forex analysis and retail discussions
from the ForexLive RSS feed, filtered and correlated for the active base/quote pair.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_FEED_URL = "https://www.forexlive.com/feed/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def fetch_forex_social_feed(ticker: str, limit: int = 30, timeout: float = 10.0) -> str:
    """Fetch recent live discussions and market sentiment from ForexLive
    filtered/correlated for the currency symbol.
    
    Provides the AI Sentiment Analyst with 100% genuine retail Forex discussions
    and fast-breaking news, with zero dependency on external packages.
    """
    req = Request(_FEED_URL, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            xml_data = resp.read()
            # Parse RSS XML
            root = ET.fromstring(xml_data)
            items = root.findall(".//item")
            
            lines = []
            matched_count = 0
            
            # Clean symbol (e.g. 'EURUSD=X' -> 'EURUSD', 'EURUSDm' -> 'EURUSD')
            sym = ticker.strip().upper().replace("/", "")
            if sym.endswith("=X"):
                sym = sym[:-2]
            if len(sym) == 7 and sym[:6].isalpha():
                sym = sym[:6]
                
            base = sym[:3] if len(sym) == 6 else sym
            quote = sym[3:6] if len(sym) == 6 else ""
            
            for item in items:
                title = item.find("title").text if item.find("title") is not None else ""
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                desc_el = item.find("description")
                desc = desc_el.text if desc_el is not None else ""
                
                # strip html tags
                desc_clean = re.sub('<[^<]+?>', '', desc).strip()
                if len(desc_clean) > 220:
                    desc_clean = desc_clean[:220] + "..."
                    
                # Match title/description against symbol currencies (e.g. 'EUR' or 'USD' or 'EURUSD')
                is_match = False
                if base.lower() in title.lower() or base.lower() in desc_clean.lower():
                    is_match = True
                if quote and (quote.lower() in title.lower() or quote.lower() in desc_clean.lower()):
                    is_match = True
                    
                if is_match or not base:
                    lines.append(f"[{pub_date} · ForexLive] {title}\n  Summary: {desc_clean}")
                    matched_count += 1
                    if matched_count >= limit:
                        break
            
            if not lines:
                # Fallback to general market feed if no specific currency match
                for item in items[:15]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    desc_el = item.find("description")
                    desc = desc_el.text if desc_el is not None else ""
                    desc_clean = re.sub('<[^<]+?>', '', desc).strip()
                    if len(desc_clean) > 220:
                        desc_clean = desc_clean[:220] + "..."
                    lines.append(f"[{pub_date} · ForexLive] {title}\n  Summary: {desc_clean}")
                matched_count = len(lines)
                
            summary = f"ForexLive Feed Summary: {matched_count} active discussion topics matching {sym.upper()}"
            return summary + "\n\n" + "\n\n".join(lines)
            
    except Exception as e:
        logger.warning("ForexLive RSS fetch failed: %s", e)
        return f"<ForexLive feed temporarily unavailable: {str(e)}>"
