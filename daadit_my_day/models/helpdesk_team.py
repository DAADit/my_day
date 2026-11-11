from odoo import fields, models


class HelpdeskTeam(models.Model):
    _inherit = "helpdesk.team"

    daadit_day_ticket_minutes = fields.Integer(
        string="My Day: time per ticket (minutes)",
        help="My Day reserves this much time for a ticket from this team. Leave blank or enter 0 "
             "to use the general default. Match this to the team’s SLA.",
    )

    _daadit_day_ticket_minutes_range = models.Constraint(
        "CHECK(daadit_day_ticket_minutes >= 0 AND daadit_day_ticket_minutes <= 480)",
        "Time per ticket must be between 0 and 480 minutes.",
    )
