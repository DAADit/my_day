from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPrioritiesAndFollowup(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="my_day_priority_user",
            groups="project.group_project_manager,helpdesk.group_helpdesk_manager",
        )
        cls.employee = cls.env["hr.employee"].create({
            "name": "My Day priority fixture", "user_id": cls.user.id,
        })
        cls.model = cls.env["daadit.my.day"].with_user(cls.user)
        cls.tz = cls.model._tz(cls.employee)
        cls.today = cls.model._today(cls.tz)
        cls.project = cls.env["project.project"].create({"name": "Priority fixture"})
        cls.tasks = cls.env["project.task"].create([
            {"name": "Priority task %s" % name, "project_id": cls.project.id,
             "user_ids": [(6, 0, cls.user.ids)]}
            for name in ("A", "B", "C")
        ])
        cls.support = cls.env["helpdesk.team"].create({
            "name": "Support fixture", "daadit_day_ticket_minutes": 10,
        })
        cls.service = cls.env["helpdesk.team"].create({
            "name": "Service fixture", "daadit_day_ticket_minutes": 30,
        })
        cls.ticket = cls.env["helpdesk.ticket"].create({
            "name": "Support fixture ticket", "team_id": cls.support.id, "user_id": cls.user.id,
        })

    def _keys(self, day=None):
        return [(item["kind"], item["res_id"])
                for item in self.model._queue(self.employee, day or self.today, self.tz)]

    def test_team_priorities_show_and_reassign_shared_tasks(self):
        colleague, recipient = (
            new_test_user(
                self.env, login=f"my_day_team_{name}", groups="project.group_project_user",
            )
            for name in ("colleague", "recipient")
        )
        self.env["hr.employee"].create([
            {"name": user.name, "user_id": user.id} for user in (colleague, recipient)
        ])
        task = self.tasks[0]
        task.user_ids = [(4, colleague.id)]
        items = self.model.get_priorities("all")["items"]
        item = next(row for row in items if row["key"] == f"project.task-{task.id}")
        self.assertEqual({user["id"] for user in item["assignees"]}, {self.user.id, colleague.id})
        self.assertEqual((item["open_model"], item["open_id"]), ("project.task", task.id))
        self.model.set_employee_priorities(colleague.id, [["project.task", task.id]])
        with self.assertRaises(UserError):
            self.model.with_user(recipient).set_employee_priorities(
                colleague.id, [["project.task", task.id]],
            )
        self.model.assign_priority("project.task", task.id, recipient.id, colleague.id)
        self.assertEqual(set(task.user_ids.ids), {self.user.id, recipient.id})

    # Reserveringstijd per team -------------------------------------------------
    def test_ticket_minutes_follow_the_team(self):
        item = next(i for i in self.model._queue(self.employee, self.today, self.tz)
                    if i["kind"] == "ticket" and i["res_id"] == self.ticket.id)
        self.assertEqual(item["minutes"], 10)
        self.ticket.team_id = self.service
        self.assertEqual(self.model._ticket_minutes(self.ticket), 30)
        self.service.daadit_day_ticket_minutes = 0
        self.env["ir.config_parameter"].sudo().set_param("daadit_my_day.ticket_minutes", "25")
        self.assertEqual(self.model._ticket_minutes(self.ticket), 25)

    # Geblokkeerd werk komt terug -----------------------------------------------
    def _block(self, record, kind, followup):
        with patch.object(type(self.model), "_payload", return_value={}):
            self.model.set_work_blocked(
                kind, record.id, "customer", "Antwoord klant nodig", followup, self.user.id,
            )

    def test_blocked_work_returns_after_three_workdays(self):
        task = self.tasks[0]
        friday = date(2026, 11, 13)
        with patch.object(type(self.model), "_now", return_value=datetime(2026, 11, 13, 9)):
            self._block(task, "task", friday + timedelta(days=30))
        self.assertEqual(task.daadit_day_blocked_on, friday)
        back = date(2026, 11, 18)
        self.assertEqual(self.model._followup_return_date(task), back)
        self.assertNotIn(("task", task.id), self._keys(back - timedelta(days=1)))
        item = next(i for i in self.model._queue(self.employee, back, self.tz)
                    if i["kind"] == "task" and i["res_id"] == task.id)
        self.assertTrue(item["followup"])
        self.assertIn(self.model.env._("Waiting for customer"), item["reason"])
        self.assertEqual(task.daadit_day_block_kind, "customer")
        self.employee.resource_calendar_id = self.env["resource.calendar"].create({
            "name": "Follow-up test roster",
            "tz": "Europe/Amsterdam",
            "attendance_ids": [(5, 0, 0), (0, 0, {
                "name": "Wednesday", "dayofweek": "2", "day_period": "afternoon",
                "hour_from": 13, "hour_to": 17,
            })],
        })
        with patch.object(type(self.model), "_now", return_value=datetime(2026, 11, 18, 9)):
            payload = self.model.get_day()
        block = next(b for b in payload["blocks"] if b["kind"] == "task" and b["res_id"] == task.id)
        self.assertTrue(block["followup_due"])
        self.assertEqual(block["return_date"], "2026-11-18")
        self.assertEqual(block["state"], "planned")

    def test_earlier_followup_date_wins_and_done_restarts_period(self):
        task = self.tasks[1]
        tomorrow = self.today + timedelta(days=1)
        self._block(task, "task", tomorrow)
        self.assertEqual(self.model._followup_return_date(task), tomorrow)
        start, _ = self.model._bounds(self.today, self.tz)
        item = self.model._followup_item(task, "task", self.today)
        begin = start + timedelta(hours=9)
        slot = self.env["planning.slot"].create(self.model._slot_vals(
            self.employee, item, begin, begin + timedelta(minutes=15), 10,
        ))
        task.daadit_day_followup_date = self.today
        self.model._mark_done(slot)
        self.assertEqual(task.daadit_day_blocked_on, self.today)
        self.assertEqual(task.daadit_day_followup_date, self.model._add_workdays(self.today, 3))
        self.assertEqual(task.daadit_day_block_kind, "customer")

    def test_release_clears_waiting_period(self):
        task = self.tasks[2]
        self._block(task, "task", self.today + timedelta(days=5))
        with patch.object(type(self.model), "_payload", return_value={}):
            self.model.unblock_work("task", task.id)
        self.assertFalse(task.daadit_day_blocked_on)
        self.assertIn(("task", task.id), self._keys())

    # Activiteiten ------------------------------------------------------------------
    def test_activity_merges_into_its_task_and_goes_first(self):
        task = self.tasks[2]
        activity = self.env["mail.activity"].create({
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "res_model_id": self.env["ir.model"]._get_id("project.task"), "res_id": task.id,
            "summary": "Klant terugbellen", "user_id": self.user.id, "date_deadline": self.today,
        })
        queue = self.model._queue(self.employee, self.today, self.tz)
        self.assertNotIn(activity.id, [i.get("activity_id") for i in queue])
        item = next(i for i in queue if i["kind"] == "task" and i["res_id"] == task.id)
        self.assertEqual(item["activity_ids"], activity.ids)
        self.assertIn("Klant terugbellen", item["reason"])
        others = [i for i in queue if i["kind"] in ("task", "ticket") and i is not item
                  and i["rank"] != 0]
        self.assertTrue(all(item["rank"] < other["rank"] for other in others))

    # Eigen volgorde ----------------------------------------------------------------
    def test_own_order_drives_the_queue(self):
        a, b, c = self.tasks
        self.model.set_priorities([["project.task", c.id], ["project.task", a.id]])
        tasks = [key for key in self._keys() if key[0] == "task"]
        self.assertLess(tasks.index(("task", c.id)), tasks.index(("task", a.id)))
        queue = self.model._queue(self.employee, self.today, self.tz)
        item_c = next(i for i in queue if i["res_id"] == c.id and i["kind"] == "task")
        self.assertTrue(item_c["manual"])
        self.assertIn(("task", b.id), tasks)
        self.model.reset_priorities()
        self.assertFalse(self.model._priority_map())

    def test_urgent_ticket_stays_first(self):
        urgent = self.env["helpdesk.ticket"].create({
            "name": "Urgent fixture", "team_id": self.service.id, "user_id": self.user.id,
            "priority": "3",
        })
        self.model.set_priorities([["project.task", self.tasks[0].id]])
        self.assertEqual(self._keys()[0], ("ticket", urgent.id))

    def test_priorities_reject_foreign_models(self):
        with self.assertRaises(UserError):
            self.model.set_priorities([["res.partner", self.user.partner_id.id]])

    def test_priority_list_has_open_work(self):
        result = self.model.get_priorities()
        keys = {item["key"] for item in result["items"]}
        self.assertIn("project.task-%d" % self.tasks[0].id, keys)
        self.assertIn("helpdesk.ticket-%d" % self.ticket.id, keys)

    def test_priorities_distinguish_todos_from_project_tasks(self):
        view = self.env.ref(
            "project_todo.project_task_view_todo_form", raise_if_not_found=False,
        )
        if not view:
            self.skipTest("Odoo To-do is not installed")
        todo = self.env["project.task"].with_user(self.user).create({
            "name": "Priority to-do fixture", "user_ids": [(6, 0, self.user.ids)],
        })
        for scope in (None, "all"):
            with self.subTest(scope=scope):
                result = self.model.get_priorities(scope)
                self.assertTrue(result["has_todo"])
                items = {item["key"]: item for item in result["items"]}
                self.assertEqual(items[f"project.task-{self.tasks[0].id}"]["kind"], "task")
                row = items[f"project.task-{todo.id}"]
                self.assertEqual(row["kind"], "todo")
                self.assertEqual(row["res_model"], "project.task")
                self.assertEqual((row["open_model"], row["open_id"]), ("project.task", todo.id))
                self.assertEqual(row["open_view_id"], view.id)

    # Gantt --------------------------------------------------------------------------
    def test_gantt_marks_moves(self):
        before = [{"date": "2026-11-11", "weekday": "wo", "items": [{
            "key": "slot-1", "identity": "task-7", "kind": "task", "state": "planned",
            "start": 540, "end": 600, "time": "09:00–10:00", "title": "X",
        }]}]
        after = [{"date": "2026-11-11", "weekday": "wo", "items": [{
            "key": "slot-2", "identity": "task-7", "kind": "task", "state": "planned",
            "start": 660, "end": 720, "time": "11:00–12:00", "title": "X",
        }]}]
        rows = self.model._gantt_compare(before, after)
        statuses = {item["status"] for item in rows[0]["items"]}
        self.assertEqual(statuses, {"moved", "moved_away"})
        moved = next(item for item in rows[0]["items"] if item["status"] == "moved")
        self.assertEqual(moved["from"], "wo 09:00–10:00")
        self.assertTrue(self.model.get_gantt(self.today)["rows"])
