from odoo import api, fields, models
from odoo.exceptions import UserError


class ProjectTask(models.Model):
    _inherit = "project.task"

    daadit_day_plan_owner_id = fields.Many2one(
        "res.users", string="My Day scheduling owner", copy=False, groups="base.group_user",
    )
    daadit_day_parallel = fields.Boolean(
        string="Schedule together in My Day", copy=False, groups="base.group_user",
    )


class MyDaySharedWork(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _shared_plan_owners(self, tasks):
        shared = tasks.filtered(
            lambda task: len(task.user_ids) > 1 and not task.daadit_day_parallel,
        )
        owners = {}
        undecided = tasks.browse()
        for task in shared:
            owner = task.daadit_day_plan_owner_id
            if owner.active and owner in task.user_ids:
                owners[task.id] = owner.id
            else:
                undecided |= task
        if undecided:
            slots = self.env["planning.slot"].sudo().search([
                ("daadit_task_id", "in", undecided.ids), ("daadit_day_state", "=", "planned"),
                "|", ("end_datetime", ">", self._now()), ("daadit_timer_started_at", "!=", False),
            ])
            slots = slots.sorted(lambda slot: (
                not bool(slot.daadit_timer_started_at), slot.start_datetime, slot.id,
            ))
            for slot in slots:
                user = slot.resource_id.user_id
                if user in slot.daadit_task_id.user_ids and user.active:
                    owners.setdefault(slot.daadit_task_id.id, user.id)
            for task in undecided:
                active = task.user_ids.filtered(lambda user: user.active and not user.share)
                if active:
                    owners.setdefault(task.id, min(active.ids))
        return owners

    def _can_plan_shared(self, task):
        return self._shared_plan_owners(task).get(task.id, self.env.uid) == self.env.uid

    def _shared_elsewhere(self):
        assigned = self.env["project.task"].search([
            ("user_ids", "in", [self.env.uid]),
            ("state", "not in", ("1_done", "1_canceled")),
            ("project_id", "!=", False),
            ("daadit_day_block_kind", "=", False),
        ], limit=500)
        owners = self._shared_plan_owners(assigned)
        users = self.env["res.users"].browse(list(set(owners.values())))
        names = {user.id: user.display_name for user in users}
        return [{
            "kind": "task", "res_model": task._name, "res_id": task.id,
            "key": "task-%d" % task.id, "title": task.display_name, "shared_task": True,
            "shared_elsewhere": True, "subtitle": names.get(owners[task.id], ""),
        } for task in assigned if owners.get(task.id, self.env.uid) != self.env.uid]

    @api.model
    def shared_work_details(self, task_id, block_id=False):
        task = self._editable_work_record("task", task_id)
        employee = self._employee()
        tz = self._tz(employee)
        assignees = task.user_ids.filtered(lambda u: u.active and not u.share)
        if len(assignees) < 2:
            raise UserError(self.env._("This task does not have a shared schedule."))
        own_slot = False
        if block_id:
            own_slot, _employee = self._own_slot(block_id)
            if own_slot.daadit_task_id.id != task.id:
                raise UserError(self.env._("This block does not belong to this task."))
        # The task is assigned to the caller. Read only this task's planning
        # and show only the assignments, not colleagues' other work.
        slots = self.env["planning.slot"].sudo().search([
            ("daadit_task_id", "=", task.id), ("daadit_day_state", "=", "planned"),
            ("resource_id.user_id", "in", assignees.ids),
            "|", ("end_datetime", ">", self._now()), ("daadit_timer_started_at", "!=", False),
        ], order="start_datetime, id")
        return {
            "users": [{"id": user.id, "name": user.display_name} for user in assignees],
            "owner": self._shared_plan_owners(task).get(task.id) or task.daadit_day_plan_owner_id.id
            or min(assignees.ids),
            "parallel": task.daadit_day_parallel,
            "part": own_slot.daadit_day_work_part or "" if own_slot else "",
            "slots": [{
                "id": slot.id, "user": slot.resource_id.user_id.display_name,
                "start": self._local(slot.start_datetime, tz).strftime("%d-%m %H:%M"),
                "end": self._local(slot.end_datetime, tz).strftime("%d-%m %H:%M"),
                "part": slot.daadit_day_work_part or "",
            } for slot in slots],
        }

    @api.model
    def set_shared_work(self, task_id, owner_id, parallel, block_id=False, part=None, day=None):
        task = self._editable_work_record("task", task_id)
        assignees = task.user_ids.filtered(lambda u: u.active and not u.share)
        if self.env.user not in assignees or len(assignees) < 2 or owner_id not in assignees.ids:
            raise UserError(self.env._("Choose an active employee assigned to this task."))
        task.write({"daadit_day_plan_owner_id": owner_id, "daadit_day_parallel": parallel is True})
        if block_id:
            slot, _employee = self._own_slot(block_id)
            if slot.daadit_task_id.id != task.id:
                raise UserError(self.env._("This block does not belong to this task."))
            slot.daadit_day_work_part = (part or "").strip()[:200]
        if parallel is not True:
            slots = self.env["planning.slot"].sudo().search([
                ("daadit_task_id", "=", task.id), ("daadit_day_state", "=", "planned"),
                ("resource_id.user_id", "!=", owner_id),
            ], order="resource_id, id")
            for slot in slots:
                self._remove_planned_slot(slot, self.env._("Time worked before task handover"))
        employee = self._employee()
        tz = self._tz(employee)
        return self._payload(employee, self._parse_day(day, tz), tz,
                             message=self.env._("Shared scheduling saved."))
