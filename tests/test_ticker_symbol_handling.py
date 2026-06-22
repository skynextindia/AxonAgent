import unittest

import pytest

from cli.utils import normalize_ticker_symbol


@pytest.mark.unit
class TickerSymbolHandlingTests(unittest.TestCase):
    def test_normalize_ticker_symbol_preserves_exchange_suffix(self):
        self.assertEqual(normalize_ticker_symbol(" cnc.to "), "CNC.TO")


if __name__ == "__main__":
    unittest.main()
