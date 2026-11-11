from odoo import fields, models


class PlanningSlot(models.Model):
    _inherit = "planning.slot"

    daadit_day_kind = fields.Selection(
        [("ticket", "Ticket"), ("task", "Task"), ("own", "Personal work"),
         ("activity", "Activity")],
        string="My Day block type",
        index=True,
        copy=False,
    )
    daadit_ticket_id = fields.Many2one(
        "helpdesk.ticket", string="Ticket", ondelete="cascade", index="btree_not_null", copy=False,
    )
    daadit_task_id = fields.Many2one(
        "project.task", string="Task", ondelete="cascade", index="btree_not_null", copy=False,
    )
    daadit_activity_id = fields.Many2one(
        "mail.activity", string="Odoo activity", ondelete="set null", index=True, copy=False,
    )
    daadit_activity_res_model = fields.Char(copy=False)
    daadit_activity_res_id = fields.Integer(copy=False)
    daadit_day_state = fields.Selection(
        [("planned", "Scheduled"), ("done", "Done")],
        string="My Day status",
        default="planned",
        copy=False,
    )
    daadit_day_pinned = fields.Boolean(
        string="Pinned",
        copy=False,
        help="This block stays in place when the day is rescheduled.",
    )
    daadit_day_outside_hours = fields.Boolean(
        string="Scheduled outside working hours", copy=False,
    )
    daadit_day_work_part = fields.Char(string="My part of the task", copy=False)
    daadit_day_sequence = fields.Integer(string="My Day order", default=100, copy=False)
    daadit_day_reason = fields.Char(string="Why today", copy=False)
    daadit_day_done_at = fields.Datetime(string="Done on", copy=False)
    daadit_timer_started_at = fields.Datetime(string="Timer started at", copy=False, index=True)
    daadit_timer_beat_at = fields.Datetime(string="Last timer heartbeat", copy=False)
    daadit_timer_seconds = fields.Integer(string="Elapsed seconds", copy=False)
    daadit_day_time_history = fields.Json(string="Time corrections", copy=False)
    daadit_no_hours = fields.Boolean(string="Do not log time", copy=False)
    daadit_timesheet_id = fields.Many2one(
        "account.analytic.line", string="Timesheet entry", ondelete="set null", copy=False,
    )


class ResourceResource(models.Model):
    _inherit = "resource.resource"

    daadit_day_timer_revision = fields.Integer(copy=False)
