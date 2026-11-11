"""Het brein van Mijn Dag: wat doet deze medewerker vandaag, en in welke volgorde.

Alles werkt op de ingelogde gebruiker. De blokken zijn planning.slot-records;
die mag een gewone medewerker niet schrijven, dus daar gaat het via sudo, maar
altijd beperkt tot de eigen resource. Tickets en taken worden met de rechten
van de gebruiker gelezen.
"""
import logging
import math
from datetime import date as date_cls, datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date

from .timer_utils import timer_elapsed, timer_expired

_logger = logging.getLogger(__name__)

OPEN_TASK_STATES = ("01_in_progress", "02_changes_requested", "03_approved")
DONE_QUIET_DAYS = 3
HORIZON_DAYS = 14
MIN_BLOCK = 5
MAX_BLOCK = 180
QUEUE_LIMIT = 40

RANK_URGENT = 0
RANK_CONCEPT = 10
RANK_OVERDUE = 20
RANK_THIS_WEEK = 30
RANK_REVIEW = 40
RANK_HIGH = 41
RANK_CUSTOMER_WAITS = 42
RANK_SOON = 50
RANK_PLANNED = 55
RANK_OPEN_TICKET = 60
RANK_BACKLOG = 70

HORIZON_WORK_DAYS = 5
BEAT_LIMIT = timedelta(minutes=10)
MIN_COUNTED_SECONDS = 60


def _minutes(start, end):
    return int(round((end - start).total_seconds() / 60))


def _fmt_day(env, day):
    return format_date(env, day, date_format="EEE d MMM")


def _subtract(gaps, start, end):
    """Haal [start, end] uit de lijst met vrije stukken."""
    result = []
    for gap_start, gap_end in gaps:
        if end <= gap_start or start >= gap_end:
            result.append([gap_start, gap_end])
            continue
        if gap_start < start:
            result.append([gap_start, start])
        if end < gap_end:
            result.append([end, gap_end])
    return result


