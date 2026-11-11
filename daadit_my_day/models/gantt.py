"""Gegevens voor de Gantt-weergave: per dag een rij met rooster, afspraken en blokken.

Tijden gaan als minuten vanaf middernacht in de tijdzone van de medewerker naar
het scherm, zodat het scherm alleen hoeft te tekenen.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools.misc import format_date

from .my_day import _fmt_day


class MyDayGantt(models.AbstractModel):
    _inherit = "daadit.my.day"

    def _day_minute(self, value, day, tz):
        local = self._local(value, tz)
        if local.date() < day:
            return 0
        if local.date() > day:
            return 1440
        return local.hour * 60 + local.minute

    def _gantt_identity(self, slot):
        source = slot.daadit_ticket_id or slot.daadit_task_id or slot.daadit_activity_id
        return "%s-%s" % (slot.daadit_day_kind, source.id if source else "own-%d" % slot.id)

    def _gantt_row(self, employee, day, tz):
        now = self._now()
        work = self._work_intervals(employee, day, tz)
        events = self._blocking_events(self._events(employee, day, tz))
        items = []
        for slot in self._slots(employee, day, tz):
            ticket, task = slot.daadit_ticket_id, slot.daadit_task_id
            title = ticket.name or task.name or (slot.name or "").split("\n", 1)[0]
            items.append({
                "key": "slot-%d" % slot.id,
                "block_id": slot.id,
                "identity": self._gantt_identity(slot),
                "kind": slot.daadit_day_kind,
                "title": title,
                "start": self._day_minute(slot.start_datetime, day, tz),
                "end": self._day_minute(slot.end_datetime, day, tz),
                "time": "%s–%s" % (self._local(slot.start_datetime, tz).strftime("%H:%M"),
                                   self._local(slot.end_datetime, tz).strftime("%H:%M")),
                "state": slot.daadit_day_state,
                "focus": slot.daadit_day_focus,
                "goal": bool(slot.daadit_day_goal_date),
                "pinned": not self._is_auto(slot),
                "running": bool(slot.daadit_timer_started_at),
                "status": "same",
            })
        for event in events:
            items.append({
                "key": "event-%d" % event.id,
                "identity": "event-%d" % event.id,
                "kind": "event",
                "title": event.name,
                "start": self._day_minute(event.start, day, tz),
                "end": self._day_minute(event.stop, day, tz),
                "time": "%s–%s" % (self._local(event.start, tz).strftime("%H:%M"),
                                   self._local(event.stop, tz).strftime("%H:%M")),
                "state": "done" if event.stop <= now else "planned",
                "status": "same",
            })
        items.sort(key=lambda item: (item["start"], item["end"]))
        return {
            "date": fields.Date.to_string(day),
            "label": _fmt_day(self.env, day),
            "weekday": format_date(self.env, day, date_format="EEE"),
            "is_today": day == self._today(tz),
            "closed": bool(self._closed_day(employee, day, tz)),
            "work": [[self._day_minute(start, day, tz), self._day_minute(end, day, tz)]
                     for start, end in work],
            "now": self._day_minute(now, day, tz) if day == self._today(tz) else False,
            "items": items,
        }

    def _gantt_rows(self, employee, days, tz):
        return [self._gantt_row(employee, day, tz) for day in days]

    def _week_days(self, employee, day, tz):
        monday = day - timedelta(days=day.weekday())
        result = []
        for offset in range(7):
            current = monday + timedelta(days=offset)
            if offset > 4 and current != day and not self._work_intervals(employee, current, tz) \
                    and not self._slots(employee, current, tz):
                continue
            result.append(current)
        return result

    @api.model
    def get_gantt(self, day=None):
        employee = self._employee()
        tz = self._tz(employee)
        day = self._parse_day(day, tz)
        return {"rows": self._gantt_rows(employee, self._week_days(employee, day, tz), tz),
                "selected": fields.Date.to_string(day)}

    def _gantt_compare(self, before_rows, after_rows):
        """Markeer in de nieuwe indeling wat nieuw, verschoven, gelijk of vervallen is."""
        def work_items(rows):
            pool = defaultdict(list)
            for row in rows:
                for item in row["items"]:
                    if item["kind"] != "event" and item["state"] == "planned":
                        pool[item["identity"]].append((row, item))
            return pool

        old, new = work_items(before_rows), work_items(after_rows)
        rows_by_date = {row["date"]: row for row in after_rows}
        for identity in old.keys() | new.keys():
            previous, current = old[identity], new[identity]
            for entry in previous[:]:
                match = next((pair for pair in current
                              if pair[0]["date"] == entry[0]["date"]
                              and pair[1]["start"] == entry[1]["start"]
                              and pair[1]["end"] == entry[1]["end"]), None)
                if match:
                    previous.remove(entry)
                    current.remove(match)
            for index, (row, item) in enumerate(current):
                if index < len(previous):
                    old_row, old_item = previous[index]
                    item["status"] = "moved"
                    item["from"] = "%s %s" % (old_row["weekday"], old_item["time"])
                else:
                    item["status"] = "new"
            for index, (old_row, old_item) in enumerate(previous):
                ghost = dict(old_item, key="ghost-%s" % old_item["key"], block_id=False,
                             status="moved_away" if index < len(current) else "removed")
                target = rows_by_date.get(old_row["date"])
                if target is not None:
                    target["items"].append(ghost)
        for row in after_rows:
            row["items"].sort(key=lambda item: (item["start"], item["end"]))
        return after_rows
