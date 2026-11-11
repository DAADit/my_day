from datetime import timedelta

from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestActivityWork(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="my_day_activity_user", groups="project.group_project_manager",
        )
        cls.employee = cls.env["hr.employee"].create({
            "name": "My Day activity fixture", "user_id": cls.user.id,
        })
        cls.model = cls.env["daadit.my.day"].with_user(cls.user)
        cls.tz = cls.model._tz(cls.employee)
        cls.today = cls.model._today(cls.tz)
        project = cls.env["project.project"].create({"name": "Activity fixture"})
        cls.task = cls.env["project.task"].create({
            "name": "Activity fixture", "project_id": project.id,
        })
        cls.activities = cls.env["mail.activity"].create([
            {"activity_type_id": cls.env.ref("mail.mail_activity_data_todo").id,
             "res_model_id": cls.env["ir.model"]._get_id("project.task"), "res_id": cls.task.id,
             "summary": summary, "user_id": cls.user.id, "date_deadline": cls.today}
            for summary in ("Activity fixture A", "Activity fixture B")
        ])

    def test_distinct_activities_and_completion_does_not_close_source(self):
        activities = [i for i in self.model._queue(self.employee, self.today, self.tz)
                      if i["kind"] == "activity"]
        self.assertEqual({i["activity_id"] for i in activities}, set(self.activities.ids))
        item = activities[0]
        self.assertEqual(item["res_id"], self.task.id)
        start, _ = self.model._bounds(self.today, self.tz)
        vals = self.model._slot_vals(self.employee, item, start, start + timedelta(minutes=15), 10)
        slot = self.env["planning.slot"].create(vals)
        queued = self.model._queue(self.employee, self.today, self.tz)
        self.assertNotIn(item["activity_id"], [i.get("activity_id") for i in queued])
        state = self.task.state
        self.assertEqual(self.model._close_record(slot), self.model.env._("Activity completed."))
        self.assertFalse(self.model.env["mail.activity"].search([("id", "=", item["activity_id"])]))
        self.assertEqual(self.task.state, state)
        self.assertFalse(slot.daadit_timesheet_id)
        self.assertEqual(slot.daadit_activity_res_id, self.task.id)
