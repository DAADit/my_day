from datetime import timedelta
from unittest.mock import patch

from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestWorkEstimate(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="my_day_estimate_user", groups="project.group_project_manager",
        )
        cls.employee = cls.env["hr.employee"].create({
            "name": "My Day estimate test", "user_id": cls.user.id,
        })
        cls.day = cls.env["daadit.my.day"].with_user(cls.user)
        cls.tz = cls.day._tz(cls.employee)
        cls.today = cls.day._today(cls.tz)
        project = cls.env["project.project"].create({"name": "Estimate fixture"})
        cls.task = cls.env["project.task"].create({
            "name": "Estimate fixture", "project_id": project.id,
            "user_ids": [(6, 0, cls.user.ids)],
        })

    def _item(self):
        return next((i for i in self.day._queue(self.employee, self.today, self.tz)
                     if i["kind"] == "task" and i["res_id"] == self.task.id), None)

    def _slot(self, day_offset, done=False):
        start = self.day._bounds(self.today + timedelta(days=day_offset), self.tz)[0]
        return self.env["planning.slot"].create({
            "name": "Estimate fixture", "resource_id": self.employee.resource_id.id,
            "daadit_day_kind": "task", "daadit_task_id": self.task.id,
            "start_datetime": start + timedelta(hours=10),
            "end_datetime": start + timedelta(hours=10, minutes=30),
            "daadit_day_state": "done" if done else "planned",
            "daadit_timer_seconds": 1800 if done else 0,
        })

    def test_chunks_reserve_future_minutes_and_do_not_replan_worked_time(self):
        with patch.object(type(self.day), "_payload", return_value={}):
            self.day.set_work_estimate("task", self.task.id, 120, 30)
        self.assertEqual(self._item()["minutes"], 30)
        self._slot(0, done=True)
        self._slot(1)
        expected = self.day.env._("%s min remaining outside scheduled blocks", 60)
        self.assertIn(expected, self._item()["reason"])
        self._slot(2)
        self._slot(3)
        self.assertIsNone(self._item())

    def test_suggestions_need_three_samples_and_ignore_outlier(self):
        samples = [(self.task, 1), (self.task, 2), (self.task, 18)]
        self.assertFalse(self.day._suggest_from_samples(samples[:2], 0))
        suggestion = self.day._suggest_from_samples(samples, 1800)
        self.assertEqual(suggestion["actual"], 120)
        self.assertEqual(suggestion["remaining"], 90)
