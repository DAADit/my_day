from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class MyDayReview(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _review_row(self, slot, tz, is_own):
        line = self.env["account.analytic.line"].browse(slot.daadit_timesheet_id.id)
        readable_line = line if line and line.has_access("read") else line.browse()
        linked = bool(line)
        source = slot.daadit_ticket_id or slot.daadit_task_id
        if source:
            source = self.env[source._name].browse(source.id)
        elif slot.daadit_day_kind == "activity":
            source = self._readable_activity_source(
                slot.daadit_activity_res_model, slot.daadit_activity_res_id,
            )
        title = slot.name.split("\n", 1)[0] if slot.name else self.env._("Personal work")
        if source:
            if source.has_access("read"):
                title = source.display_name
            else:
                title = "Ticket" if slot.daadit_day_kind == "ticket" else self.env._("Task")
                source = False
        planned = (slot.end_datetime - slot.start_datetime).total_seconds() / 60
        expected = is_own and bool(self._booking_target(slot)[0])
        return {
            "id": slot.id,
            "kind": slot.daadit_day_kind,
            "title": title,
            "date": fields.Date.to_string(self._local_day(slot.start_datetime, tz)),
            "start": self._local(slot.start_datetime, tz).strftime("%H:%M"),
            "planned": max(0, round(planned)),
            "seconds": self._timer_info(slot)["seconds"],
            "done": slot.daadit_day_state == "done",
            "missing": bool(expected and not linked and (
                slot.daadit_day_state == "done" or slot.end_datetime <= self._now()
            )),
            "booked": readable_line.unit_amount if readable_line else 0,
            "line_id": readable_line.id,
            "can_correct": is_own and not slot.daadit_timer_started_at and (
                self._local_day(slot.start_datetime, tz) <= self._today(tz)
                and (not line or readable_line and not self._protected_timesheet(readable_line))
            ),
            "time_history": (slot.daadit_day_time_history or []) if is_own else [],
            "source_model": source._name if source else False,
            "source_id": source.id if source else False,
        }

    @api.model
    def get_day_review(self, start_date, end_date=None, employee_id=False, done_only=False):
        own = self._employee()
        tz = self._tz(own)
        start_day = self._parse_day(start_date, tz)
        end_day = self._parse_day(end_date, tz) if end_date else start_day
        if end_day < start_day or (end_day - start_day).days >= 31:
            raise UserError(self.env._("Choose a period of up to 31 days."))
        Employee = self.env["hr.employee"]
        employees = Employee.browse(own.id)
        if done_only and self.env.user.has_group("planning.group_planning_manager"):
            employees |= Employee.search([("company_id", "in", self.env.companies.ids)],
                                         order="name, id")
        selected = employees.filtered(lambda e: e.id == (employee_id or own.id))
        if not selected:
            raise AccessError(self.env._("You do not have access to this employee’s schedule."))
        is_own = selected.id == own.id
        tz = self._tz(selected.sudo() if is_own else selected)
        start, _ = self._bounds(start_day, tz)
        _, end = self._bounds(end_day, tz)
        resource = (selected.sudo() if is_own else selected).resource_id
        Slot = self.env["planning.slot"]
        domain = [
            ("resource_id", "=", resource.id), ("daadit_day_kind", "!=", False),
            ("start_datetime", ">=", start), ("start_datetime", "<", end),
        ]
        if done_only:
            domain.append(("daadit_day_state", "=", "done"))
        slots = (Slot.sudo() if is_own else Slot).search(domain, order="start_datetime, id")
        rows = [self._review_row(slot, tz, is_own) for slot in slots]
        return {
            "employees": [{"id": e.id, "name": e.name} for e in employees],
            "employee_id": selected.id,
            "can_close": is_own and not done_only and start_day == end_day == self._today(tz),
            "rows": rows,
            "seconds": sum(row["seconds"] for row in rows),
            "booked": sum(row["booked"] for row in rows),
            "missing": sum(row["missing"] for row in rows),
            "open": sum(not row["done"] for row in rows),
        }

    @api.model
    def review_day_block(self, block_id, operation, day):
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        date = self._parse_day(day, tz)
        if (date != self._today(tz) or slot.daadit_day_state != "planned"
                or self._local_day(slot.start_datetime, tz) != date):
            raise UserError(self.env._("This block is no longer open on this day."))
        if operation == "complete":
            if slot.daadit_day_kind == "activity":
                self.close_block(slot.id)
            else:
                self.complete_block(slot.id)
        elif operation == "defer":
            self.defer_block(slot.id)
        else:
            raise UserError(self.env._("Choose to finish or defer the block."))
        return self.get_day_review(day)

    @api.model
    def correct_day_time(self, block_id, minutes, reason):
        slot, employee = self._own_slot(block_id)
        self._lock_timer(employee.resource_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        reason = (reason or "").strip()
        if slot.daadit_timer_started_at or day > self._today(tz):
            raise UserError(self.env._("Stop the timer before correcting time worked."))
        if type(minutes) is not int or not 0 <= minutes <= 1440 or not reason:
            raise UserError(self.env._("Enter the minutes worked and a reason for the correction."))
        line = self.env["account.analytic.line"].browse(slot.daadit_timesheet_id.id)
        if line:
            line.check_access("write")
            if self._protected_timesheet(line):
                raise UserError(self.env._(
                    "This timesheet entry is validated, invoiced or read-only."
                ))
        before = slot.daadit_timer_seconds
        if before == minutes * 60:
            return True
        history = list(slot.daadit_day_time_history or [])
        history.append({
            "date": fields.Datetime.to_string(self._now()),
            "user": self.env.user.display_name,
            "before": before,
            "after": minutes * 60,
            "reason": reason,
        })
        slot.write({"daadit_timer_seconds": minutes * 60, "daadit_day_time_history": history})
        self._book(slot, day=day)
        return True
