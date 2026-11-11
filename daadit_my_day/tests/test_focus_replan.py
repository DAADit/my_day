from datetime import timedelta
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFocusReplan(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user = new_test_user(cls.env, login="my_day_focus", groups="project.group_project_manager")
        cls.employee = cls.env["hr.employee"].create({"name": "Focus fixture", "user_id": user.id})
        cls.model = cls.env["daadit.my.day"].with_user(user)
        cls.tz = cls.model._tz(cls.employee)
        cls.day = cls.model._today(cls.tz) + timedelta(days=1)
        cls.start, _ = cls.model._bounds(cls.day, cls.tz)
        cls.start += timedelta(hours=9)

    def _slot(self, title, start):
        item = {"kind": "own", "title": title, "subtitle": "", "reason": "Zelf toegevoegd"}
        return self.env["planning.slot"].create(
            self.model._slot_vals(
                self.employee, item, start, start + timedelta(minutes=30), 10, True,
            )
        )

    def test_limit_and_protect_focus(self):
        slots = [self._slot(str(index), self.start + timedelta(hours=index)) for index in range(4)]
        with patch.object(type(self.model), "_payload", return_value={}):
            for slot in slots[:3]:
                self.model.set_day_focus(slot.id, True, True, False, self.day)
            with self.assertRaises(UserError):
                self.model.set_day_focus(slots[3].id, True, False, False, self.day)
        with patch.object(type(self.model), "_free_gaps", return_value=[
            [self.start - timedelta(hours=1), self.start + timedelta(hours=6)],
        ]):
            self.model._reflow(self.employee, self.day, self.tz)
        self.assertEqual(slots[0].start_datetime, self.start)
        self.assertEqual(len(self.model._goals(self.employee, self.day)), 3)

    def test_preview_confirm_and_undo_without_changing_records(self):
        slot = self._slot("Planning fixture", self.start)
        later = self.start + timedelta(hours=2)

        def simulate(_employee, _day, _tz):
            slot.write({"start_datetime": later, "end_datetime": later + timedelta(minutes=30)})

        with patch.object(type(self.model), "_plan_horizon", side_effect=simulate), patch.object(
            type(self.model), "_free_gaps", return_value=[[self.start, later + timedelta(hours=1)]],
        ), patch.object(type(self.model), "_payload", return_value={}):
            preview = self.model.preview_replan(self.day)
            self.assertEqual(len(preview["changes"]), 1)
            self.assertEqual(slot.start_datetime, self.start)
            self.model.confirm_replan(self.day, preview["token"])
            self.assertEqual(slot.start_datetime, later)
            self.model.undo_replan(self.day)
            self.assertEqual(slot.start_datetime, self.start)
            preview = self.model.preview_replan(self.day)
            self.model.confirm_replan(self.day, preview["token"])
            slot.daadit_timer_seconds = 60
            with self.assertRaises(UserError):
                self.model.undo_replan(self.day)
            self.assertEqual(slot.daadit_timer_seconds, 60)

    def test_only_opted_in_focus_can_move_for_urgent_ticket(self):
        slot = self._slot("Protected focus fixture", self.start)
        slot.daadit_day_focus = True
        urgent = {"minutes": 15}
        next_start = self.start + timedelta(days=1)
        gaps = [[self.start, self.start + timedelta(minutes=30)]]
        next_placement = (
            self.day + timedelta(days=1), (next_start, next_start + timedelta(minutes=30)),
        )
        with patch.object(type(self.model), "_today", return_value=self.day), patch.object(
            type(self.model), "_free_gaps", return_value=gaps,
        ), patch.object(type(self.model), "_closed_day", return_value=False), patch.object(
            type(self.model), "_next_available_slot",
            return_value=next_placement,
        ):
            protected = self.model._urgent_focus_placement(self.employee, self.day, self.tz, urgent)
            self.assertFalse(protected)
            slot.daadit_day_focus_interruptible = True
            placed = self.model._urgent_focus_placement(self.employee, self.day, self.tz, urgent)
        self.assertEqual(placed, (self.day, (self.start, self.start + timedelta(minutes=15))))
        self.assertEqual(slot.start_datetime, next_start)
        self.assertTrue(slot.daadit_day_focus)
