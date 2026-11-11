from datetime import timedelta
from unittest.mock import patch

from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSharedWork(common.TransactionCase):
    def test_single_owner_handoff_and_parallel_work(self):
        users = self.env["res.users"].concat(*[
            new_test_user(self.env, login="my_day_shared_" + name,
                          groups="project.group_project_manager")
            for name in ("owner", "colleague")
        ])
        employees = self.env["hr.employee"].create([
            {"name": user.name, "user_id": user.id} for user in users
        ])
        project = self.env["project.project"].create({"name": "Shared work fixture"})
        task = self.env["project.task"].create({
            "name": "Shared work fixture", "project_id": project.id,
            "user_ids": [(6, 0, users.ids)], "priority": "1",
        })
        owner, colleague = users
        my_day = self.env["daadit.my.day"].with_user(owner)
        other_day = my_day.with_user(colleague)
        self.assertTrue(my_day._can_plan_shared(task.with_user(owner)))
        self.assertFalse(other_day._can_plan_shared(task.with_user(colleague)))
        self.assertIn(task.id, [item["res_id"] for item in other_day._shared_elsewhere()])
        start = my_day._now() + timedelta(days=1)
        day = start.date()
        item = my_day._task_item(task, day, day, day + timedelta(days=14), my_day._tz(employees[0]))
        slot = self.env["planning.slot"].create({
            **my_day._slot_vals(employees[0], item,
                                start, start + timedelta(minutes=15), 10),
            "daadit_timer_seconds": 120,
        })
        with patch.object(type(my_day), "_payload", return_value={}):
            my_day.set_shared_work(task.id, owner.id, True, slot.id, "Onderzoek")
            self.assertTrue(other_day._can_plan_shared(task.with_user(colleague)))
            details = other_day.shared_work_details(task.id)
            self.assertEqual(details["slots"][0]["part"], "Onderzoek")
            my_day.set_shared_work(task.id, colleague.id, False)
        self.assertFalse(my_day._can_plan_shared(task.with_user(owner)))
        self.assertTrue(other_day._can_plan_shared(task.with_user(colleague)))
        self.assertEqual(slot.daadit_day_state, "done")
        self.assertEqual(slot.daadit_timer_seconds, 120)
