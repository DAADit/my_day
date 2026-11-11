from datetime import datetime, time, timedelta

from odoo import api, models
from odoo.exceptions import UserError

from .my_day import HORIZON_DAYS, RANK_SOON

# An Odoo activity is a concrete promise with a date, so on its due day it goes
# before ordinary tickets and tasks; only urgent tickets come first.
RANK_ACTIVITY_DUE = 5
WORK_MODELS = {"helpdesk.ticket": "ticket", "project.task": "task"}


class MyDayActivities(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _readable_activity_source(self, model, record_id):
        if not model or model not in self.env or not record_id:
            return False
        record = self.env[model].browse(record_id).exists()
        return record if record and record.has_access("read") else False

    def _activity_item(self, activity, day):
        record = self._readable_activity_source(activity.res_model, activity.res_id)
        if not record:
            return None
        due = activity.date_deadline
        duration = max(5, min(60, self._param_int("activity_minutes", 15)))
        return {
            "kind": "activity",
            "activity_id": activity.id,
            "res_model": record._name,
            "res_id": record.id,
            "title": activity.summary or activity.activity_type_id.display_name,
            "subtitle": record.display_name,
            "reason": self.env._("Follow up by %s") % due.strftime("%d-%m-%Y"),
            "rank": self._activity_rank(due, day),
            "sort": (0, datetime.combine(due, time.min)),
            "minutes": duration,
        }

    def _activity_rank(self, due, day):
        return RANK_ACTIVITY_DUE if due <= day else RANK_SOON

    def _planned_work_sources(self, employee):
        """Tickets en taken die al een open blok hebben, als (model, id)."""
        slots = self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_kind", "in", ("ticket", "task")),
            ("daadit_day_state", "=", "planned"),
        ])
        return ({("helpdesk.ticket", rid) for rid in slots.daadit_ticket_id.ids}
                | {("project.task", rid) for rid in slots.daadit_task_id.ids})

    def _merge_activity(self, item, activity, day):
        """Neem de activiteit mee in het blok van het ticket of de taak zelf."""
        due = activity.date_deadline
        rank = self._activity_rank(due, day)
        label = activity.summary or activity.activity_type_id.display_name or self.env._("Activity")
        if rank < item["rank"]:
            item["rank"] = rank
            item["sort"] = (0, datetime.combine(due, time.min))
        item.setdefault("activity_ids", []).append(activity.id)
        prefix = self.env._("Activity due by %s: %s") % (due.strftime("%d-%m"), label)
        item["reason"] = prefix if not item.get("reason") else "%s · %s" % (prefix, item["reason"])

    def _queue(self, employee, day, tz):
        items = super()._queue(employee, day, tz)
        planned = self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_kind", "=", "activity"),
            ("daadit_day_state", "=", "planned"),
        ])
        activities = self.env["mail.activity"].search([
            ("user_id", "=", self.env.uid),
            ("date_deadline", "<=", day + timedelta(days=HORIZON_DAYS)),
            ("id", "not in", planned.daadit_activity_id.ids),
        ], order="date_deadline, id", limit=200)
        work = {(item["res_model"], item["res_id"]): item
                for item in items if item["kind"] in ("ticket", "task")}
        in_blocks = self._planned_work_sources(employee)
        for activity in activities:
            source = (activity.res_model, activity.res_id)
            if source in work:
                self._merge_activity(work[source], activity, day)
                continue
            if source in in_blocks:
                # Already in a ticket or task block of its own: no second block.
                continue
            item = self._activity_item(activity, day)
            if item:
                items.append(item)
        return sorted(items, key=lambda item: (item["rank"], item["sort"]))

    def _slot_vals(self, employee, item, start, end, sequence, pinned=False):
        values = super()._slot_vals(employee, item, start, end, sequence, pinned)
        if item["kind"] == "activity":
            values.update(
                daadit_activity_id=item["activity_id"],
                daadit_activity_res_model=item["res_model"],
                daadit_activity_res_id=item["res_id"],
                daadit_no_hours=True,
            )
        return values

    def _slot_payload(self, slot, tz, now, capacity_gaps):
        payload = super()._slot_payload(slot, tz, now, capacity_gaps)
        if slot.daadit_day_kind == "activity":
            record = self._readable_activity_source(
                slot.daadit_activity_res_model, slot.daadit_activity_res_id,
            )
            if record:
                payload.update(res_model=record._name, res_id=record.id)
            return payload
        record = slot.daadit_ticket_id or slot.daadit_task_id
        if record:
            activities = self.env["mail.activity"].search([
                ("user_id", "=", self.env.uid), ("res_model", "=", record._name),
                ("res_id", "=", record.id),
            ], order="date_deadline, id")
            payload["activities"] = [{
                "id": activity.id,
                "title": activity.summary or activity.activity_type_id.display_name,
                "due": activity.date_deadline.strftime("%d-%m-%Y"),
                "overdue": activity.date_deadline < self._local_day(now, tz),
            } for activity in activities]
        return payload

    @api.model
    def complete_activity(self, activity_id, day=None):
        """Voltooi een activiteit die in een ticket- of taakblok is meegenomen."""
        activity = self.env["mail.activity"].browse(int(activity_id)).exists()
        if not activity or activity.user_id != self.env.user:
            raise UserError(self.env._("This activity is no longer open and assigned to you."))
        if not self._readable_activity_source(activity.res_model, activity.res_id):
            raise UserError(self.env._("You no longer have access to the linked record."))
        activity.action_feedback()
        employee = self._employee()
        tz = self._tz(employee)
        return self._payload(
            employee, self._parse_day(day, tz), tz, message=self.env._("Activity completed."),
        )

    def _prune_activity(self, slot):
        activity = slot.daadit_activity_id
        record = self._readable_activity_source(
            slot.daadit_activity_res_model, slot.daadit_activity_res_id,
        )
        if (not activity or activity.user_id != self.env.user or not record
                or ("active" in activity._fields and not activity.active)):
            self._remove_planned_slot(slot, self.env._("Time worked before handover or completion"))
            return
        employee = self._employee()
        if (record._name, record.id) in self._planned_work_sources(employee):
            self._remove_planned_slot(slot, self.env._("Included in the ticket or task block"))

    def _booking_target(self, slot):
        if slot.daadit_day_kind == "activity":
            return False, self.env._("Activities do not log time")
        return super()._booking_target(slot)

    def _close_record(self, slot):
        if slot.daadit_day_kind != "activity":
            return super()._close_record(slot)
        activity = self.env["mail.activity"].browse(slot.daadit_activity_id.id).exists()
        if not activity or activity.user_id != self.env.user:
            raise UserError(self.env._("This activity is no longer open and assigned to you."))
        if not self._readable_activity_source(activity.res_model, activity.res_id):
            raise UserError(self.env._("You no longer have access to the linked record."))
        activity.action_feedback()
        return self.env._("Activity completed.")
