from datetime import datetime

from odoo import api, fields, models
from odoo.exceptions import UserError

from .my_day import _minutes, _subtract


class PlanningSlot(models.Model):
    _inherit = "planning.slot"

    daadit_day_goal_date = fields.Date(copy=False, index=True)
    daadit_day_focus = fields.Boolean(copy=False)
    daadit_day_focus_interruptible = fields.Boolean(copy=False)


class MyDayFocus(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _goals(self, employee, day):
        return self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_goal_date", "=", day), ("daadit_day_kind", "!=", False),
        ], order="start_datetime, id")

    @api.model
    def set_day_focus(self, block_id, goal, focus, interruptible, day=None):
        slot, employee = self._own_slot(block_id)
        self._lock_timer(employee.resource_id)
        tz = self._tz(employee)
        slot_day = self._local_day(slot.start_datetime, tz)
        if slot.daadit_day_state != "planned" or slot_day < self._today(tz):
            raise UserError(self.env._("Choose an open block for today or later."))
        goal_date = (slot.daadit_day_goal_date or slot_day) if goal is True else False
        if goal_date and len(self._goals(employee, goal_date) - slot) >= 3:
            raise UserError(self.env._("Choose up to three daily goals."))
        slot.write({
            "daadit_day_goal_date": goal_date,
            "daadit_day_focus": focus is True,
            "daadit_day_focus_interruptible": focus is True and interruptible is True,
        })
        return self._payload(employee, self._parse_day(day, tz), tz, select="slot-%d" % slot.id)

    def _urgent_focus_placement(self, employee, day, tz, item):
        if day != self._today(tz) or self._closed_day(employee, day, tz):
            return None
        candidates = self._slots(employee, day, tz, states=["planned"]).filtered(
            lambda slot: slot.daadit_day_focus and slot.daadit_day_focus_interruptible
            and not slot.daadit_day_outside_hours and not slot.daadit_timer_started_at
            and not slot.daadit_timer_seconds and slot.start_datetime > self._now(),
        ).sorted("start_datetime")
        for slot in candidates:
            gaps = self._unoccupied_gaps(
                employee, day, tz, self._free_gaps(employee, day, tz), slot,
            )
            urgent = self._take_gap(gaps, item["minutes"])
            if not urgent:
                continue
            duration = _minutes(slot.start_datetime, slot.end_datetime)
            displaced = self._take_gap(_subtract(gaps, datetime.min, urgent[1]), duration)
            placement = ((day, displaced) if displaced else self._next_available_slot(
                employee, day, tz, duration, skip_closed=slot.daadit_day_kind == "ticket",
            ))
            if placement:
                self._move_slot(slot, employee, tz, placement)
                slot.daadit_day_reason = self.env._("Focus block moved for an urgent ticket")
                return day, urgent
        return None
