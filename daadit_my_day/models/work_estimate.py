from collections import defaultdict
from datetime import timedelta
from statistics import median

from odoo import api, models
from odoo.exceptions import UserError

from .my_day import MAX_BLOCK, MIN_BLOCK, _minutes


class MyDayEstimates(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _tag_estimate(self, record, item):
        item["estimate"] = record.daadit_day_estimate_minutes
        item["estimate_anchor"] = record.daadit_day_estimate_anchor
        item["estimate_max"] = record.daadit_day_estimate_block_max or MAX_BLOCK
        item["minutes"] = min(MAX_BLOCK, max(MIN_BLOCK, item["minutes"]))
        return item

    def _worked_seconds(self, kind, record_ids):
        if not record_ids:
            return {}
        field = "daadit_ticket_id" if kind == "ticket" else "daadit_task_id"
        rows = self.env["planning.slot"].sudo()._read_group(
            [(field, "in", list(record_ids))], [field], ["daadit_timer_seconds:sum"],
        )
        return {record.id: seconds or 0 for record, seconds in rows}

    def _remaining_estimate(self, estimate, anchor, worked):
        return max(0, estimate - max(0, worked - anchor) // 60)

    def _apply_estimates(self, items, planned):
        planned_minutes = defaultdict(int)
        for slot in planned:
            record = slot.daadit_ticket_id or slot.daadit_task_id
            if record:
                duration = _minutes(slot.start_datetime, slot.end_datetime)
                key = (slot.daadit_day_kind, record.id)
                planned_minutes[key] += max(0, duration - slot.daadit_timer_seconds // 60)
        worked = {
            kind: self._worked_seconds(kind, [
                i["res_id"] for i in items if i["kind"] == kind and i["estimate"]
            ]) for kind in ("ticket", "task")
        }
        result = []
        for item in items:
            if item["estimate"]:
                kind, res_id = item["kind"], item["res_id"]
                remaining = self._remaining_estimate(
                    item["estimate"], item["estimate_anchor"], worked[kind].get(res_id, 0),
                )
                reserved = planned_minutes[kind, res_id]
                if reserved and remaining <= reserved:
                    continue
                item["estimate_spent"] = not remaining
                item["minutes"] = max(MIN_BLOCK, min(item["estimate_max"], remaining - reserved))
                item["reason"] = (
                    self.env._("%s min remaining outside scheduled blocks") % (remaining - reserved)
                    if remaining else self.env._("Estimate used up · review it")
                )
            result.append(item)
        return result

    def _estimate_suggestion(self, record, kind, today, worked):
        Line = self.env["account.analytic.line"]
        field = "helpdesk_ticket_id" if kind == "ticket" else "task_id"
        if not record.project_id or field not in Line._fields or "validated" not in Line._fields:
            return False
        if not Line.has_access("read"):
            return False
        done = ("helpdesk_ticket_id.stage_id.fold", "=", True) if kind == "ticket" else (
            "task_id.state", "=", "1_done",
        )
        samples = Line._read_group([
            ("project_id", "=", record.project_id.id), ("validated", "=", True),
            ("date", ">=", today - timedelta(days=90)), (field, "!=", record.id), done,
        ], [field], ["unit_amount:sum"])
        return self._suggest_from_samples(samples, worked)

    def _suggest_from_samples(self, samples, worked):
        actual = []
        estimated = []
        for source, hours in samples:
            if not source or not hours or hours <= 0 or not source.has_access("read"):
                continue
            actual.append(hours * 60)
            if source.daadit_day_estimate_minutes:
                estimated.append(source.daadit_day_estimate_minutes
                                 + source.daadit_day_estimate_anchor / 60)
        if len(actual) < 3:
            return False
        typical = round(median(actual))
        return {
            "count": len(actual), "actual": typical,
            "estimated": round(median(estimated)) if estimated else False,
            "remaining": min(4800, max(MIN_BLOCK, typical - worked // 60)),
        }

    @api.model
    def estimate_details(self, kind, record_id):
        record = self._editable_work_record(kind, record_id)
        worked = self._worked_seconds(kind, [record.id]).get(record.id, 0)
        today = self._today(self._tz(self._employee()))
        return {
            "known": bool(record.daadit_day_estimate_minutes),
            "minutes": self._remaining_estimate(
                record.daadit_day_estimate_minutes, record.daadit_day_estimate_anchor, worked,
            ),
            "max_block": record.daadit_day_estimate_block_max or MAX_BLOCK,
            "suggestion": self._estimate_suggestion(record, kind, today, worked),
        }

    @api.model
    def set_work_estimate(self, kind, record_id, remaining, max_block, day=None):
        record = self._editable_work_record(kind, record_id)
        if (type(remaining) is not int or not 0 <= remaining <= 4800
                or type(max_block) is not int or not MIN_BLOCK <= max_block <= MAX_BLOCK):
            raise UserError(self.env._(
                "Choose 0 to 4800 minutes and a block duration of 5 to 180 minutes."
            ))
        worked = self._worked_seconds(kind, [record.id]).get(record.id, 0)
        record.write({
            "daadit_day_estimate_minutes": remaining,
            "daadit_day_estimate_anchor": worked if remaining else 0,
            "daadit_day_estimate_block_max": max_block if remaining else 0,
        })
        employee = self._employee()
        tz = self._tz(employee)
        return self._payload(employee, self._parse_day(day, tz), tz,
                             message=self.env._("Time estimate saved."))
