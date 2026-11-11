from odoo import fields, models


class PlanningRole(models.Model):
    _inherit = "planning.role"

    daadit_no_helpdesk = fields.Boolean(
        string="No helpdesk on this day",
        help="A shift with this role keeps the day free of tickets in My Day. If the shift has a "
             "project, only tasks from that project are scheduled that day.",
    )
