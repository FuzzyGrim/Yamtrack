from datetime import date
from unittest import TestCase

from app.history_views import _activity_day_intensity


class ActivityIntensityTests(TestCase):
    def test_months_and_units_have_independent_bands(self):
        def day(day_number, values, month=1):
            return {
                "date": date(2026, month, day_number),
                "entries": [{"runtime_minutes": value} for value in values],
            }

        result = _activity_day_intensity([
            day(1, [10, None]), day(2, [20]), day(3, [30]), day(4, [30]),
            day(5, [0]), day(6, [None, 0, 0]), day(1, [1], month=2),
            {"date": None, "entries": [{"runtime_minutes": 1000}]},
            {"date": date(2026, 1, 7), "entries": []},
        ])
        self.assertEqual([result[f"2026-01-0{i}"]["level"] for i in range(1, 7)], [1, 2, 3, 3, 1, 3])
        self.assertEqual(result["2026-01-05"]["measure"], "entries")
        self.assertEqual(result["2026-01-01"]["value"], 10)
        self.assertEqual(result["2026-02-01"]["level"], 3)
        self.assertNotIn("2026-01-07", result)
