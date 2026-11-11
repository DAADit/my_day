from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import common, tagged


@tagged("post_install", "-at_install")
class TestDayCapacity(common.TransactionCase):
    def setUp(self):
        super().setUp()
        self.model = self.env["daadit.my.day"]
        self.morning = datetime(2026, 11, 10, 9)

    def test_capacity_accounts_for_elapsed_time_and_overlapping_work(self):
        start = self.morning
        gaps = [[start, start + timedelta(hours=3)],
                [start + timedelta(hours=4), start + timedelta(hours=6)]]
        slots = [
            SimpleNamespace(start_datetime=start + timedelta(hours=1),
                            end_datetime=start + timedelta(hours=2),
                            daadit_day_state="done"),
            SimpleNamespace(start_datetime=start + timedelta(hours=1, minutes=30),
                            end_datetime=start + timedelta(hours=2, minutes=30),
                            daadit_day_state="planned"),
        ]
        stats = self.model._capacity_stats(gaps, slots, start + timedelta(hours=1))
        self.assertEqual(stats, {"available": 300, "planned": 60, "free": 150})

    def test_defer_skips_day_without_a_contiguous_free_slot(self):
        start = self.morning
        first = start + timedelta(days=1)
        second = start + timedelta(days=2)
        occupied = SimpleNamespace(start_datetime=first + timedelta(minutes=45),
                                   end_datetime=first + timedelta(minutes=75))
        with (
            patch.object(type(self.model), "_free_gaps", side_effect=[
                [[first, first + timedelta(hours=2)]],
                [[second, second + timedelta(hours=2)]],
            ]),
            patch.object(type(self.model), "_slots", side_effect=[[occupied], []]),
        ):
            placement = self.model._next_available_slot(None, start.date(), None, 60)
        self.assertEqual(placement, (second.date(), (second, second + timedelta(hours=1))))

    def test_fit_does_not_round_up_a_gap_that_is_too_short(self):
        start = self.morning
        gaps = [[start, start + timedelta(minutes=29, seconds=59)]]
        self.assertIsNone(self.model._take_gap(gaps, 30))

    def test_replan_moves_overflow_and_preserves_explicit_exception(self):
        import pytz

        start = self.morning
        target = start + timedelta(days=1)
        employee = self.env["hr.employee"].create({"name": "My Day capacity test"})
        slots = self.env["planning.slot"]
        for sequence, label, start_time, outside in (
            (10, "Past binnen het rooster", start, False),
            (20, "Schuift door", start + timedelta(hours=1), False),
            (30, "Bewust buiten werktijd", start + timedelta(hours=8), True),
        ):
            slots |= self.env["planning.slot"].create({
                "name": label,
                "resource_id": employee.resource_id.id,
                "daadit_day_kind": "own",
                "daadit_day_sequence": sequence,
                "daadit_day_pinned": True,
                "daadit_day_outside_hours": outside,
                "start_datetime": start_time,
                "end_datetime": start_time + timedelta(hours=1),
            })

        def gaps(_employee, day, _tz):
            if day == start.date():
                return [[start, start + timedelta(hours=1)]]
            if day == target.date():
                return [[target, target + timedelta(hours=2)]]
            return []

        with (
            patch.object(type(self.model), "_now", return_value=start - timedelta(hours=1)),
            patch.object(type(self.model), "_free_gaps", side_effect=gaps),
        ):
            self.model._reflow(employee, start.date(), pytz.utc)
            self.model._reflow(employee, start.date(), pytz.utc)
        self.assertEqual(slots[0].start_datetime, start)
        self.assertEqual(slots[1].start_datetime, target)
        self.assertEqual(slots[1].end_datetime, target + timedelta(hours=1))
        self.assertEqual(slots[2].start_datetime, start + timedelta(hours=8))
        self.assertTrue(slots[2].daadit_day_outside_hours)

    def test_no_capacity_does_not_create_an_unapproved_outside_hours_slot(self):
        with patch.object(type(self.model), "_find_placement", return_value=None):
            slot = self.model._insert_block(
                None, self.morning.date(), None, {"kind": "own", "minutes": 30},
            )
        self.assertFalse(slot)
