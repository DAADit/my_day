from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestTimerRecovery(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="my_day_timer_test")
        cls.employee = cls.env["hr.employee"].create({
            "name": "Timer test", "user_id": cls.user.id,
        })
        cls.day = cls.env["daadit.my.day"].with_user(cls.user)
        cls.start = datetime(2026, 11, 10, 9)

    def _slot(self, beat_minutes=5):
        return self.env["planning.slot"].create({
            "resource_id": self.employee.resource_id.id,
            "start_datetime": self.start,
            "end_datetime": self.start + timedelta(hours=1),
            "daadit_day_kind": "own",
            "daadit_timer_started_at": self.start,
            "daadit_timer_beat_at": self.start + timedelta(minutes=beat_minutes),
        })

    def test_late_heartbeat_cannot_revive_session(self):
        slot = self._slot()
        with patch.object(type(self.day), "_now", return_value=self.start + timedelta(hours=2)):
            self.assertFalse(self.day.heartbeat(slot.id))
            self.assertFalse(slot.daadit_timer_started_at)
            self.assertEqual(slot.daadit_timer_seconds, 300)
            self.assertFalse(self.day.heartbeat(slot.id))
            self.assertEqual(slot.daadit_timer_seconds, 300)

    def test_legacy_heartbeat_also_expires_session(self):
        slot = self._slot()
        with patch.object(type(self.day), "_now", return_value=self.start + timedelta(hours=2)):
            self.assertFalse(self.day.heartbeat())
            self.assertEqual(slot.daadit_timer_seconds, 300)

    def test_old_tab_does_not_renew_another_block(self):
        old = self._slot()
        old.write({"daadit_timer_started_at": False, "daadit_timer_beat_at": False})
        current = self._slot()
        beat = current.daadit_timer_beat_at
        with patch.object(type(self.day), "_now", return_value=self.start + timedelta(minutes=6)):
            self.assertFalse(self.day.heartbeat(old.id))
            self.assertEqual(current.daadit_timer_beat_at, beat)

    def test_reopen_settles_gap_and_starts_new_session(self):
        slot = self._slot()
        now = self.start + timedelta(hours=2)
        with patch.object(type(self.day), "_now", return_value=now):
            info = self.day.open_block(slot.id)
            self.assertTrue(info["running"])
            self.assertEqual(info["seconds"], 300)
            self.assertEqual(slot.daadit_timer_started_at, now)

    def test_display_and_stop_use_same_elapsed_time(self):
        slot = self._slot()
        with patch.object(type(self.day), "_now", return_value=self.start + timedelta(hours=2)):
            displayed = self.day._timer_info(slot)["seconds"]
            self.day.pause_block(slot.id)
            self.assertEqual(displayed, 300)
            self.assertEqual(slot.daadit_timer_seconds, displayed)

    def test_heartbeat_cannot_access_another_employee(self):
        slot = self._slot()
        other = self.env["hr.employee"].create({"name": "Other timer employee"})
        slot.resource_id = other.resource_id
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.day.heartbeat(slot.id)

    def test_starting_another_block_stops_the_previous_timer(self):
        old = self._slot()
        current = self._slot()
        current.write({"daadit_timer_started_at": False, "daadit_timer_beat_at": False})
        now = self.start + timedelta(minutes=6)
        with patch.object(type(self.day), "_now", return_value=now):
            self.day.open_block(current.id)
        self.assertFalse(old.daadit_timer_started_at)
        self.assertEqual(old.daadit_timer_seconds, 360)
        self.assertEqual(self.day._running_slots(self.employee), current)
