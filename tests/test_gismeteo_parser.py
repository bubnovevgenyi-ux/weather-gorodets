from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.config import settings
from app.gismeteo import GismeteoClient, _clean_duplicate_fragment, _parse_datetime_title, _text


class GismeteoParserTests(unittest.TestCase):
    def test_duplicate_time_fragment_is_collapsed(self) -> None:
        self.assertEqual(_clean_duplicate_fragment("0:000:00"), "0:00")
        self.assertEqual(_clean_duplicate_fragment("0:00 0:00"), "0:00")

    def test_distinct_values_are_not_collapsed(self) -> None:
        self.assertEqual(_clean_duplicate_fragment("0:00 3:00"), "0:00 3:00")
        self.assertEqual(_clean_duplicate_fragment("0 1"), "0 1")

    def test_text_collapses_duplicate_nested_fragments(self) -> None:
        soup = BeautifulSoup(
            '<div><span>0:00</span><span>0:00</span></div>',
            "lxml",
        )
        self.assertEqual(_text(soup.div), "0:00")

    def test_numeric_russian_datetime(self) -> None:
        value = _parse_datetime_title("05.11.2026, 03:00")
        self.assertIsNotNone(value)
        self.assertEqual(value.year, 2026)
        self.assertEqual(value.month, 11)
        self.assertEqual(value.day, 5)
        self.assertEqual(value.hour, 3)

    def test_current_widget_markup_extracts_hourly_forecast(self) -> None:
        html = '''
        <div class="widget__row_time">
          <span class="w_time">0:000:00</span>
          <span class="w_time">3:00</span>
        </div>
        <div class="w_temperature">
          <div class="value"><span class="unit_temperature_c">+4</span></div>
          <div class="value"><span class="unit_temperature_c">+5</span></div>
        </div>
        <div class="widget__row_pressure">
          <span class="unit_pressure_mm_hg_atm">750</span>
          <span class="unit_pressure_mm_hg_atm">751</span>
        </div>
        <div data-widget-id="humidity">
          <span class="w-humidity">80</span>
          <span class="w-humidity">81</span>
        </div>
        '''
        client = GismeteoClient()
        points = client._parse_hourly_page(html, "сегодня")
        self.assertEqual(len(points), 2)
        self.assertEqual([point.datetime.hour for point in points], [0, 3])
        self.assertEqual([point.temperature_c for point in points], [4.0, 5.0])
        self.assertEqual([point.pressure_mmhg for point in points], [750.0, 751.0])
        self.assertEqual([point.humidity_percent for point in points], [80.0, 81.0])


if __name__ == "__main__":
    unittest.main()
