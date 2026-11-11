"""Geblokkeerd werk raakt niet zoek.

Wie een ticket of taak blokkeert, krijgt het na een paar werkdagen (standaard
drie) of op de opvolgdatum, wat het eerst komt, vanzelf terug in de planning
als kort opvolgblok. Is dat blok klaar, dan begint de termijn opnieuw, tot het
werk wordt vrijgegeven.
"""
from datetime import datetime, time, timedelta

from odoo import api, fields, models

from .my_day import _fmt_day

RANK_FOLLOWUP = 15


class MyDayFollowup(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _add_workdays(self, day, count):
        while count > 0:
            day += timedelta(days=1)
            if day.weekday() < 5:
                count -= 1
        return day

    def _followup_return_date(self, record):
        followup = record.daadit_day_followup_date
        since = record.daadit_day_blocked_on
        if not since:
            return followup
        back = self._add_workdays(since, max(1, self._param_int("blocked_return_days", 3)))
        return min(followup, back) if followup else back

    def _followup_due(self, record, day):
        due = self._followup_return_date(record)
        return bool(record.daadit_day_block_kind and due and due <= day)

    def _followup_item(self, record, kind, day):
        waiting = (self.env._("Waiting for customer") if record.daadit_day_block_kind == "customer"
                   else self.env._("Blocked"))
        since = record.daadit_day_blocked_on
        since_label = self.env._(" since %s", _fmt_day(self.env, since)) if since else ""
        reason = self.env._("%s%s · follow up", waiting, since_label)
        if kind == "ticket":
            ref = (record.ticket_ref or str(record.id)).lstrip("0") or str(record.id)
            partner = record.partner_id
            subtitle = "Ticket %s · %s" % (
                ref, partner.commercial_company_name or partner.name or self.env._("No customer"))
        else:
            subtitle = self.env._("Task %d · %s") % (record.id, record.project_id.display_name)
        due = self._followup_return_date(record) or day
        return {
            "kind": kind,
            "res_model": record._name,
            "res_id": record.id,
            "title": record.name,
            "subtitle": subtitle,
            "reason": reason,
            "next_step": record.daadit_day_block_reason or "",
            "minutes": max(5, min(60, self._param_int("followup_minutes", 15))),
            "rank": RANK_FOLLOWUP,
            "sort": (0, datetime.combine(due, time.min)),
            "followup": True,
        }

    def _followup_records(self, day, kinds=("ticket", "task")):
        uid = self.env.uid
        result = []
        for kind, model, assignee, open_filter in (
            ("ticket", "helpdesk.ticket", "user_id", ("stage_id.fold", "=", False)),
            ("task", "project.task", "user_ids", ("state", "not in", ("1_done", "1_canceled"))),
        ):
            if kind not in kinds:
                continue
            records = self.env[model].search([
                ("daadit_day_block_kind", "!=", False), open_filter,
                "|", ("daadit_day_followup_user_id", "=", uid),
                "&", ("daadit_day_followup_user_id", "=", False), (assignee, "in", [uid]),
            ], limit=200)
            result.extend((kind, record) for record in records if self._followup_due(record, day))
        return result

    def _queue(self, employee, day, tz):
        items = super()._queue(employee, day, tz)
        today_start, _end = self._bounds(self._today(tz), tz)
        planned = self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_kind", "in", ("ticket", "task")),
            ("daadit_day_state", "=", "planned"),
            ("end_datetime", ">", today_start),
        ])
        taken = {("ticket", ticket_id) for ticket_id in planned.daadit_ticket_id.ids}
        taken |= {("task", task_id) for task_id in planned.daadit_task_id.ids}
        kinds = ("task",) if self._closed_day(employee, day, tz) else ("ticket", "task")
        for kind, record in self._followup_records(day, kinds):
            if (kind, record.id) not in taken:
                items.append(self._followup_item(record, kind, day))
        items.sort(key=lambda item: (item["rank"], item["sort"]))
        return items

    def _unschedule_blocked(self, slot):
        record = slot.daadit_ticket_id or slot.daadit_task_id
        still_open = record and (
            not record.stage_id.fold if record._name == "helpdesk.ticket"
            else record.state not in ("1_done", "1_canceled"))
        if (not self.env.context.get("daadit_day_force_unschedule") and still_open
                and self._followup_due(record, self._today(self._tz(self._employee())))):
            return
        return super()._unschedule_blocked(slot)

    @api.model
    def set_work_blocked(self, kind, record_id, reason_kind, reason, followup, user_id, day=None):
        record = self._editable_work_record(kind, record_id)
        # Each new or changed blockade restarts the waiting period.
        record.daadit_day_blocked_on = self._today(self._tz(self._employee()))
        return super(MyDayFollowup, self.with_context(
            daadit_day_force_unschedule=True,
        )).set_work_blocked(kind, record_id, reason_kind, reason, followup, user_id, day=day)

    @api.model
    def unblock_work(self, kind, record_id, day=None):
        record = self._editable_work_record(kind, record_id)
        record.daadit_day_blocked_on = False
        return super().unblock_work(kind, record_id, day=day)

    def _blocking_item(self, record, kind, today):
        item = super()._blocking_item(record, kind, today)
        due = self._followup_return_date(record)
        item.update(
            followup_due=bool(due and due <= today),
            return_date=fields.Date.to_string(due) if due else False,
            blocked_on=fields.Date.to_string(record.daadit_day_blocked_on)
            if record.daadit_day_blocked_on else False,
        )
        return item

    def _mark_done(self, slot):
        """Een afgerond opvolgblok start de wachttermijn opnieuw."""
        super()._mark_done(slot)
        record = slot.daadit_ticket_id or slot.daadit_task_id
        if not record:
            return
        record = record.with_env(self.env)
        today = self._today(self._tz(self._employee()))
        if record.daadit_day_block_kind and self._followup_due(record, today):
            days = max(1, self._param_int("blocked_return_days", 3))
            record.sudo().write({
                "daadit_day_blocked_on": today,
                "daadit_day_followup_date": self._add_workdays(today, days),
            })

    def _slot_payload(self, slot, tz, now, capacity_gaps):
        payload = super()._slot_payload(slot, tz, now, capacity_gaps)
        record = slot.daadit_ticket_id or slot.daadit_task_id
        if record and record.daadit_day_block_kind:
            today = self._local_day(now, tz)
            item = self._blocking_item(record, slot.daadit_day_kind, today)
            payload.update({
                key: item[key] for key in (
                    "blocked", "followup_date", "followup_user_id", "followup_user",
                    "followup_due", "return_date", "blocked_on",
                )
            })
            payload["block_reason"] = item["reason"]
        return payload
