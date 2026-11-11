from odoo import fields, models


class MyDayWorkBlocking(models.AbstractModel):
    _name = "daadit.my.day.work.blocking"
    _description = "My Day work blocking"

    daadit_day_block_kind = fields.Selection(
        [("blocked", "Blocked"), ("customer", "Waiting for customer")],
        string="My Day blocking status", index=True, copy=False, groups="base.group_user",
    )
    daadit_day_block_reason = fields.Char(
        string="Reason for blocking", copy=False, groups="base.group_user",
    )
    daadit_day_followup_date = fields.Date(
        string="Follow-up date", copy=False, groups="base.group_user",
    )
    daadit_day_followup_user_id = fields.Many2one(
        "res.users", string="Follow-up owner",
        index=True, copy=False, groups="base.group_user",
    )

    daadit_day_blocked_on = fields.Date(
        string="Blocked since", copy=False, groups="base.group_user",
    )
    daadit_day_estimate_minutes = fields.Integer(
        string="Remaining estimate in minutes", copy=False, groups="base.group_user",
    )
    daadit_day_estimate_anchor = fields.Integer(copy=False, groups="base.group_user")
    daadit_day_estimate_block_max = fields.Integer(
        string="Maximum block duration in minutes", copy=False, groups="base.group_user",
    )


class HelpdeskTicket(models.Model):
    _name = "helpdesk.ticket"
    _inherit = ["helpdesk.ticket", "daadit.my.day.work.blocking"]


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "daadit.my.day.work.blocking"]
