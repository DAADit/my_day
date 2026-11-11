from psycopg2 import IntegrityError

from odoo import fields, models
from odoo.exceptions import AccessError, UserError


class MyDayMailWork(models.Model):
    _name = "daadit.my.day.mail.work"
    _description = "Selected Microsoft 365 mail"
    _order = "id desc"
    _rec_name = "subject"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    account_id = fields.Char(required=True)
    conversation_id = fields.Char(required=True)
    subject = fields.Char(required=True)
    task_id = fields.Many2one("project.task", ondelete="set null")
    ticket_id = fields.Many2one("helpdesk.ticket", ondelete="set null")
    activity_id = fields.Many2one("mail.activity", ondelete="set null")
    body = fields.Text()
    draft = fields.Text()

    _unique_conversation = models.Constraint(
        "UNIQUE(user_id, account_id, conversation_id)",
        "This conversation has already been added as work.",
    )

    def write(self, values):
        if not self.env.su and set(values) - {"draft"}:
            raise AccessError(self.env._("You can only edit your own reply draft."))
        return super().write(values)


class MyDayMailImport(models.TransientModel):
    _name = "daadit.my.day.mail.import"
    _description = "Choose a Microsoft 365 message"

    line_ids = fields.One2many("daadit.my.day.mail.line", "wizard_id", readonly=True)
    message_id = fields.Many2one(
        "daadit.my.day.mail.line", string="Message",
        domain="[('wizard_id', '=', id)]",
    )
    ticket_id = fields.Many2one(
        "helpdesk.ticket", string="Ticket",
        domain="[('user_id', '=', uid), ('stage_id.fold', '=', False)]",
    )
    task_id = fields.Many2one(
        "project.task", string="Task",
        domain="[('user_ids', 'in', [uid]), ('state', 'not in', ['1_done', '1_canceled'])]",
    )

    def action_connect(self):
        self.ensure_one()
        self.check_access("write")
        self.env["daadit.my.day.graph"]._configuration()
        return {
            "type": "ir.actions.act_url", "url": "/my-day/microsoft/authorize", "target": "self",
        }

    def action_refresh(self):
        self.ensure_one()
        self.check_access("write")
        threads = {}
        for message in self.env["daadit.my.day.graph"]._inbox():
            if message.get("conversationId") and message.get("id"):
                threads.setdefault(message["conversationId"], message)
        self.line_ids.unlink()
        self.message_id = False
        self.env["daadit.my.day.mail.line"].create([{
            "wizard_id": self.id, "graph_id": message["id"],
            "conversation_id": message["conversationId"],
            "name": message.get("subject") or self.env._("(No subject)"),
            "sender": ((message.get("from") or {}).get("emailAddress") or {}).get("address"),
            "received": message.get("receivedDateTime"),
            "preview": message.get("bodyPreview"),
        } for message in threads.values()])
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
            "views": [(False, "form")], "target": "current",
            "name": self.env._("Microsoft 365 mail"),
        }

    def action_link(self):
        self.ensure_one()
        self.check_access("write")
        if not self.message_id or self.message_id.wizard_id != self:
            raise UserError(self.env._("Choose a message from your own inbox."))
        if bool(self.ticket_id) == bool(self.task_id):
            raise UserError(self.env._("Choose one ticket or one task."))
        my_day = self.env["daadit.my.day"]
        source = my_day._editable_work_record(
            "ticket" if self.ticket_id else "task", (self.ticket_id or self.task_id).id,
        )
        graph = self.env["daadit.my.day.graph"]
        message = graph._message(self.message_id.graph_id)
        conversation = message.get("conversationId")
        if not conversation or conversation != self.message_id.conversation_id:
            raise UserError(self.env._("The selected message has changed. Reload your inbox."))
        body = message.get("body") or {}
        if body.get("contentType", "").lower() != "text":
            raise UserError(self.env._("Microsoft 365 did not provide the text of this message."))
        user = self.env.user
        subject = (message.get("subject") or self.env._("(No subject)"))[:200]
        work_values = {
            "user_id": user.id, "account_id": user.sudo().daadit_day_mail_account_id,
            "conversation_id": conversation, "subject": subject,
            "task_id": self.task_id.id, "ticket_id": self.ticket_id.id,
            "body": (body.get("content") or "")[:50000],
        }
        if not work_values["account_id"]:
            raise UserError(self.env._("Reconnect your Microsoft 365 account."))
        Work = self.env["daadit.my.day.mail.work"]
        domain = [(name, "=", work_values[name]) for name in (
            "user_id", "account_id", "conversation_id",
        )]
        work = Work.search(domain, limit=1)
        if not work:
            try:
                with self.env.cr.savepoint():
                    work = Work.sudo().create(work_values)
                    employee = my_day._employee()
                    activity = self.env["mail.activity"].create({
                        "res_model_id": self.env["ir.model"]._get_id(source._name),
                        "res_id": source.id, "user_id": user.id,
                        "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                        "summary": self.env._("Follow up on Microsoft 365 message"),
                        "date_deadline": my_day._today(my_day._tz(employee)),
                    })
                    work.activity_id = activity
            except IntegrityError as error:
                if error.diag.constraint_name != "daadit_my_day_mail_work_unique_conversation":
                    raise
                work = Work.search(domain, limit=1)
                if not work:
                    raise UserError(self.env._(
                        "This conversation was just added. Open Selected mail."
                    ))
        return {
            "type": "ir.actions.act_window", "res_model": Work._name, "res_id": work.id,
            "views": [(False, "form")], "target": "current", "name": self.env._("Selected mail"),
        }


class MyDayMailLine(models.TransientModel):
    _name = "daadit.my.day.mail.line"
    _description = "Message in the Microsoft 365 inbox"
    _order = "received desc"

    wizard_id = fields.Many2one("daadit.my.day.mail.import", required=True, ondelete="cascade")
    graph_id = fields.Char(required=True)
    conversation_id = fields.Char(required=True)
    name = fields.Char(required=True)
    sender = fields.Char()
    received = fields.Char()
    preview = fields.Text()
