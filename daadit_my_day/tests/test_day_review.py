from datetime import timedelta

from odoo.exceptions import AccessError, UserError
from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDayReview(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="my_day_review_user", groups="base.group_user")
        cls.employee = cls.env["hr.employee"].create({
            "name": "My Day review test", "user_id": cls.user.id,
        })
        cls.other = cls.env["hr.employee"].create({"name": "Other employee review test"})
        cls.model = cls.env["daadit.my.day"].with_user(cls.user)
        cls.tz = cls.model._tz(cls.employee)
        cls.today = cls.model._today(cls.tz)
        cls.start, _ = cls.model._bounds(cls.today, cls.tz)

    def _slot(self, name, employee=None, done=False):
        return self.env["planning.slot"].create({
            "resource_id": (employee or self.employee).resource_id.id,
            "name": name,
            "start_datetime": self.start + timedelta(hours=4),
            "end_datetime": self.start + timedelta(hours=5),
            "daadit_day_kind": "own",
            "daadit_day_state": "done" if done else "planned",
            "daadit_timer_seconds": 180 if done else 0,
            "daadit_day_pinned": True,
        })

    def test_day_and_done_views_report_work_without_mixing_employees(self):
        done = self._slot("Worked three minutes", done=True)
        pending = self._slot("Still open")
        self._slot("Other employee", employee=self.other, done=True)
        review = self.model.get_day_review(self.today)
        self.assertEqual({row["id"] for row in review["rows"]}, {done.id, pending.id})
        self.assertEqual(review["seconds"], 180)
        self.assertEqual(review["open"], 1)
        self.assertTrue(review["can_close"])
        history = self.model.get_day_review(self.today, done_only=True)
        self.assertEqual([row["id"] for row in history["rows"]], [done.id])
        self.assertFalse(history["can_close"])

    def test_other_employee_and_unbounded_period_are_rejected(self):
        with self.assertRaises(AccessError):
            self.model.get_day_review(self.today, employee_id=self.other.id, done_only=True)
        with self.assertRaises(UserError):
            self.model.get_day_review(self.today, self.today + timedelta(days=31), done_only=True)

    def test_already_finished_block_cannot_be_finalized_again(self):
        done = self._slot("Already finished", done=True)
        with self.assertRaises(UserError):
            self.model.review_day_block(done.id, "complete", self.today)
        self.assertEqual(done.daadit_timer_seconds, 180)
        self.assertFalse(done.daadit_timesheet_id)

    def test_time_correction_preserves_history_and_is_idempotent(self):
        done = self._slot("Corrected work", done=True)
        self.model.correct_day_time(done.id, 7, "Werkduur nagekeken")
        self.model.correct_day_time(done.id, 7, "Werkduur nagekeken")
        self.assertEqual(done.daadit_timer_seconds, 420)
        self.assertEqual(len(done.daadit_day_time_history), 1)
        change = done.daadit_day_time_history[0]
        self.assertEqual((change["before"], change["after"]), (180, 420))
        self.assertEqual(change["reason"], "Werkduur nagekeken")
        self.assertFalse(done.daadit_timesheet_id)

    def test_running_timer_cannot_be_corrected(self):
        slot = self._slot("In progress")
        self.model.open_block(slot.id)
        with self.assertRaises(UserError):
            self.model.correct_day_time(slot.id, 7, "Dit mag niet tijdens de timer")
