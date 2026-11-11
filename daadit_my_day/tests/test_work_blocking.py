from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestWorkBlocking(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="my_day_block_assignee",
            groups="project.group_project_manager,helpdesk.group_helpdesk_manager",
        )
        cls.day = cls.env["daadit.my.day"].with_user(cls.user)
        cls.employee = cls.env["hr.employee"].create({
            "name": "My Day blocking test", "user_id": cls.user.id,
        })
        cls.tz = cls.day._tz(cls.employee)
        cls.today = cls.day._today(cls.tz)
        cls.project = cls.env["project.project"].create({"name": "My Day blocking test"})
        cls.task = cls.env["project.task"].create({
            "name": "Blocked test task", "project_id": cls.project.id,
            "user_ids": [(6, 0, cls.user.ids)],
        })
        team = cls.env["helpdesk.team"].create({"name": "My Day blocking test"})
        cls.ticket = cls.env["helpdesk.ticket"].create({
            "name": "Waiting test ticket", "team_id": team.id, "user_id": cls.user.id,
        })

    def _block(self, kind, record, followup=None):
        with patch.object(type(self.day), "_payload", return_value={}):
            self.day.set_work_blocked(
                kind, record.id, "customer", "Antwoord nodig", followup or self.today, self.user.id,
            )

    def test_blocked_work_returns_only_as_followup_until_release(self):
        task_state, ticket_stage = self.task.state, self.ticket.stage_id
        tomorrow = self.today + timedelta(days=1)
        self._block("task", self.task, tomorrow)
        self._block("ticket", self.ticket, tomorrow)
        source_keys = {("task", self.task.id), ("ticket", self.ticket.id)}
        today_queue = self.day._queue(self.employee, self.today, self.tz)
        self.assertFalse(source_keys & {(item["kind"], item["res_id"]) for item in today_queue})
        due_queue = self.day._queue(self.employee, tomorrow, self.tz)
        followups = {(item["kind"], item["res_id"]): item for item in due_queue
                     if (item["kind"], item["res_id"]) in source_keys}
        self.assertEqual(set(followups), source_keys)
        self.assertTrue(all(item.get("followup") for item in followups.values()))
        self.assertEqual(self.task.state, task_state)
        self.assertEqual(self.ticket.stage_id, ticket_stage)
        with patch.object(type(self.day), "_payload", return_value={}):
            self.day.unblock_work("task", self.task.id)
        self.assertFalse(self.task.daadit_day_block_kind)
        queue = self.day._queue(self.employee, self.today, self.tz)
        released = next(i for i in queue if i["kind"] == "task" and i["res_id"] == self.task.id)
        self.assertFalse(released.get("followup"))

    def test_followup_becomes_due_without_changing_status(self):
        tomorrow = self.today + timedelta(days=1)
        self._block("ticket", self.ticket, tomorrow)
        item = next(i for i in self.day._blocked_work(self.today) if i["res_id"] == self.ticket.id
                    and i["kind"] == "ticket")
        self.assertFalse(item["followup_due"])
        self.assertEqual(item["followup_user_id"], self.user.id)
        self.assertEqual(item["reason"], "Antwoord nodig")
        self.assertTrue(self.day._blocking_item(self.ticket, "ticket", tomorrow)["followup_due"])
        self.assertEqual(self.ticket.daadit_day_block_kind, "customer")

    def test_block_removes_future_planning_and_preserves_worked_time(self):
        start = fields.Datetime.now() + timedelta(hours=1)
        slots = self.env["planning.slot"].create([
            {
                "name": "Blocked test planning", "resource_id": self.employee.resource_id.id,
                "daadit_day_kind": "task", "daadit_task_id": self.task.id,
                "start_datetime": start + timedelta(days=index),
                "end_datetime": start + timedelta(days=index, hours=1),
                "daadit_timer_seconds": seconds,
            }
            for index, seconds in enumerate((180, 0))
        ])
        self._block("task", self.task)
        self.assertEqual(slots.exists(), slots[0])
        self.assertEqual(slots[0].daadit_timer_seconds, 180)
        self.assertEqual(slots[0].end_datetime - slots[0].start_datetime, timedelta(minutes=3))
        self.assertEqual(slots[0].daadit_day_state, "done")
        self.assertFalse(slots[0].daadit_day_done_at)

    def test_unassigned_user_cannot_change_block(self):
        stranger = new_test_user(
            self.env, login="my_day_block_stranger", groups="project.group_project_manager",
        )
        with self.assertRaises(UserError):
            self.day.with_user(stranger).blocking_details("task", self.task.id)
