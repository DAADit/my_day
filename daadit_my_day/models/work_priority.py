"""Eigen volgorde: de medewerker sleept zijn werk in de volgorde die hij wil.

De opgeslagen volgorde gaat voor de standaardregels. Werk dat nog niet in de
lijst stond, schuift in op de plek waar het volgens de standaardregels hoort.
Urgente tickets gaan altijd voor.
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .my_day import HORIZON_DAYS, OPEN_TASK_STATES, RANK_URGENT

PRIORITY_MODELS = {"helpdesk.ticket": "ticket", "project.task": "task", "mail.activity": "activity"}


class PriorityRollback(Exception):
    pass


class MyDayPriority(models.Model):
    _name = "daadit.my.day.priority"
    _description = "My Day: custom work order"
    _order = "sequence, id"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.user,
    )
    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    sequence = fields.Integer(default=10)

    _user_record_unique = models.Constraint(
        "UNIQUE(user_id, res_model, res_id)", "Each work item can appear only once in the order.",
    )


class MyDayPriorities(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _item_ref(self, item):
        if item["kind"] == "activity":
            return ("mail.activity", item["activity_id"])
        return (item["res_model"], item["res_id"])

    def _priority_map(self):
        rows = self.env["daadit.my.day.priority"].search([("user_id", "=", self.env.uid)])
        return {(row.res_model, row.res_id): row.sequence for row in rows}

    def _apply_priorities(self, items, order):
        urgent = [item for item in items if item["rank"] == RANK_URGENT]
        rest = [item for item in items if item["rank"] != RANK_URGENT]
        ordered = sorted((item for item in rest if self._item_ref(item) in order),
                         key=lambda item: order[self._item_ref(item)])
        loose = [item for item in rest if self._item_ref(item) not in order]
        result = []
        for item in ordered:
            # New work slides in where the default rules would put it.
            while loose and loose[0]["rank"] < item["rank"]:
                result.append(loose.pop(0))
            item["manual"] = True
            result.append(item)
        return urgent + result + loose

    def _queue(self, employee, day, tz):
        items = super()._queue(employee, day, tz)
        order = self._priority_map()
        return self._apply_priorities(items, order) if order else items

    def _priority_payload(self, item, fixed=False):
        model, res_id = self._item_ref(item)
        return {
            "key": "%s-%d" % (model, res_id),
            "res_model": model,
            "res_id": res_id,
            "open_model": item["res_model"],
            "open_id": item["res_id"],
            "kind": item["kind"],
            "title": item["title"],
            "subtitle": item.get("subtitle", ""),
            "reason": item.get("reason", ""),
            "minutes": item.get("minutes", 0),
            "urgent": item["rank"] == RANK_URGENT,
            "manual": bool(item.get("manual")),
            "fixed": fixed,
        }

    def _priority_employees(self):
        employees = self.env["hr.employee.public"].search([
            ("company_id", "in", self.env.companies.ids), ("user_id", "!=", False),
        ])
        users = employees.user_id.filtered_domain([("active", "=", True), ("share", "=", False)])
        return users | self.env.user

    def _priority_assignees(self, record):
        return record.user_ids if record._name == "project.task" else record.user_id

    def _priority_can_manage(self, model):
        group = {
            "project.task": "project.group_project_manager",
            "helpdesk.ticket": "helpdesk.group_helpdesk_manager",
            "mail.activity": "hr.group_hr_manager",
        }[model]
        return self.env.user.has_group(group) or self.env.user.has_group("base.group_system")

    def _priority_source(self, record):
        if record._name == "mail.activity":
            return self._readable_activity_source(record.res_model, record.res_id)
        return record

    def _priority_details(self, item, record):
        item["assignees"] = [
            {"id": user.id, "name": user.name} for user in self._priority_assignees(record)
        ]
        item["can_assign"] = record.has_access("write")
        if record._name == "project.task" and not record.project_id:
            view = self.env.ref(
                "project_todo.project_task_view_todo_form", raise_if_not_found=False,
            )
            if view:
                item["open_view_id"] = view.id
                if not record.parent_id:
                    item["kind"] = "todo"
                    if not item["fixed"]:
                        item["subtitle"] = ""
        return item

    def _priority_team_items(self, scope):
        today = fields.Date.context_today(self)
        tz = self._tz(self._employee())
        week_end = today + timedelta(days=6 - today.weekday())
        horizon = today + timedelta(days=HORIZON_DAYS)
        companies = self.env.companies
        items = []
        for model, domain in (
            ("helpdesk.ticket", [("stage_id.fold", "=", False)]),
            ("project.task", [
                ("state", "in", OPEN_TASK_STATES + ("04_waiting_normal",)),
            ]),
            ("mail.activity", []),
        ):
            Model = self.env[model]
            if not Model.has_access("read"):
                continue
            if model != "mail.activity":
                domain.append(("company_id", "in", [False, *companies.ids]))
            if model == "project.task" and "is_template" in Model._fields:
                domain.append(("is_template", "=", False))
            if model == "project.task" and not self.env.ref(
                "project_todo.project_task_view_todo_form", raise_if_not_found=False,
            ):
                domain.append(("project_id", "!=", False))
            if scope != "all":
                domain.append(("user_ids" if model == "project.task" else "user_id", "=", scope))
            for record in Model.search(domain):
                source = self._priority_source(record)
                if not source or (
                    "company_id" in source._fields and source.company_id
                    and source.company_id not in companies
                ):
                    continue
                if model == "project.task":
                    item = self._task_item(record, today, week_end, horizon, tz)
                elif model == "helpdesk.ticket":
                    item = self._ticket_item(record, None)
                    if not item:
                        item = {
                            "kind": "ticket", "res_model": model, "res_id": record.id,
                            "title": record.name,
                            "rank": RANK_URGENT if record.priority == "3" else 99,
                            "sort": (0, record.create_date),
                        }
                else:
                    item = self._activity_item(record, today)
                if item:
                    items.append((item, record))
        return sorted(items, key=lambda pair: (pair[0]["rank"], pair[0]["sort"]))

    def _priority_validate_scope(self, scope):
        if type(scope) is not int or scope not in self._priority_employees().ids:
            raise UserError(self.env._("Choose an active employee."))

    def _team_priorities(self, scope):
        if scope != "all":
            self._priority_validate_scope(scope)
        pairs = self._priority_team_items(scope)
        records = {self._item_ref(item): record for item, record in pairs}
        items = [item for item, _record in pairs]
        if scope != "all":
            order = self.env["daadit.my.day.priority"].sudo().search([("user_id", "=", scope)])
            items = self._apply_priorities(
                items, {(row.res_model, row.res_id): row.sequence for row in order},
            )
        result = []
        for item in items:
            record = records[self._item_ref(item)]
            payload = self._priority_details(self._priority_payload(item), record)
            payload["can_reorder"] = (
                scope != "all" and not payload["urgent"]
                and self._priority_can_manage(record._name)
                and payload["can_assign"]
            )
            result.append(payload)
        return {"items": result, "fixed": [], "has_custom": False}

    @api.model
    def get_priorities(self, scope=None):
        """Al het open werk in de volgorde waarin Mijn Dag het zou inplannen."""
        users = self._priority_employees().sorted("name")
        has_todo = bool(self.env.ref(
            "project_todo.project_task_view_todo_form", raise_if_not_found=False,
        ))
        if scope not in (None, self.env.uid):
            result = self._team_priorities(scope)
            result["employees"] = [{"id": user.id, "name": user.name} for user in users]
            result["has_todo"] = has_todo
            return result
        employee = self._employee()
        tz = self._tz(employee)
        today = self._today(tz)
        days = sorted({today, *self._horizon_days(employee, today, tz)})
        fixed = self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_kind", "in", ("ticket", "task", "activity")),
            ("daadit_day_state", "=", "planned"),
        ]).filtered(lambda slot: not self._is_auto(slot))
        try:
            with self.env.cr.savepoint():
                for day in days:
                    self._clear_auto(employee, day, tz)
                items = self._queue(employee, today, tz)
                raise PriorityRollback
        except PriorityRollback:
            pass
        fixed_items = []
        for slot in fixed.sorted("start_datetime"):
            record = slot.daadit_ticket_id or slot.daadit_task_id
            model, res_id = ((record._name, record.id) if record
                             else ("mail.activity", slot.daadit_activity_id.id))
            source_record = self.env[model].browse(res_id).exists()
            if not source_record or not source_record.has_access("read"):
                continue
            source = self._priority_source(source_record)
            if not source:
                continue
            fixed_items.append({
                "key": "%s-%d" % (model, res_id), "res_model": model, "res_id": res_id,
                "open_model": source._name, "open_id": source.id,
                "kind": slot.daadit_day_kind, "title": (slot.name or "").split("\n", 1)[0],
                "subtitle": self.env._(
                    "Fixed at %s", self._local(slot.start_datetime, tz).strftime("%d-%m %H:%M"),
                ),
                "reason": slot.daadit_day_reason or "", "fixed": True, "urgent": False,
                "manual": False,
                "minutes": int((slot.end_datetime - slot.start_datetime).total_seconds() // 60),
            })
            self._priority_details(fixed_items[-1], source_record)
        payloads = []
        for item in items:
            model, res_id = self._item_ref(item)
            record = self.env[model].browse(res_id)
            payload = self._priority_details(self._priority_payload(item), record)
            payload["can_reorder"] = not payload["urgent"]
            payloads.append(payload)
        return {
            "items": payloads,
            "has_todo": has_todo,
            "fixed": fixed_items,
            "has_custom": bool(self._priority_map()),
            "employees": [{"id": user.id, "name": user.name} for user in users],
        }

    @api.model
    def assign_priority(self, model, res_id, user_id, scope=None):
        self._priority_validate_scope(user_id)
        if not isinstance(model, str) or model not in PRIORITY_MODELS or type(res_id) is not int:
            raise UserError(self.env._("The order contains an unknown work item."))
        record = self.env[model].browse(res_id).exists()
        if not record or not record.has_access("read"):
            raise UserError(self.env._("This work no longer exists."))
        source = self._priority_source(record)
        if not source:
            raise UserError(self.env._("This work no longer exists."))
        record.check_access("write")
        if ("company_id" in source._fields and source.company_id
                and source.company_id not in self.env.companies):
            raise UserError(self.env._("You do not have access to all of this work."))
        if model == "project.task":
            previous = self.env.uid if scope is None else scope
            commands = [fields.Command.link(user_id)]
            if type(previous) is int and previous != user_id and previous in record.user_ids.ids:
                commands.append(fields.Command.unlink(previous))
            record.write({"user_ids": commands})
        else:
            record.write({"user_id": user_id})
        return True

    @api.model
    def set_employee_priorities(self, scope, refs):
        self._priority_validate_scope(scope)
        if scope == self.env.uid:
            return self.set_priorities(refs)
        if not isinstance(refs, list) or len(refs) > 1000:
            raise UserError(self.env._("Provide the order as a list."))
        allowed = {
            (row["res_model"], row["res_id"]) for row in self._team_priorities(scope)["items"]
            if row["can_reorder"]
        }
        requested = []
        for ref in refs:
            if (not isinstance(ref, (list, tuple)) or len(ref) != 2
                    or not isinstance(ref[0], str) or type(ref[1]) is not int
                    or tuple(ref) not in allowed or tuple(ref) in requested):
                raise UserError(self.env._("You do not have access to all of this work."))
            requested.append(tuple(ref))
        if not requested:
            return True
        Priority = self.env["daadit.my.day.priority"].sudo()
        self._lock_priority_order(scope)
        rows = Priority.search([("user_id", "=", scope)])
        remaining = [(row.res_model, row.res_id) for row in rows
                     if (row.res_model, row.res_id) not in requested]
        rows.unlink()
        Priority.create([
            {"user_id": scope, "res_model": model, "res_id": res_id, "sequence": sequence * 10}
            for sequence, (model, res_id) in enumerate(requested + remaining, 1)
        ])
        return True

    def _lock_priority_order(self, user_id):
        self.env.cr.execute("SELECT id FROM res_users WHERE id = %s FOR UPDATE", (user_id,))

    @api.model
    def set_priorities(self, refs):
        """Bewaar de gesleepte volgorde: een lijst van [model, id]."""
        if not isinstance(refs, list) or len(refs) > 1000:
            raise UserError(self.env._("Provide the order as a list."))
        values = []
        seen = set()
        for ref in refs:
            if not isinstance(ref, (list, tuple)) or len(ref) != 2:
                raise UserError(self.env._("The order contains an unknown work item."))
            model, res_id = ref[0], ref[1]
            if model not in PRIORITY_MODELS or type(res_id) is not int or (model, res_id) in seen:
                raise UserError(self.env._("The order contains an unknown work item."))
            record = self.env[model].browse(res_id).exists()
            if not record or not record.has_access("read"):
                raise UserError(self.env._("You do not have access to all of this work."))
            seen.add((model, res_id))
            values.append({
                "user_id": self.env.uid, "res_model": model, "res_id": res_id,
                "sequence": (len(values) + 1) * 10,
            })
        self._lock_priority_order(self.env.uid)
        Priority = self.env["daadit.my.day.priority"]
        Priority.search([("user_id", "=", self.env.uid)]).unlink()
        Priority.create(values)
        return True

    @api.model
    def reset_priorities(self):
        self._lock_priority_order(self.env.uid)
        self.env["daadit.my.day.priority"].search([("user_id", "=", self.env.uid)]).unlink()
        return True
