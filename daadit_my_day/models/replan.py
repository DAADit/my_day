from collections import defaultdict
from hashlib import sha256
from json import dumps

from odoo import api, fields, models
from odoo.exceptions import UserError

PLAN_FIELDS = (
    "name", "start_datetime", "end_datetime", "daadit_day_kind", "daadit_ticket_id",
    "daadit_task_id", "daadit_activity_id", "daadit_activity_res_model", "daadit_activity_res_id",
    "daadit_day_pinned", "daadit_day_outside_hours", "daadit_day_sequence", "daadit_day_reason",
    "daadit_day_work_part", "daadit_day_goal_date", "daadit_day_focus",
    "daadit_day_focus_interruptible",
    "daadit_no_hours",
)


class PreviewRollback(Exception):
    pass


class ResourceResource(models.Model):
    _inherit = "resource.resource"

    daadit_day_last_replan = fields.Json(copy=False, groups="base.group_system")


class MyDayReplan(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _replan_snapshot(self, employee):
        slots = self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id), ("daadit_day_kind", "!=", False),
            ("daadit_day_state", "=", "planned"),
        ], order="start_datetime, id")
        result = {}
        for slot in slots:
            values = slot.read(PLAN_FIELDS, load=None)[0]
            values.pop("id")
            for name in ("start_datetime", "end_datetime", "daadit_day_goal_date"):
                if values[name]:
                    values[name] = str(values[name])
            source = slot.daadit_ticket_id or slot.daadit_task_id or slot.daadit_activity_id
            result[str(slot.id)] = {
                "values": values,
                "source_version": fields.Datetime.to_string(source.write_date) if source else False,
                "worked": bool(slot.daadit_timer_started_at or slot.daadit_timer_seconds
                               or slot.daadit_timesheet_id or slot.daadit_day_time_history),
            }
        return result

    def _replan_changes(self, before, after, tz):
        def group(rows):
            groups = defaultdict(list)
            for key, row in rows.items():
                values = row["values"]
                source = (values["daadit_ticket_id"] or values["daadit_task_id"]
                          or values["daadit_activity_id"])
                groups[(values["daadit_day_kind"], source or key)].append(values)
            return groups

        def label(values):
            if values:
                start = self._local(fields.Datetime.to_datetime(values["start_datetime"]), tz)
                end = self._local(fields.Datetime.to_datetime(values["end_datetime"]), tz)
                return "%s–%s" % (start.strftime("%d-%m %H:%M"), end.strftime("%H:%M"))
            return False

        previous, current = group(before), group(after)
        changes = []
        for key in sorted(previous.keys() | current.keys(), key=str):
            old = previous[key]
            new = current[key]
            # Match identical intervals first so adding or removing a split block
            # doesn't mark the other blocks as shifted.
            for row in old[:]:
                match = next((item for item in new if label(item) == label(row)), None)
                if match:
                    old.remove(row)
                    new.remove(match)
            for index in range(max(len(old), len(new))):
                prior = old[index] if index < len(old) else None
                next_row = new[index] if index < len(new) else None
                row = next_row or prior
                old_time, new_time = label(prior), label(next_row)
                if not next_row:
                    reason = self.env._("No block in the new schedule")
                elif not prior:
                    reason = row["daadit_day_reason"] or self.env._(
                        "Scheduled in available working hours"
                    )
                elif old_time[:5] != new_time[:5]:
                    reason = self.env._("Moved to available working hours on another day")
                else:
                    reason = self.env._("Rescheduled within available working hours")
                changes.append({
                    "title": (row["name"] or "").split("\n", 1)[0],
                    "before": old_time, "after": new_time, "reason": reason,
                })
        return changes

    def _replan_context(self, day):
        employee = self._employee()
        tz = self._tz(employee)
        parsed = self._parse_day(day, tz)
        if parsed < self._today(tz):
            raise UserError(self.env._("You cannot reschedule a day in the past."))
        return employee, tz, parsed

    def _preview_days(self, employee, day, tz):
        return sorted({day, *self._horizon_days(employee, day, tz)})

    def _preview_replan(self, employee, day, tz, before):
        days = self._preview_days(employee, day, tz)
        before_rows = self._gantt_rows(employee, days, tz)
        try:
            with self.env.cr.savepoint():
                self._plan_horizon(employee, day, tz)
                changes = self._replan_changes(before, self._replan_snapshot(employee), tz)
                after_rows = self._gantt_rows(employee, days, tz)
                raise PreviewRollback
        except PreviewRollback:
            return changes, self._gantt_compare(before_rows, after_rows)

    def _replan_token(self, before, changes):
        return sha256(dumps([before, changes], sort_keys=True).encode()).hexdigest()

    @api.model
    def preview_replan(self, day=None):
        employee, tz, day = self._replan_context(day)
        self._lock_timer(employee.resource_id)
        before = self._replan_snapshot(employee)
        changes, rows = self._preview_replan(employee, day, tz, before)
        return {"changes": changes, "token": self._replan_token(before, changes), "rows": rows}

    @api.model
    def confirm_replan(self, day, token):
        employee, tz, day = self._replan_context(day)
        self._lock_timer(employee.resource_id)
        before = self._replan_snapshot(employee)
        with self.env.cr.savepoint():
            self._plan_horizon(employee, day, tz)
            after = self._replan_snapshot(employee)
            changes = self._replan_changes(before, after, tz)
            if token != self._replan_token(before, changes):
                raise UserError(self.env._("Your schedule has changed. Review the new proposal."))
        old_rows = {key: row for key, row in before.items() if after.get(key) != row}
        new_rows = {key: row for key, row in after.items() if before.get(key) != row}
        changed_rows = [*old_rows.values(), *new_rows.values()]
        safe = changes and not any(row["worked"] for row in changed_rows)
        employee.resource_id.sudo().daadit_day_last_replan = (
            {"before": old_rows, "after": new_rows, "day": day.isoformat()} if safe else False
        )
        return self._payload(employee, day, tz, message=self.env._("Schedule updated."))

    def _validate_restored_row(self, employee, tz, row, gaps_by_day):
        values = row["values"]
        start = fields.Datetime.to_datetime(values["start_datetime"])
        end = fields.Datetime.to_datetime(values["end_datetime"])
        if start <= self._now():
            raise UserError(self.env._("A previously scheduled block has already started."))
        for name, model in (
            ("daadit_ticket_id", "helpdesk.ticket"), ("daadit_task_id", "project.task"),
            ("daadit_activity_id", "mail.activity"),
        ):
            if values[name]:
                source = self.env[model].browse(values[name]).exists()
                if not source or not source.has_access("read") or (
                    fields.Datetime.to_string(source.write_date) != row["source_version"]
                ):
                    raise UserError(self.env._("The linked work has changed since then."))
        day = self._local_day(start, tz)
        if values["daadit_day_outside_hours"]:
            gaps = self._capacity_gaps([(start, end)], self._events(employee, day, tz))
            if gaps != [[start, end]]:
                raise UserError(self.env._("An appointment now uses this time."))
            return start, end
        if day not in gaps_by_day:
            gaps_by_day[day] = self._free_gaps(employee, day, tz)
        if not any(left <= start and end <= right for left, right in gaps_by_day[day]):
            raise UserError(self.env._(
                "A previously scheduled block no longer fits in the schedule."
            ))
        return start, end

    @api.model
    def undo_replan(self, day=None):
        employee, tz, day = self._replan_context(day)
        self._lock_timer(employee.resource_id)
        resource = employee.resource_id.sudo()
        saved = resource.daadit_day_last_replan
        if not saved:
            raise UserError(self.env._("There is no rescheduling to undo."))
        current = self._replan_snapshot(employee)
        old_rows = saved["before"]
        new_rows = saved["after"]
        if any(current.get(key) != row or fields.Datetime.to_datetime(
            row["values"]["start_datetime"]
        ) <= self._now() for key, row in new_rows.items()):
            raise UserError(self.env._("The scheduled blocks have changed since then."))
        Slot = self.env["planning.slot"].sudo()
        unaffected = {key: row for key, row in current.items() if key not in new_rows}
        gaps_by_day = {}
        for row in old_rows.values():
            start, end = self._validate_restored_row(employee, tz, row, gaps_by_day)
            if any(fields.Datetime.to_datetime(other["values"]["start_datetime"]) < end
                   and fields.Datetime.to_datetime(other["values"]["end_datetime"]) > start
                   for other in unaffected.values()):
                raise UserError(self.env._("Another block now uses this time."))
        Slot.browse([int(key) for key in new_rows if key not in old_rows]).unlink()
        for key, row in old_rows.items():
            values = row["values"]
            if key in new_rows:
                Slot.browse(int(key)).write(values)
            else:
                Slot.create({
                    **values, "resource_id": resource.id, "company_id": employee.company_id.id,
                    "state": "draft", "daadit_day_state": "planned",
                })
        resource.daadit_day_last_replan = False
        message = self.env._("The previous schedule was restored.")
        return self._payload(employee, day, tz, message=message)

    def _payload(self, employee, day, tz, select=None, message=None):
        payload = super()._payload(employee, day, tz, select=select, message=message)
        payload["can_undo_replan"] = bool(employee.resource_id.sudo().daadit_day_last_replan)
        return payload
