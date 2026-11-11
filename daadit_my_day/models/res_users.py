from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    daadit_my_day_planned_on = fields.Date(
        string="My Day last scheduled on",
        copy=False,
    )