class DaaditMyDay(models.AbstractModel):
    _name = "daadit.my.day"
    _description = "My Day"

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------
    def _employee(self):
        employee = self.env.user.sudo().employee_id
        if not employee:
            raise UserError(
                self.env._("Link an employee to your user account to schedule your day.")
            )
        return employee

    def _tz(self, employee):
        return pytz.timezone(employee.tz or self.env.user.tz or "Europe/Amsterdam")

    def _now(self):
        return fields.Datetime.now().replace(microsecond=0)

    def _today(self, tz):
        return pytz.utc.localize(self._now()).astimezone(tz).date()

    def _parse_day(self, day, tz):
        if not day:
            return self._today(tz)
        return day if isinstance(day, date_cls) else fields.Date.from_string(day)

    def _bounds(self, day, tz):
        start = tz.localize(datetime.combine(day, time.min))
        end = tz.localize(datetime.combine(day + timedelta(days=1), time.min))
        return (start.astimezone(pytz.utc).replace(tzinfo=None),
                end.astimezone(pytz.utc).replace(tzinfo=None))

    def _local(self, value, tz):
        return pytz.utc.localize(value).astimezone(tz)

    def _local_day(self, value, tz):
        return self._local(value, tz).date()

    def _param_int(self, key, default):
        value = self.env["ir.config_parameter"].sudo().get_param("daadit_my_day.%s" % key)
        try:
            return int(value) if value else default
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Beschikbare tijd
    # ------------------------------------------------------------------
    def _work_intervals(self, employee, day, tz):
        calendar = employee.resource_calendar_id or employee.company_id.resource_calendar_id
        if not calendar:
            return []
        start = tz.localize(datetime.combine(day, time.min))
        end = tz.localize(datetime.combine(day + timedelta(days=1), time.min))
        resource = employee.resource_id
        intervals = calendar.sudo()._work_intervals_batch(start, end, resources=resource, tz=tz)
        return [
            [begin.astimezone(pytz.utc).replace(tzinfo=None),
             stop.astimezone(pytz.utc).replace(tzinfo=None)]
            for begin, stop, _records in intervals[resource.id]
        ]

    def _events(self, employee, day, tz):
        """Afspraken van vandaag, zonder afgewezen uitnodigingen."""
        start, end = self._bounds(day, tz)
        partner = self.env.user.partner_id
        events = self.env["calendar.event"].search(
            [("partner_ids", "in", partner.ids), ("start", "<", end), ("stop", ">", start)],
            order="start",
        )
        result = self.env["calendar.event"]
        for event in events:
            attendee = event.attendee_ids.filtered(lambda a: a.partner_id == partner)[:1]
            if attendee and attendee.state == "declined":
                continue
            result |= event
        return result

    def _blocking_events(self, events):
        return events.filtered(lambda e: not e.allday and e.show_as == "busy")

    def _slots(self, employee, day, tz, states=None):
        start, end = self._bounds(day, tz)
        domain = [
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_kind", "!=", False),
            ("start_datetime", "<", end),
            ("end_datetime", ">", start),
        ]
        if states:
            domain.append(("daadit_day_state", "in", states))
        return self.env["planning.slot"].sudo().search(
            domain, order="daadit_day_sequence, start_datetime, id",
        )

    def _closed_day(self, employee, day, tz):
        """Diensten die de dag vrijhouden van helpdesk."""
        start, end = self._bounds(day, tz)
        return self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("role_id.daadit_no_helpdesk", "=", True),
            ("start_datetime", "<", end),
            ("end_datetime", ">", start),
        ])

    def _free_gaps(self, employee, day, tz):
        """Vrije stukken van de dag: rooster min afspraken, min wat al klaar is."""
        gaps = self._capacity_gaps(
            self._work_intervals(employee, day, tz), self._events(employee, day, tz),
        )
        now = self._now()
        for slot in self._slots(employee, day, tz, states=["done"]):
            if slot.end_datetime > now:
                gaps = _subtract(gaps, slot.start_datetime, slot.end_datetime)
        if day == self._today(tz):
            # Rond af op vijf minuten, zodat de tijden leesbaar blijven.
            minute = now.replace(second=0, microsecond=0)
            if minute < now:
                minute += timedelta(minutes=1)
            cursor = minute + timedelta(minutes=(-minute.minute) % 5)
            gaps = _subtract(gaps, datetime.min, cursor)
        return [gap for gap in gaps if _minutes(gap[0], gap[1]) >= MIN_BLOCK]

    def _capacity_gaps(self, work, events):
        gaps = [list(gap) for gap in work]
        for event in self._blocking_events(events):
            gaps = _subtract(gaps, event.start, event.stop)
        return gaps

    def _capacity_stats(self, gaps, slots, now):
        remaining = _subtract(gaps, datetime.min, now)
        for slot in slots:
            remaining = _subtract(remaining, slot.start_datetime, slot.end_datetime)
        return {
            "available": sum(_minutes(start, end) for start, end in gaps),
            "planned": sum(_minutes(slot.start_datetime, slot.end_datetime)
                           for slot in slots if slot.daadit_day_state == "planned"),
            "free": sum(_minutes(start, end) for start, end in remaining),
        }

    # ------------------------------------------------------------------
    # Wat ligt er te wachten
    # ------------------------------------------------------------------
    def _recently_done(self, employee, kind_field):
        since = self._now() - timedelta(days=DONE_QUIET_DAYS)
        slots = self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_state", "=", "done"),
            ("daadit_day_done_at", ">=", since),
            (kind_field, "!=", False),
        ])
        result = {}
        for slot in slots:
            result[slot[kind_field].id] = max(
                slot.daadit_day_done_at, result.get(slot[kind_field].id, slot.daadit_day_done_at),
            )
        return result

    def _ticket_minutes(self, ticket):
        """Reserveer per helpdeskteam: een supportvraag is korter dan een serviceklus."""
        team_minutes = ticket.team_id.daadit_day_ticket_minutes if ticket.team_id else 0
        return team_minutes or self._param_int("ticket_minutes", 30)

    def _ticket_item(self, ticket, done_at):
        fields_map = ticket._fields
        get = lambda name: ticket[name] if name in fields_map else False  # noqa: E731
        if get("daadit_clarify_pending") or get("daadit_research_requested"):
            return None
        if get("daadit_dispatch_task_id") and not get("daadit_dispatch_pr_url"):
            return None
        unanswered = get("oldest_unanswered_customer_message_date")
        if done_at and not (unanswered and unanswered > done_at):
            return None
        concept = (get("daadit_draft_reply_state") == "draft"
                   and get("daadit_research_state") != "dev_needed")
        next_step = get("daadit_hilda_next_step") or ""
        minutes = self._ticket_minutes(ticket)
        if ticket.priority == "3":
            rank, reason = RANK_URGENT, "Urgent ticket"
        elif concept:
            rank, reason = RANK_CONCEPT, self.env._("Draft reply is ready")
            minutes = min(minutes, self._param_int("concept_minutes", 5))
        elif next_step:
            rank, reason = RANK_REVIEW, self.env._("Waiting for your review")
        elif ticket.priority == "2":
            rank, reason = RANK_HIGH, self.env._("High priority")
        elif unanswered:
            rank, reason = RANK_CUSTOMER_WAITS, self.env._("Customer is waiting for a reply")
        else:
            rank, reason = RANK_OPEN_TICKET, "Open ticket"
        ref = (ticket.ticket_ref or str(ticket.id)).lstrip("0") or str(ticket.id)
        return {
            "kind": "ticket",
            "res_model": "helpdesk.ticket",
            "res_id": ticket.id,
            "title": ticket.name,
            "subtitle": "Ticket %s · %s" % (ref, ticket.partner_id.commercial_company_name
                                            or ticket.partner_id.name or self.env._("No customer")),
            "reason": reason,
            "next_step": next_step,
            "minutes": minutes,
            "rank": rank,
            "sort": (-int(ticket.priority or 0), ticket.create_date),
        }

    def _task_item(self, task, day, week_end, horizon, tz):
        deadline = self._local_day(task.date_deadline, tz) if task.date_deadline else False
        if deadline and deadline < day:
            rank, reason = RANK_OVERDUE, self.env._("Overdue (%s)") % _fmt_day(self.env, deadline)
        elif deadline and deadline <= week_end:
            rank, reason = RANK_THIS_WEEK, self.env._("Deadline %s", _fmt_day(self.env, deadline))
        elif deadline and deadline <= horizon:
            rank, reason = RANK_SOON, self.env._("Deadline %s", _fmt_day(self.env, deadline))
        elif "planned_date_begin" in task._fields and task.planned_date_begin and (
                self._local_day(task.planned_date_begin, tz) <= horizon):
            rank, reason = RANK_PLANNED, self.env._("Scheduled within the next two weeks")
        else:
            rank, reason = RANK_BACKLOG, self.env._("Open task")
        hours = 0
        if "remaining_hours" in task._fields and task.allocated_hours:
            hours = max(task.remaining_hours, 0)
        hours = hours or task.allocated_hours
        minutes = int(hours * 60) if hours else self._param_int("task_minutes", 60)
        return {
            "kind": "task",
            "res_model": "project.task",
            "res_id": task.id,
            "title": task.name,
            "subtitle": self.env._("Task %d · %s") % (task.id, task.project_id.display_name),
            "shared_task": len(task.user_ids) > 1,
            "reason": reason,
            "next_step": "",
            "minutes": max(MIN_BLOCK, min(minutes, MAX_BLOCK)),
            "rank": rank,
            "sort": (-int(task.priority or 0), task.date_deadline or datetime.max),
        }

    def _queue(self, employee, day, tz):
        """Alles wat ingepland kan worden, belangrijkste eerst."""
        today_start, _today_end = self._bounds(self._today(tz), tz)
        planned = self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_kind", "in", ("ticket", "task")),
            ("daadit_day_state", "=", "planned"),
            "|", "|", "|", ("end_datetime", ">", today_start),
            ("daadit_day_pinned", "=", True), ("daadit_timer_seconds", ">", 0),
            ("daadit_timer_started_at", "!=", False),
        ])
        planned_tickets = set(planned.daadit_ticket_id.ids)
        planned_tasks = set(planned.daadit_task_id.ids)
        closed = self._closed_day(employee, day, tz)
        only_projects = []
        if closed and "project_id" in closed._fields:
            only_projects = closed.project_id.ids
        items = []
        uid = self.env.uid

        if not closed:
            done = self._recently_done(employee, "daadit_ticket_id")
            tickets = self.env["helpdesk.ticket"].search([
                ("user_id", "=", uid), ("stage_id.fold", "=", False),
                ("daadit_day_block_kind", "=", False),
            ], limit=200)
            for ticket in tickets:
                estimate = ticket.daadit_day_estimate_minutes
                if ticket.id in planned_tickets and not estimate:
                    continue
                item = self._ticket_item(ticket, None if estimate else done.get(ticket.id))
                if item:
                    items.append(self._tag_estimate(ticket, item))

        week_end = day + timedelta(days=6 - day.weekday())
        horizon = day + timedelta(days=HORIZON_DAYS)
        Task = self.env["project.task"]
        domain = [
            ("user_ids", "in", uid),
            ("state", "in", OPEN_TASK_STATES),
            ("daadit_day_block_kind", "=", False),
        ]
        if not self.env.ref("project_todo.project_task_view_todo_form", raise_if_not_found=False):
            domain.append(("project_id", "!=", False))
        if "is_template" in Task._fields:
            domain.append(("is_template", "=", False))
        if only_projects:
            domain.append(("project_id", "in", only_projects))
        tasks = Task.search(domain, limit=500)
        owners = self._shared_plan_owners(tasks)
        tasks = tasks.filtered(lambda task: owners.get(task.id, uid) == uid)
        parents = set(Task.search([
            ("parent_id", "in", tasks.ids),
            ("state", "in", OPEN_TASK_STATES + ("04_waiting_normal",)),
        ]).parent_id.ids) if tasks else set()
        done = self._recently_done(employee, "daadit_task_id")
        for task in tasks:
            estimate = task.daadit_day_estimate_minutes
            already_handled = task.id in planned_tasks or task.id in done
            if task.id in parents or (not estimate and already_handled):
                continue
            item = self._task_item(task, day, week_end, horizon, tz)
            items.append(self._tag_estimate(task, item))

        items = self._apply_estimates(items, planned)
        items.sort(key=lambda item: (item["rank"], item["sort"]))
        return items

    # ------------------------------------------------------------------
    # Plaatsen
    # ------------------------------------------------------------------
    def _slot_vals(self, employee, item, start, end, sequence, pinned=False):
        return {
            "resource_id": employee.resource_id.id,
            "company_id": employee.company_id.id,
            "start_datetime": start,
            "end_datetime": end,
            "state": "draft",
            "name": "%s\n%s" % (item["title"], item["subtitle"]),
            "daadit_day_kind": item["kind"],
            "daadit_ticket_id": item["res_id"] if item["kind"] == "ticket" else False,
            "daadit_task_id": item["res_id"] if item["kind"] == "task" else False,
            "daadit_day_state": "planned",
            "daadit_day_pinned": pinned,
            "daadit_day_sequence": sequence,
            "daadit_day_reason": item["reason"],
        }

    def _take_gap(self, gaps, minutes):
        """Pak het eerste vrije stuk waar dit in past."""
        for gap in gaps:
            if gap[1] - gap[0] >= timedelta(minutes=minutes):
                start = gap[0]
                gap[0] = start + timedelta(minutes=minutes)
                return start, gap[0]
        return None

    def _reflow(self, employee, day, tz):
        """Zet de open blokken weer netjes achter elkaar in de vrije tijd."""
        slots = self._slots(employee, day, tz, states=["planned"])
        if not slots:
            return
        gaps = self._free_gaps(employee, day, tz)
        now = self._now()
        fixed = slots.filtered(lambda slot: slot.daadit_day_focus or slot.daadit_day_outside_hours
                               or slot.daadit_timer_started_at
                               or slot.start_datetime <= now < slot.end_datetime)
        for slot in fixed:
            gaps = _subtract(gaps, slot.start_datetime, slot.end_datetime)
        for slot in slots - fixed:
            minutes = max(MIN_BLOCK, _minutes(slot.start_datetime, slot.end_datetime))
            placed = None
            if not (slot.daadit_ticket_id and self._closed_day(employee, day, tz)):
                placed = self._take_gap(gaps, minutes)
            if placed:
                if (slot.start_datetime, slot.end_datetime) != placed:
                    slot.write({"start_datetime": placed[0], "end_datetime": placed[1]})
            else:
                placement = self._next_available_slot(
                    employee, day, tz, minutes, skip_closed=slot.daadit_day_kind == "ticket",
                )
                if placement:
                    self._move_slot(slot, employee, tz, placement)

    def _past_open_slots(self, employee, day, tz):
        start, _end = self._bounds(day, tz)
        return self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_day_kind", "!=", False),
            ("daadit_day_state", "=", "planned"),
            ("end_datetime", "<=", start),
        ])

    def _rollover(self, employee, day, tz):
        """Behoud handmatig of begonnen werk en zoek een nieuw passend blok."""
        old = self._past_open_slots(employee, day, tz)
        for slot in old.filtered("daadit_timer_started_at"):
            self._timer_stop(slot)
        old.filtered(self._is_auto).unlink()
        for slot in old.exists():
            placement = self._find_placement(
                employee, day, tz, max(MIN_BLOCK, _minutes(slot.start_datetime, slot.end_datetime)),
                skip_closed=slot.daadit_day_kind == "ticket",
            )
            if placement:
                self._move_slot(slot, employee, tz, placement)

    def _prune(self, employee, day, tz):
        """Haal blokken weg waarvan het werk intussen klaar of overgedragen is."""
        slots = self._slots(employee, day, tz, states=["planned"])
        owners = self._shared_plan_owners(slots.daadit_task_id)
        for slot in slots:
            if slot.daadit_day_kind == "activity":
                self._prune_activity(slot)
                continue
            ticket = slot.daadit_ticket_id
            task = slot.daadit_task_id
            gone = (ticket and (ticket.stage_id.fold or ticket.user_id != self.env.user)) or (
                task and (task.state not in OPEN_TASK_STATES + ("04_waiting_normal",)
                          or self.env.user not in task.user_ids))
            record = ticket or task
            if record and record.daadit_day_block_kind:
                self._unschedule_blocked(slot)
            elif task and owners.get(task.id, self.env.uid) != self.env.uid:
                self._remove_planned_slot(slot, self.env._("Time worked before task handover"))
            elif gone:
                self._timer_stop(slot)
                slot.unlink()

    def _is_auto(self, slot):
        return not (slot.daadit_day_pinned or slot.daadit_timer_seconds
                    or slot.daadit_timer_started_at or slot.daadit_day_focus
                    or slot.daadit_day_goal_date)

    def _clear_auto(self, employee, day, tz):
        self._slots(employee, day, tz, states=["planned"]).filtered(self._is_auto).unlink()

    def _plan(self, employee, day, tz):
        """Vul de vrije tijd van deze dag met wat het belangrijkst is."""
        if day == self._today(tz):
            self._rollover(employee, day, tz)
        self._prune(employee, day, tz)
        self._reflow(employee, day, tz)
        planned = self._slots(employee, day, tz, states=["planned"])
        gaps = self._free_gaps(employee, day, tz)
        total = sum(_minutes(gap[0], gap[1]) for gap in gaps)
        for slot in planned:
            gaps = _subtract(gaps, slot.start_datetime, slot.end_datetime)
        buffer_pct = self._param_int("buffer_percent", 10)
        used = total - sum(_minutes(start, end) for start, end in gaps)
        budget = total * (100 - buffer_pct) / 100
        sequence = (max(planned.mapped("daadit_day_sequence") or [0])) + 10
        values = []
        for item in self._queue(employee, day, tz):
            if used >= budget:
                break
            if item.get("estimate_spent"):
                continue
            placed = self._take_gap(gaps, item["minutes"])
            if not placed and item.get("estimate") and gaps:
                largest = max(int((end - start).total_seconds() // 60) for start, end in gaps)
                if largest >= MIN_BLOCK:
                    placed = self._take_gap(gaps, largest)
            if not placed:
                continue
            used += _minutes(placed[0], placed[1])
            values.append(self._slot_vals(employee, item, placed[0], placed[1], sequence))
            sequence += 10
        if values:
            self.env["planning.slot"].sudo().create(values)

    def _horizon_days(self, employee, start_day, tz):
        days = []
        for offset in range(0, 28):
            candidate = start_day + timedelta(days=offset)
            if self._work_intervals(employee, candidate, tz):
                days.append(candidate)
            if len(days) >= HORIZON_WORK_DAYS:
                break
        return days

    def _plan_horizon(self, employee, start_day, tz):
        """Deel al het open werk opnieuw in over de komende werkdagen."""
        days = sorted({start_day, *self._horizon_days(employee, start_day, tz)})
        for day in days:
            self._clear_auto(employee, day, tz)
        for day in days:
            self._plan(employee, day, tz)
        if start_day == self._today(tz):
            self.env.user.sudo().daadit_my_day_planned_on = start_day

    def _fill_new(self, employee, tz):
        """Plan nieuw werk in zonder de bestaande indeling om te gooien."""
        today = self._today(tz)
        for item in self._queue(employee, today, tz):
            if item["rank"] != RANK_URGENT:
                break
            if not item.get("estimate_spent"):
                self._insert_block(employee, today, tz, item)
        for day in sorted({today, *self._horizon_days(employee, today, tz)}):
            self._plan(employee, day, tz)

    # ------------------------------------------------------------------
    # Klok en uren
    # ------------------------------------------------------------------
    def _no_hours_partners(self):
        partners = self.env["res.company"].sudo().search([]).partner_id.commercial_partner_id
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "daadit_my_day.no_hours_partner_ids", "")
        ids = [int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()]
        return partners | self.env["res.partner"].sudo().browse(ids).exists()

    def _booking_target(self, slot):
        """Waar deze tijd heen gaat: (waarden, False) of (False, reden)."""
        if slot.daadit_no_hours:
            return False, self.env._("Timesheets are disabled for this block")
        ticket = slot.daadit_ticket_id
        task = slot.daadit_task_id
        record = ticket or task
        if not record:
            return False, self.env._("Personal work is not logged")
        project = record.project_id
        if not project:
            return False, self.env._("No project to log time against")
        partner = (record.partner_id or project.partner_id).commercial_partner_id
        sale_line = record.sale_line_id if "sale_line_id" in record._fields else False
        if not sale_line and task and "sale_line_id" in project._fields:
            sale_line = project.sale_line_id
        if not partner and not sale_line:
            return False, self.env._("Internal work: no timesheet")
        if partner in self._no_hours_partners():
            return False, self.env._("No timesheet for %s") % partner.name
        values = {"project_id": project.id}
        if sale_line:
            values["so_line"] = sale_line.id
        if ticket:
            values.update(helpdesk_ticket_id=ticket.id, name="Support: %s" % ticket.name)
        else:
            values.update(task_id=task.id, name="Consulting: %s" % task.name)
        return values, False

    def _rounded_hours(self, seconds):
        step = max(1, self._param_int("timesheet_rounding", 5))
        return math.ceil(seconds / 60 / step) * step / 60

    def _protected_timesheet(self, line):
        return bool(line) and any(field in line._fields and line[field]
                                  for field in ("validated", "timesheet_invoice_id",
                                                "readonly_timesheet"))

    def _book(self, slot, day=None):
        """Schrijf de getelde tijd weg: één urenregel per blok, die meegroeit."""
        line = slot.daadit_timesheet_id
        if self._protected_timesheet(line):
            return
        if slot.daadit_timer_seconds < MIN_COUNTED_SECONDS and not line:
            return
        values, _reason = self._booking_target(slot)
        if not values:
            return
        employee = slot.resource_id.employee_id[:1]
        hours = self._rounded_hours(slot.daadit_timer_seconds)
        if line:
            line.write({"unit_amount": hours})
            return
        tz = self._tz(employee)
        values.update(
            date=day or self._local_day(fields.Datetime.now(), tz),
            unit_amount=hours,
            employee_id=employee.id,
        )
        slot.daadit_timesheet_id = self.env["account.analytic.line"].sudo().create(values)

    def _lock_timer(self, resource):
        """Serialize timer and booking writes even when two tabs start different blocks."""
        resource.flush_recordset(["daadit_day_timer_revision"])
        self.env.cr.execute(
            """UPDATE resource_resource
                  SET daadit_day_timer_revision = COALESCE(daadit_day_timer_revision, 0) + 1
                WHERE id = %s""",
            (resource.id,),
        )
        resource.invalidate_recordset(["daadit_day_timer_revision"], flush=False)

    def _timer_stop(self, slot):
        if not slot.daadit_timer_started_at:
            return
        self._lock_timer(slot.resource_id)
        now = self._now()
        seconds = timer_elapsed(
            slot.daadit_timer_started_at, slot.daadit_timer_beat_at, now, BEAT_LIMIT,
        )
        slot.write({
            "daadit_timer_started_at": False,
            "daadit_timer_beat_at": False,
            "daadit_timer_seconds": slot.daadit_timer_seconds + seconds,
        })
        self._book(slot)

    def _running_slots(self, employee):
        return self.env["planning.slot"].sudo().search([
            ("resource_id", "=", employee.resource_id.id),
            ("daadit_timer_started_at", "!=", False),
        ])

    def _timer_info(self, slot):
        running = bool(slot.daadit_timer_started_at)
        session_seconds = timer_elapsed(
            slot.daadit_timer_started_at, slot.daadit_timer_beat_at, self._now(), BEAT_LIMIT,
        ) if running else 0
        return {
            "key": "slot-%d" % slot.id,
            "running": running,
            "seconds": slot.daadit_timer_seconds + session_seconds,
            "session_seconds": session_seconds,
        }

    @api.model
    def open_block(self, block_id):
        """Start op verzoek de klok; stop eerst de vorige actieve klok."""
        slot, employee = self._own_slot(block_id)
        self._lock_timer(employee.resource_id)
        if slot.daadit_task_id and not self._can_plan_shared(slot.daadit_task_id):
            raise UserError(
                self.env._("Another employee is responsible for scheduling this task.")
            )
        for other in self._running_slots(employee) - slot:
            self._timer_stop(other)
        # A reload may explicitly open the same block after a disconnected session.
        # Settle that session before starting a new one; never bridge the offline gap.
        if timer_expired(
            slot.daadit_timer_started_at, slot.daadit_timer_beat_at, self._now(), BEAT_LIMIT,
        ):
            self._timer_stop(slot)
        if slot.daadit_day_state == "planned" and not slot.daadit_timer_started_at:
            now = self._now()
            slot.write({"daadit_timer_started_at": now, "daadit_timer_beat_at": now})
        return self._timer_info(slot)

    @api.model
    def pause_block(self, block_id):
        slot, _employee = self._own_slot(block_id)
        self._timer_stop(slot)
        return self._timer_info(slot)

    @api.model
    def stop_timer(self):
        employee = self._employee()
        self._lock_timer(employee.resource_id)
        for slot in self._running_slots(employee):
            self._timer_stop(slot)
        return True

    @api.model
    def heartbeat(self, block_id=None):
        # Older clients send no id. New clients may only renew their own open
        # block, not a different timer started by another browser tab.
        employee = self._employee()
        self._lock_timer(employee.resource_id)
        if block_id is None:
            running = self._running_slots(employee)
        else:
            if not self.env["planning.slot"].sudo().browse(int(block_id)).exists():
                return False
            slot, _employee = self._own_slot(block_id)
            running = slot.filtered("daadit_timer_started_at")
        now = self._now()
        alive = False
        for slot in running:
            if timer_expired(
                slot.daadit_timer_started_at, slot.daadit_timer_beat_at, now, BEAT_LIMIT,
            ):
                self._timer_stop(slot)
            else:
                slot.write({"daadit_timer_beat_at": now})
                alive = True
        return alive

    @api.model
    def toggle_no_hours(self, block_id):
        slot, employee = self._own_slot(block_id)
        self._lock_timer(employee.resource_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        turn_off = not slot.daadit_no_hours
        slot.daadit_no_hours = turn_off
        if turn_off and slot.daadit_timesheet_id:
            line = slot.daadit_timesheet_id
            slot.daadit_timesheet_id = False
            line.unlink()
        elif not turn_off:
            self._book(slot)
        return self._payload(employee, day, tz, select="slot-%d" % slot.id)

    # ------------------------------------------------------------------
    # Geplande acties
    # ------------------------------------------------------------------
    def _active_users(self):
        return self.env["res.users"].sudo().search([
            ("daadit_my_day_planned_on", "!=", False), ("share", "=", False),
        ])

    def _for_each_user(self, method):
        for user in self._active_users():
            try:
                with self.env.cr.savepoint():
                    model = self.with_user(user).with_company(user.company_id)
                    employee = model._employee()
                    method(model, employee, model._tz(employee))
            except Exception:
                _logger.exception("Mijn Dag: indelen mislukt voor %s", user.login)

    @api.model
    def _cron_plan_all(self):
        def run(model, employee, tz):
            model._plan_horizon(employee, model._today(tz), tz)
        self._for_each_user(run)

    @api.model
    def _cron_fill_all(self):
        limit = self._now() - BEAT_LIMIT
        stale = self.env["planning.slot"].sudo().search([
            ("daadit_timer_started_at", "!=", False), ("daadit_timer_beat_at", "<", limit),
        ])
        for slot in stale:
            self._timer_stop(slot)

        def run(model, employee, tz):
            model._fill_new(employee, tz)
        self._for_each_user(run)

    def _own_slot(self, block_id):
        employee = self._employee()
        slot = self.env["planning.slot"].sudo().browse(int(block_id)).exists()
        if not slot or slot.resource_id != employee.resource_id or not slot.daadit_day_kind:
            raise UserError(self.env._("This block is no longer in your schedule."))
        return slot, employee

    def _unoccupied_gaps(self, employee, day, tz, gaps, exclude=None):
        for slot in self._slots(employee, day, tz):
            if slot != exclude:
                gaps = _subtract(gaps, slot.start_datetime, slot.end_datetime)
        return gaps

    def _day_placement(
        self, employee, day, tz, minutes, skip_closed=False, after=None, exclude=None,
    ):
        if skip_closed and self._closed_day(employee, day, tz):
            return None
        gaps = self._unoccupied_gaps(
            employee, day, tz, self._free_gaps(employee, day, tz), exclude,
        )
        if after:
            gaps = _subtract(gaps, datetime.min, after)
        return self._take_gap(gaps, minutes)

    def _next_available_slot(self, employee, day, tz, minutes, skip_closed=False):
        for offset in range(1, HORIZON_DAYS + 1):
            candidate = day + timedelta(days=offset)
            placed = self._day_placement(employee, candidate, tz, minutes, skip_closed)
            if placed:
                return candidate, placed
        return None

    def _find_placement(
        self, employee, day, tz, minutes, skip_closed=False, after=None, exclude=None,
    ):
        placed = self._day_placement(employee, day, tz, minutes, skip_closed, after, exclude)
        return ((day, placed) if placed else
                self._next_available_slot(employee, day, tz, minutes, skip_closed))

    def _outside_hours_gap(self, employee, day, tz, minutes, exclude=None):
        day_start, day_end = self._bounds(day, tz)
        work = self._work_intervals(employee, day, tz)
        morning = tz.localize(datetime.combine(day, time(8))).astimezone(pytz.utc)
        start = max([end for _, end in work], default=morning.replace(tzinfo=None))
        start = max(start, day_start, self._now())
        if start >= day_end:
            return None
        gaps = self._capacity_gaps([[start, day_end]], self._events(employee, day, tz))
        return self._take_gap(self._unoccupied_gaps(employee, day, tz, gaps, exclude), minutes)

    def _move_slot(self, slot, employee, tz, placement, outside_hours=False):
        day, (start, end) = placement
        slots = self._slots(employee, day, tz, states=["planned"]) - slot
        slot.write({
            "start_datetime": start,
            "end_datetime": end,
            "daadit_day_pinned": True,
            "daadit_day_sequence": max(slots.mapped("daadit_day_sequence") or [0]) + 10,
            "daadit_day_outside_hours": outside_hours,
        })

    # ------------------------------------------------------------------
    # Naar het scherm
    # ------------------------------------------------------------------
    def _slot_payload(self, slot, tz, now, capacity_gaps):
        ticket = slot.daadit_ticket_id
        task = slot.daadit_task_id
        minutes = _minutes(slot.start_datetime, slot.end_datetime)
        title = slot.name or ""
        subtitle = ""
        if "\n" in title:
            title, subtitle = title.split("\n", 1)
        timer = self._timer_info(slot)
        next_step = ""
        if ticket and "daadit_hilda_next_step" in ticket._fields:
            next_step = ticket.daadit_hilda_next_step or ""
        return {
            "key": "slot-%d" % slot.id,
            "block_id": slot.id,
            "kind": slot.daadit_day_kind,
            "res_model": ticket and "helpdesk.ticket" or task and "project.task" or False,
            "res_id": ticket.id or task.id or False,
            "estimate": (ticket or task).daadit_day_estimate_minutes if ticket or task else 0,
            "shared_task": bool(task and len(task.user_ids) > 1),
            "goal": bool(slot.daadit_day_goal_date),
            "focus": slot.daadit_day_focus,
            "focus_interruptible": slot.daadit_day_focus_interruptible,
            "title": ticket.name or task.name or title,
            "subtitle": "" if slot.daadit_day_kind == "own" else subtitle,
            "reason": slot.daadit_day_reason or "",
            "next_step": next_step,
            "state": slot.daadit_day_state,
            "start": self._local(slot.start_datetime, tz).strftime("%H:%M"),
            "end": self._local(slot.end_datetime, tz).strftime("%H:%M"),
            "minutes": minutes,
            "sort": slot.start_datetime.isoformat(),
            "running": slot.start_datetime <= now < slot.end_datetime,
            "timer_running": bool(slot.daadit_timer_started_at),
            "timer_seconds": timer["seconds"],
            "timer_session_seconds": timer["session_seconds"],
            "no_hours": slot.daadit_no_hours,
            "book_note": self._booking_target(slot)[1] or "",
            "booked_hours": slot.daadit_timesheet_id.unit_amount or 0,
            "outside_hours": slot.daadit_day_outside_hours,
            "overflow": slot.daadit_day_state == "planned" and not any(
                start <= slot.start_datetime and slot.end_datetime <= end
                for start, end in capacity_gaps
            ),
        }

    def _event_payload(self, event, tz, now):
        location = event.location or ""
        if "videocall_location" in event._fields and event.videocall_location:
            location = location or "Online"
        return {
            "key": "event-%d" % event.id,
            "kind": "event",
            "res_model": "calendar.event",
            "res_id": event.id,
            "title": event.name,
            "subtitle": location,
            "reason": "",
            "next_step": "",
            "state": "done" if event.stop <= now else "planned",
            "start": self._local(event.start, tz).strftime("%H:%M"),
            "end": self._local(event.stop, tz).strftime("%H:%M"),
            "minutes": _minutes(event.start, event.stop),
            "sort": event.start.isoformat(),
            "running": event.start <= now < event.stop,
            "overflow": False,
        }

    def _week_payload(self, employee, day, tz):
        monday = day - timedelta(days=day.weekday())
        today = self._today(tz)
        now = self._now()
        result = []
        for offset in range(7):
            current = monday + timedelta(days=offset)
            work = self._work_intervals(employee, current, tz)
            if offset > 4 and not work and current != day:
                continue
            gaps = self._capacity_gaps(work, self._events(employee, current, tz))
            capacity = self._capacity_stats(gaps, self._slots(employee, current, tz), now)
            result.append({
                "date": fields.Date.to_string(current),
                "label": format_date(self.env, current, date_format="EEE"),
                "number": current.day,
                "is_today": current == today,
                "selected": current == day,
                **capacity,
                "closed": bool(self._closed_day(employee, current, tz)),
                "off": not work,
            })
        return result

    def _work_assignees(self, record):
        return record.user_id if record._name == "helpdesk.ticket" else record.user_ids

    def _blocking_item(self, record, kind, today):
        followup = record.daadit_day_followup_date
        return {
            "key": "%s-%d" % (kind, record.id),
            "kind": kind,
            "res_model": record._name,
            "res_id": record.id,
            "title": record.display_name,
            "blocked": record.daadit_day_block_kind,
            "reason": record.daadit_day_block_reason,
            "followup_date": fields.Date.to_string(followup) if followup else False,
            "followup_user_id": record.daadit_day_followup_user_id.id,
            "followup_user": record.daadit_day_followup_user_id.display_name,
            "followup_due": bool(followup and followup <= today),
        }

    def _blocked_work(self, today):
        items = []
        for kind, model, assignee, open_filter in (
            ("ticket", "helpdesk.ticket", "user_id", ("stage_id.fold", "=", False)),
            ("task", "project.task", "user_ids", ("state", "not in", ("1_done", "1_canceled"))),
        ):
            records = self.env[model].search([
                ("daadit_day_block_kind", "!=", False), open_filter,
                "|", (assignee, "in", [self.env.uid]),
                ("daadit_day_followup_user_id", "=", self.env.uid),
            ], order="daadit_day_followup_date, id")
            items.extend(self._blocking_item(record, kind, today) for record in records)
        return sorted(items, key=lambda item: item["followup_date"] or "9999-12-31")

    def _editable_work_record(self, kind, record_id):
        model = {"ticket": "helpdesk.ticket", "task": "project.task"}.get(kind)
        if not model:
            raise UserError(self.env._("Choose a ticket or task."))
        record = self.env[model].browse(int(record_id)).exists()
        if not record:
            raise UserError(self.env._("This work no longer exists."))
        record.check_access("write")
        if self.env.user not in (self._work_assignees(record) | record.daadit_day_followup_user_id):
            raise UserError(self.env._("This work is not assigned to you."))
        if (kind == "ticket" and record.stage_id.fold) or (
            kind == "task" and record.state in ("1_done", "1_canceled")
        ):
            raise UserError(self.env._("You can only change open work in My Day."))
        return record

    @api.model
    def blocking_details(self, kind, record_id):
        record = self._editable_work_record(kind, record_id)
        today = self._today(self._tz(self._employee()))
        return {
            **self._blocking_item(record, kind, today),
            "today": fields.Date.to_string(today),
            "default_followup": fields.Date.to_string(today + timedelta(days=1)),
            "user_id": self.env.uid,
            "assignees": [
                {"id": user.id, "name": user.display_name}
                for user in self._work_assignees(record) | record.daadit_day_followup_user_id
                if user.active and not user.share
            ],
        }

    def _unschedule_blocked(self, slot):
        self._remove_planned_slot(slot, self.env._("Time worked before blocking"))

    def _remove_planned_slot(self, slot, reason):
        self._timer_stop(slot)
        if slot.daadit_timer_seconds or slot.daadit_timesheet_id:
            # Record worked time without marking the source work as completed.
            end = min(slot.end_datetime, self._now())
            seconds = slot.daadit_timer_seconds or int(slot.daadit_timesheet_id.unit_amount * 3600)
            slot.write({
                "start_datetime": end - timedelta(seconds=max(1, seconds)),
                "end_datetime": end,
                "daadit_day_state": "done",
                "daadit_day_done_at": False,
                "daadit_day_reason": reason,
            })
        else:
            slot.unlink()

    @api.model
    def set_work_blocked(self, kind, record_id, reason_kind, reason, followup, user_id, day=None):
        record = self._editable_work_record(kind, record_id)
        employee = self._employee()
        tz = self._tz(employee)
        today = self._today(tz)
        reason = (reason or "").strip()
        try:
            followup = fields.Date.to_date(followup)
        except (TypeError, ValueError):
            followup = False
        user = (self._work_assignees(record) | record.daadit_day_followup_user_id).filtered(
            lambda u: u.id == user_id and u.active and not u.share,
        )
        if reason_kind not in ("blocked", "customer") or not reason:
            raise UserError(self.env._("Choose a type and enter a reason for blocking."))
        if not followup or followup < today or not user:
            raise UserError(self.env._("Choose an owner and a follow-up date from today onward."))
        record.write({
            "daadit_day_block_kind": reason_kind,
            "daadit_day_block_reason": reason,
            "daadit_day_followup_date": followup,
            "daadit_day_followup_user_id": user.id,
        })
        source_field = "daadit_ticket_id" if kind == "ticket" else "daadit_task_id"
        # The authorized write blocks this source for all assignees. Restrict the
        # elevated Planning write to its derived open My Day blocks.
        for slot in self.env["planning.slot"].sudo().search([
            (source_field, "=", record.id), ("daadit_day_kind", "=", kind),
            ("daadit_day_state", "=", "planned"),
        ], order="resource_id, id"):
            self._unschedule_blocked(slot)
        return self._payload(employee, self._parse_day(day, tz), tz,
                             select="%s-%d" % (kind, record.id),
                             message=self.env._("Follow-up scheduled."))

    @api.model
    def unblock_work(self, kind, record_id, day=None):
        record = self._editable_work_record(kind, record_id)
        employee = self._employee()
        tz = self._tz(employee)
        record.write({
            "daadit_day_block_kind": False,
            "daadit_day_block_reason": False,
            "daadit_day_followup_date": False,
            "daadit_day_followup_user_id": False,
        })
        return self._payload(employee, self._parse_day(day, tz), tz,
                             select="%s-%d" % (kind, record.id),
                             message=self.env._("Work unblocked."))

    def _payload(self, employee, day, tz, select=None, message=None):
        now = self._now()
        today = self._today(tz)
        work = self._work_intervals(employee, day, tz)
        events = self._events(employee, day, tz)
        slots = self._slots(employee, day, tz)
        capacity_gaps = self._capacity_gaps(work, events)
        blocks = [self._slot_payload(slot, tz, now, capacity_gaps) for slot in slots]
        blocks += [self._event_payload(event, tz, now) for event in self._blocking_events(events)]
        blocks.sort(key=lambda block: block["sort"])
        queue = self._queue(employee, day, tz) if day >= today else []
        for item in queue:
            item["key"] = "%s-%d" % (item["kind"], item.get("activity_id") or item["res_id"])
            item.pop("sort", None)
        work_blocks = [b for b in blocks if b["kind"] != "event" and b["state"] == "planned"]
        closed = self._closed_day(employee, day, tz)
        closed_label = ""
        if closed:
            project = closed[:1].project_id if "project_id" in closed._fields else False
            closed_label = project.display_name if project else closed[:1].role_id.name
        return {
            "date": fields.Date.to_string(day),
            "label": _fmt_day(self.env, day),
            "is_today": day == today,
            "is_past": day < today,
            "now": self._local(now, tz).strftime("%H:%M"),
            "employee": employee.name,
            "can_edit_working_hours": self._can_edit_working_hours(employee),
            "blocks": blocks,
            "queue": queue[:QUEUE_LIMIT],
            "queue_total": len(queue),
            "blocked_work": self._blocked_work(today) if day >= today else [],
            "shared_elsewhere": self._shared_elsewhere() if day >= today else [],
            "goals": [{
                "id": slot.id, "title": (slot.name or "").split("\n", 1)[0],
                "done": slot.daadit_day_state == "done",
                "date": fields.Date.to_string(self._local_day(slot.start_datetime, tz)),
            } for slot in self._goals(employee, day)],
            "urgent": [item for item in queue if item["rank"] == RANK_URGENT][:5],
            "next_key": work_blocks[0]["key"] if work_blocks else False,
            "running_key": next((b["key"] for b in blocks if b.get("timer_running")), False),
            "select": select or False,
            "message": message or False,
            "off_day": not work,
            "closed_label": closed_label,
            "all_day": [event.name for event in events if event.allday],
            "stats": {
                **self._capacity_stats(capacity_gaps, slots, now),
                "done": sum(1 for b in blocks if b["kind"] != "event" and b["state"] == "done"),
                "open": len(work_blocks),
                "overflow": sum(1 for b in work_blocks if b["overflow"] and not b["outside_hours"]),
                "unplaced": len(self._past_open_slots(employee, today, tz)) if day == today else 0,
            },
            "week": self._week_payload(employee, day, tz),
        }

    # ------------------------------------------------------------------
    # Aanroepen vanuit het scherm
    # ------------------------------------------------------------------
    def _can_edit_working_hours(self, employee):
        employee = employee.with_user(self.env.user)
        return employee.has_access("read") and employee.has_access("write") and (
            "resource_calendar_id" in employee.fields_get(["resource_calendar_id"])
        )

    @api.model
    def get_working_hours_action(self):
        employee = self._employee()
        if not self._can_edit_working_hours(employee):
            raise UserError(
                self.env._("You need access to your employee record to edit working hours.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Working Hours"),
            "res_model": "hr.employee",
            "res_id": employee.id,
            "views": [(self.env.ref("daadit_my_day.view_my_day_working_hours").id, "form")],
            "target": "new",
        }

    @api.model
    def get_day(self, day=None):
        employee = self._employee()
        tz = self._tz(employee)
        day = self._parse_day(day, tz)
        today = self._today(tz)
        if day == today:
            if self.env.user.sudo().daadit_my_day_planned_on != today:
                self._plan_horizon(employee, day, tz)
            else:
                self._fill_new(employee, tz)
        return self._payload(employee, day, tz)

    @api.model
    def plan_day(self, day=None):
        employee = self._employee()
        tz = self._tz(employee)
        day = self._parse_day(day, tz)
        if day < self._today(tz):
            raise UserError(self.env._("You cannot schedule a day in the past."))
        self._plan_horizon(employee, day, tz)
        return self._payload(employee, day, tz)

    def _mark_done(self, slot):
        now = self._now()
        minutes = max(1, _minutes(slot.start_datetime, slot.end_datetime))
        values = {"daadit_day_state": "done", "daadit_day_done_at": now}
        if slot.start_datetime >= now:
            values.update(start_datetime=now - timedelta(minutes=minutes), end_datetime=now)
        elif slot.end_datetime > now:
            values["end_datetime"] = max(now, slot.start_datetime + timedelta(minutes=1))
        slot.write(values)

    def _closing_stage(self, ticket):
        """Eerste gesloten fase van het team, liefst een die de klant niet mailt."""
        stages = ticket.team_id.stage_ids.filtered(
            lambda s: s.fold and not any(w in s.name.lower() for w in ("cancel", "annul"))
        ).sorted("sequence")
        return stages.filtered(lambda s: not s.template_id)[:1] or stages[:1]

    def _close_record(self, slot):
        """Sluit het echte ticket of de echte taak af, met de rechten van de gebruiker."""
        if slot.daadit_ticket_id:
            ticket = self.env["helpdesk.ticket"].browse(slot.daadit_ticket_id.id)
            stage = self._closing_stage(ticket)
            if not stage:
                raise UserError(self.env._("This helpdesk team has no closed stage."))
            ticket.write({"stage_id": stage.id})
            return self.env._("Ticket moved to %s.") % stage.name
        if slot.daadit_task_id:
            self.env["project.task"].browse(slot.daadit_task_id.id).write({"state": "1_done"})
            return self.env._("Task marked as done.")
        raise UserError(self.env._("There is no linked record to close for personal work."))

    @api.model
    def complete_block(self, block_id):
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        self._timer_stop(slot)
        self._mark_done(slot)
        self._reflow(employee, day, tz)
        return self._payload(employee, day, tz, message=self.env._("Done. Here is what is next."))

    @api.model
    def close_block(self, block_id):
        """Afsluiten en volgende: het werk zelf gaat dicht, niet alleen het blok."""
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        self._timer_stop(slot)
        message = self._close_record(slot)
        self._mark_done(slot)
        self._reflow(employee, day, tz)
        return self._payload(employee, day, tz, message=message)

    @api.model
    def later_today(self, block_id):
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        minutes = max(MIN_BLOCK, _minutes(slot.start_datetime, slot.end_datetime))
        placement = self._find_placement(
            employee, max(day, self._today(tz)), tz, minutes,
            skip_closed=slot.daadit_day_kind == "ticket", after=slot.end_datetime,
        )
        return self._move_manually(slot, employee, day, tz, placement)

    @api.model
    def plan_outside_hours(self, block_id):
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        if slot.daadit_day_state != "planned" or day < self._today(tz):
            raise UserError(self.env._("Only open work can be scheduled outside working hours."))
        if slot.daadit_ticket_id and self._closed_day(employee, day, tz):
            raise UserError(self.env._("This day is reserved for work other than helpdesk."))
        minutes = max(MIN_BLOCK, _minutes(slot.start_datetime, slot.end_datetime))
        placed = self._outside_hours_gap(employee, day, tz, minutes, exclude=slot)
        if not placed:
            raise UserError(self.env._(
                "There is no suitable time outside working hours today."
            ))
        self._timer_stop(slot)
        self._move_slot(slot, employee, tz, (day, placed), outside_hours=True)
        return self._payload(employee, day, tz, select="slot-%d" % slot.id,
                             message=self.env._("Scheduled outside working hours."))

    def _move_manually(self, slot, employee, day, tz, placement):
        if slot.daadit_day_state != "planned":
            raise UserError(self.env._("Only open work can be moved."))
        if not placement:
            raise UserError(self.env._(
                "There is no suitable time in the next %s days.", HORIZON_DAYS,
            ))
        self._timer_stop(slot)
        self._move_slot(slot, employee, tz, placement)
        target = placement[0]
        message = self.env._("Deferred to %s.", _fmt_day(self.env, target))
        return self._payload(employee, day, tz, message=message)

    @api.model
    def defer_block(self, block_id):
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        minutes = max(MIN_BLOCK, _minutes(slot.start_datetime, slot.end_datetime))
        placement = self._next_available_slot(
            employee, max(day, self._today(tz)), tz, minutes,
            skip_closed=slot.daadit_day_kind == "ticket",
        )
        return self._move_manually(slot, employee, day, tz, placement)

    @api.model
    def set_block_minutes(self, block_id, minutes):
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        minutes = max(MIN_BLOCK, min(int(minutes), 480))
        end = slot.start_datetime + timedelta(minutes=minutes)
        gaps = self._capacity_gaps(self._work_intervals(employee, day, tz),
                                   self._events(employee, day, tz))
        if slot.daadit_day_outside_hours:
            _start, day_end = self._bounds(day, tz)
            gaps = self._capacity_gaps([[slot.start_datetime, day_end]],
                                       self._events(employee, day, tz))
        gaps = self._unoccupied_gaps(employee, day, tz, gaps, exclude=slot)
        if any(start <= slot.start_datetime and end <= stop for start, stop in gaps):
            slot.write({"end_datetime": end, "daadit_day_pinned": True})
            return self._payload(employee, day, tz, select="slot-%d" % slot.id)
        if slot.daadit_timer_started_at or slot.daadit_day_outside_hours:
            raise UserError(self.env._(
                "That duration does not fit here. Pause or move the block first."
            ))
        placement = self._find_placement(
            employee, max(day, self._today(tz)), tz, minutes,
            skip_closed=slot.daadit_day_kind == "ticket", exclude=slot,
        )
        return self._move_manually(slot, employee, day, tz, placement)

    @api.model
    def remove_block(self, block_id):
        slot, employee = self._own_slot(block_id)
        tz = self._tz(employee)
        day = self._local_day(slot.start_datetime, tz)
        self._timer_stop(slot)
        slot.unlink()
        self._reflow(employee, day, tz)
        return self._payload(employee, day, tz, message=self.env._("Removed from your day."))

    def _insert_block(self, employee, day, tz, item, outside_hours=False):
        if outside_hours:
            placed = self._outside_hours_gap(employee, day, tz, item["minutes"])
            placement = (day, placed) if placed else None
        else:
            placement = self._find_placement(
                employee, day, tz, item["minutes"], skip_closed=item["kind"] == "ticket",
            )
            if item.get("rank") == RANK_URGENT and (not placement or placement[0] != day):
                placement = self._urgent_focus_placement(employee, day, tz, item) or placement
        if not placement:
            return self.env["planning.slot"]
        target, (start, end) = placement
        slots = self._slots(employee, target, tz, states=["planned"])
        sequence = max(slots.mapped("daadit_day_sequence") or [0]) + 10
        values = self._slot_vals(employee, item, start, end, sequence, True)
        values["daadit_day_outside_hours"] = outside_hours
        return self.env["planning.slot"].sudo().create(values)

    def _insert_payload(self, employee, day, tz, slot, outside_hours=False):
        if not slot:
            if outside_hours:
                raise UserError(self.env._(
                    "There is no suitable time outside working hours today."
                ))
            raise UserError(self.env._(
                "There is no suitable time in the next %s days.", HORIZON_DAYS,
            ))
        target = self._local_day(slot.start_datetime, tz)
        message = None
        if target != day:
            message = self.env._("Scheduled for %s.", _fmt_day(self.env, target))
        return self._payload(employee, target, tz, select="slot-%d" % slot.id, message=message)

    @api.model
    def add_own_work(self, title, minutes=30, day=None, outside_hours=False):
        title = (title or "").strip()
        if not title:
            raise UserError(self.env._("Enter a name for your work."))
        employee = self._employee()
        tz = self._tz(employee)
        day = self._parse_day(day, tz)
        if day < self._today(tz):
            raise UserError(self.env._("You cannot schedule work in the past."))
        slot = self._insert_block(employee, day, tz, {
            "kind": "own",
            "res_id": False,
            "title": title,
            "subtitle": self.env._("Personal work"),
            "reason": self.env._("Added manually"),
            "minutes": max(MIN_BLOCK, min(int(minutes), 480)),
        }, outside_hours=outside_hours is True)
        return self._insert_payload(employee, day, tz, slot, outside_hours is True)

    @api.model
    def pull_item(self, kind, res_id, day=None):
        """Haal werk uit de wachtlijst naar het eerstvolgende passende blok."""
        employee = self._employee()
        tz = self._tz(employee)
        day = self._parse_day(day, tz)
        if day < self._today(tz):
            raise UserError(self.env._("You cannot schedule work in the past."))
        items = self._queue(employee, day, tz)
        item = next((i for i in items if i["kind"] == kind
                     and (i.get("activity_id") or i["res_id"]) == int(res_id)), None)
        if not item:
            raise UserError(self.env._("This work is no longer on your waiting list."))
        return self._insert_payload(employee, day, tz, self._insert_block(employee, day, tz, item))

    @api.model
    def get_urgent(self):
        employee = self._employee()
        tz = self._tz(employee)
        items = [i for i in self._queue(employee, self._today(tz), tz) if i["rank"] == RANK_URGENT]
        for item in items:
            item["key"] = "%s-%d" % (item["kind"], item.get("activity_id") or item["res_id"])
            item.pop("sort", None)
        return items[:5]
