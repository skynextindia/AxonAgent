"""Unit tests for the single-source session math (axonai/sessions.py).

Consolidates what used to be six hand-rolled DST computations across daemon.py,
live_state.py, world_state.py, evidence_extractor.py and api_server.py (which
read the local machine clock). These lock the boundaries, the classifier buckets,
DST-transition correctness (the whole point of moving to zoneinfo), and the HUD.
"""
import unittest
from datetime import datetime, timezone, timedelta

from axonai.sessions import (
    get_dst_session_hours, classify_session, session_hud, session_label,
)


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


class TestBoundaries(unittest.TestCase):
    def test_summer_dst_boundaries(self):
        # Aug: EU (BST) + US (EDT) both on
        self.assertEqual(get_dst_session_hours(utc(2026, 8, 3, 10)), (7.0, 15.0, 12.0, 18.0))

    def test_winter_standard_boundaries(self):
        # Jan: no DST anywhere
        self.assertEqual(get_dst_session_hours(utc(2026, 1, 15, 10)), (8.0, 16.0, 13.0, 19.0))

    def test_eu_us_dst_offset_window(self):
        # Late Oct 2026: EU has left BST (Oct 25) but US still on EDT (until Nov 1),
        # so London is winter (8/16) while NY is still summer (12/18). This mixed
        # window is exactly what hand-rolled 'nth-Sunday' math tends to get wrong.
        ldn_o, ldn_c, ny_o, ny_c = get_dst_session_hours(utc(2026, 10, 28, 10))
        self.assertEqual((ldn_o, ldn_c), (8.0, 16.0))   # London back on GMT
        self.assertEqual((ny_o, ny_c), (12.0, 18.0))    # NY still on EDT

    def test_naive_treated_as_utc(self):
        self.assertEqual(get_dst_session_hours(datetime(2026, 8, 3, 10)),
                         get_dst_session_hours(utc(2026, 8, 3, 10)))


class TestClassify(unittest.TestCase):
    def s(self, h, mi=0):
        return classify_session(utc(2026, 8, 3, h, mi))[0]

    def test_all_buckets_summer(self):
        self.assertEqual(self.s(8), "london")     # 07-12
        self.assertEqual(self.s(13), "overlap")   # 12-15
        self.assertEqual(self.s(16), "newyork")   # 15-18
        self.assertEqual(self.s(18), "rollover")  # 18-19
        self.assertEqual(self.s(20), "asian")     # else
        self.assertEqual(self.s(2), "asian")

    def test_bucket_edges_are_half_open(self):
        self.assertEqual(self.s(7), "london")     # ldn_open inclusive
        self.assertEqual(self.s(12), "overlap")   # ny_open inclusive
        self.assertEqual(self.s(15), "newyork")   # ldn_close inclusive
        self.assertEqual(self.s(19), "asian")     # rollover ends, back to asian

    def test_hours_since_london_open_wraps(self):
        # 02:00 UTC is before London open (07:00) -> wrapped, must be >= 0
        _, hrs = classify_session(utc(2026, 8, 3, 2))
        self.assertGreaterEqual(hrs, 0.0)
        self.assertAlmostEqual(hrs, 2 + 24 - 7)   # 19.0

    def test_hours_since_positive_intraday(self):
        _, hrs = classify_session(utc(2026, 8, 3, 13))
        self.assertAlmostEqual(hrs, 6.0)          # 13 - 7


class TestHudAndLabel(unittest.TestCase):
    def test_hud_shape_and_london_active(self):
        hud = session_hud(utc(2026, 8, 3, 10))    # 10:00 UTC -> London open
        names = [s["name"] for s in hud]
        self.assertEqual(names, ["Sydney", "Tokyo", "London", "New York"])
        ldn = next(s for s in hud if s["name"] == "London")
        self.assertTrue(ldn["active"])
        self.assertEqual((ldn["open_utc"], ldn["close_utc"]), (7.0, 15.0))

    def test_hud_ny_uses_human_close(self):
        # HUD NY runs to 16:00 ET (20 UTC summer) — the human display close,
        # deliberately distinct from the 18:00 analytic rollover anchor.
        ny = next(s for s in session_hud(utc(2026, 8, 3, 14)) if s["name"] == "New York")
        self.assertEqual((ny["open_utc"], ny["close_utc"]), (12.0, 20.0))
        self.assertTrue(ny["active"])             # 14:00 UTC is inside NY

    def test_hud_sydney_wraps_midnight(self):
        syd = next(s for s in session_hud(utc(2026, 8, 3, 23)) if s["name"] == "Sydney")
        self.assertGreater(syd["open_utc"], syd["close_utc"])  # wraps
        self.assertTrue(syd["active"])            # 23:00 UTC is inside Sydney

    def test_labels_map_from_classifier(self):
        self.assertEqual(session_label(utc(2026, 8, 3, 8)), "London")
        self.assertEqual(session_label(utc(2026, 8, 3, 13)), "Overlap")
        self.assertEqual(session_label(utc(2026, 8, 3, 16)), "New York")
        self.assertEqual(session_label(utc(2026, 8, 3, 2)), "Sydney/Tokyo")


if __name__ == "__main__":
    unittest.main()
