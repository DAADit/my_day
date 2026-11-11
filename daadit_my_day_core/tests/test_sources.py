from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestMyDaySources(common.TransactionCase):
    def test_missing_apps_leave_the_workspace_available(self):
        user = new_test_user(self.env, login="my_day_core_user", groups="base.group_user")
        workspace = self.env["daadit.my.day.sources"].with_user(user)
        result = workspace.get_workspace()
        if "helpdesk.ticket" not in self.env:
            self.assertNotIn("ticket", workspace._available_sources())
            self.assertNotIn("ticket", {item["kind"] for item in result["items"]})
        if "project.task" not in self.env:
            self.assertNotIn("task", workspace._available_sources())
            self.assertNotIn("todo", {item["kind"] for item in result["items"]})

    def test_todo_is_listed_without_helpdesk(self):
        if not self.env.ref("project_todo.project_task_view_todo_form", raise_if_not_found=False):
            self.skipTest("To-do is not installed")
        user = new_test_user(
            self.env, login="my_day_core_todo", groups="project.group_project_user",
        )
        tasks = self.env["project.task"].with_user(user).create([
            {"name": title, "user_ids": [(6, 0, user.ids)]}
            for title in ("Customer to-do A", "Customer to-do B")
        ])
        workspace = self.env["daadit.my.day.sources"].with_user(user)
        items = workspace.get_workspace()["items"]
        visible = {item["open_id"] for item in items if item["kind"] == "todo"}
        self.assertTrue(set(tasks.ids) <= visible)
        workspace.save_order([f"task-{task.id}" for task in tasks[::-1]])
        ordered = workspace.get_workspace()["items"]
        self.assertEqual(
            [item["open_id"] for item in ordered if item["kind"] == "todo"][:2], tasks[::-1].ids,
        )

    def test_helpdesk_tickets_follow_record_permissions(self):
        if "helpdesk.ticket" not in self.env:
            self.skipTest("Helpdesk is not installed")
        user = new_test_user(
            self.env, login="my_day_core_helpdesk", groups="helpdesk.group_helpdesk_manager",
        )
        team = self.env["helpdesk.team"].create({"name": "My Day customer test"})
        ticket = self.env["helpdesk.ticket"].create({
            "name": "Customer ticket", "team_id": team.id, "user_id": user.id,
        })
        workspace = self.env["daadit.my.day.sources"].with_user(user)
        items = workspace.get_workspace()["items"]
        self.assertIn(("helpdesk.ticket", ticket.id),
                      {(item["open_model"], item["open_id"]) for item in items})
