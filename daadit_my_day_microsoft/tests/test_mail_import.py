from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestMyDayMailImport(common.TransactionCase):
    def test_selected_thread_creates_one_activity_and_keeps_mail_private(self):
        user = new_test_user(self.env, login="my_day_mail", groups="project.group_project_manager")
        other = new_test_user(
            self.env, login="my_day_mail_other", groups="project.group_project_user",
        )
        user.daadit_day_mail_account_id = "own-graph-account"
        self.env["hr.employee"].create({"name": user.name, "user_id": user.id})
        project = self.env["project.project"].create({"name": "Mail fixture"})
        task = self.env["project.task"].create({
            "name": "Mail fixture", "project_id": project.id, "user_ids": [(6, 0, [user.id])],
        })
        wizard = self.env["daadit.my.day.mail.import"].with_user(user).create({"task_id": task.id})
        graph = self.env["daadit.my.day.graph"]
        messages = [
            {"id": "latest", "conversationId": "thread", "subject": "Recent bericht"},
            {"id": "earlier", "conversationId": "thread", "subject": "Ouder bericht"},
        ]
        selected = {**messages[0], "body": {"contentType": "text", "content": "Privétekst"}}
        with patch.object(type(graph), "_inbox", return_value=messages), patch.object(
            type(graph), "_message", return_value=selected,
        ):
            wizard.action_refresh()
            self.assertEqual(wizard.line_ids.graph_id, "latest")
            wizard.message_id = wizard.line_ids
            linked = wizard.action_link()["res_id"]
            self.assertEqual(wizard.action_link()["res_id"], linked)
        work = self.env["daadit.my.day.mail.work"].with_user(user).browse(linked)
        self.assertEqual(work.body, "Privétekst")
        self.assertEqual(work.task_id, task)
        self.assertEqual(work.activity_id.user_id, user)
        self.assertEqual(len(task.activity_ids), 1)
        work.draft = "Mijn eigen concept"
        with self.assertRaises(AccessError):
            work.with_user(other).read(["draft"])
        with self.assertRaises(AccessError):
            work.write({"conversation_id": "other-thread"})

    def test_authorization_binds_mailbox_to_current_odoo_user(self):
        user = new_test_user(
            self.env, login="my_day_mail_oauth", groups="base.group_user",
            email="owner@example.test",
        )
        graph = self.env["daadit.my.day.graph"].with_user(user)
        with self.assertRaises(AccessError):
            graph._configuration()
        payload = {"refresh_token": "fixture-refresh", "access_token": "fixture-access"}
        config = {"redirect_uri": "fixture"}
        with (
            patch.object(type(graph), "_configuration", return_value=config),
            patch.object(type(graph), "_token", return_value=payload),
            patch.object(type(graph), "_get") as get_account,
        ):
            get_account.return_value = {"id": "wrong", "mail": "other@example.test"}
            with self.assertRaises(AccessError):
                graph._connect("fixture-code", "fixture-verifier")
            self.assertFalse(user.daadit_day_mail_refresh_token)
            get_account.return_value = {"id": "owner", "mail": "OWNER@example.test"}
            graph._connect("fixture-code", "fixture-verifier")
        self.assertEqual(user.daadit_day_mail_account_id, "owner")
        self.assertEqual(user.daadit_day_mail_refresh_token, "fixture-refresh")
