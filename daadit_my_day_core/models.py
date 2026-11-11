from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    daadit_my_day_core_order = fields.Json(copy=False, groups="base.group_system")


class MyDaySources(models.AbstractModel):
    _name = "daadit.my.day.sources"
    _description = "My Day work sources"

    def _sources(self):
        """Extend this mapping in an add-on to connect another source."""
        return {
            "task": {
                "model": "project.task",
                "owner": "user_ids",
                "date": "date_deadline",
                "domain": [("state", "not in", ("1_done", "1_canceled"))],
            },
            "ticket": {
                "model": "helpdesk.ticket",
                "owner": "user_id",
                "date": None,
                "domain": [("stage_id.fold", "=", False)],
            },
            "activity": {
                "model": "mail.activity",
                "owner": "user_id",
                "date": "date_deadline",
                "domain": [],
            },
        }

    def _available_sources(self):
        return {
            kind: spec for kind, spec in self._sources().items()
            if spec["model"] in self.env and self.env[spec["model"]].has_access("read")
        }

    def _users(self):
        return self.env["res.users"].search([
            ("share", "=", False), ("company_ids", "in", self.env.companies.ids),
        ], order="name, id")

    def _users_payload(self, users):
        return [{"id": user.id, "name": user.name} for user in users]

    def _check_internal(self):
        if not self.env.user.has_group("base.group_user"):
            raise AccessError(self.env._("My Day is available to internal users."))

    def _is_company_visible(self, record):
        return ("company_id" not in record._fields or not record.company_id
                or record.company_id in self.env.companies)

    def _open_record(self, record):
        if record._name != "mail.activity":
            return record
        if record.res_model not in self.env:
            return False
        source = self.env[record.res_model].browse(record.res_id).exists()
        return source if source and source.has_access("read") else False

    def _todo_view(self):
        return self.env.ref("project_todo.project_task_view_todo_form", raise_if_not_found=False)

    def _work_items(self, scope):
        items = []
        todo_view = self._todo_view()
        for kind, spec in self._available_sources().items():
            model = self.env[spec["model"]]
            domain = list(spec["domain"])
            if scope != "all":
                domain.append((spec["owner"], "in", [scope]))
            if "company_id" in model._fields:
                domain.append(("company_id", "in", [False, *self.env.companies.ids]))
            if kind == "task":
                if not todo_view:
                    domain.append(("project_id", "!=", False))
                if "is_template" in model._fields:
                    domain.append(("is_template", "=", False))
            for record in model.search(domain):
                source = self._open_record(record)
                if not source or not self._is_company_visible(source):
                    continue
                is_todo = kind == "task" and not record.project_id
                due = record[spec["date"]] if spec["date"] else False
                items.append({
                    "key": f"{kind}-{record.id}",
                    "kind": "todo" if is_todo else kind,
                    "source_kind": kind,
                    "id": record.id,
                    "title": record.display_name,
                    "due": fields.Date.to_string(fields.Date.to_date(due)) if due else "",
                    "urgent": kind == "ticket" and record.priority == "3",
                    "assignees": self._users_payload(record[spec["owner"]]),
                    "can_assign": record.has_access("write"),
                    "open_model": source._name,
                    "open_id": source.id,
                    "open_view": todo_view.id if is_todo else False,
                })
        return items

    def _appointments(self):
        if "calendar.event" not in self.env or not self.env["calendar.event"].has_access("read"):
            return []
        events = self.env["calendar.event"].search([
            ("partner_ids", "in", self.env.user.partner_id.ids),
            ("stop", ">=", fields.Datetime.now()),
        ], order="start, id", limit=25)
        return [{
            "id": event.id, "title": event.display_name,
            "start": fields.Datetime.to_string(event.start),
        } for event in events]

    @api.model
    def get_workspace(self, scope=None):
        self._check_internal()
        users = self._users()
        scope = self.env.uid if scope is None else scope
        if scope != "all" and (type(scope) is not int or scope not in users.ids):
            raise UserError(self.env._("Choose an active employee."))
        order = {
            key: rank for rank, key in enumerate(
                self.env.user.sudo().daadit_my_day_core_order or []
            )
        } if scope == self.env.uid else {}
        items = sorted(self._work_items(scope), key=lambda item: (
            not item["urgent"], order.get(item["key"], len(order)),
            item["due"] or "9999-12-31", item["key"],
        ))
        return {
            "items": items,
            "employees": self._users_payload(users),
            "user_id": self.env.uid,
            "appointments": self._appointments() if scope == self.env.uid else [],
        }

    @api.model
    def save_order(self, keys):
        self._check_internal()
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise UserError(self.env._("Choose work assigned to you."))
        allowed = {item["key"] for item in self._work_items(self.env.uid) if not item["urgent"]}
        if not set(keys) <= allowed or len(set(keys)) != len(keys):
            raise UserError(self.env._("Choose work assigned to you."))
        self.env.user.sudo().daadit_my_day_core_order = keys

    @api.model
    def assign(self, kind, record_id, user_id, scope=None):
        self._check_internal()
        if (not isinstance(kind, str) or type(record_id) is not int
                or type(user_id) is not int or user_id not in self._users().ids):
            raise UserError(self.env._("Choose an active employee."))
        spec = self._available_sources().get(kind)
        record = self.env[spec["model"]].browse(record_id).exists() if spec else False
        if not record or not record.has_access("read"):
            raise UserError(self.env._("This work is unavailable."))
        source = self._open_record(record)
        if not source or not self._is_company_visible(source):
            raise UserError(self.env._("This work is unavailable."))
        record.check_access("write")
        value = user_id
        if record._fields[spec["owner"]].type == "many2many":
            value = [fields.Command.link(user_id)]
            previous = self.env.uid if scope is None else scope
            if (type(previous) is int and previous != user_id
                    and previous in record[spec["owner"]].ids):
                value.append(fields.Command.unlink(previous))
        record.write({spec["owner"]: value})
