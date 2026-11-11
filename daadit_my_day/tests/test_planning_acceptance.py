from datetime import date, datetime

import pytz

from odoo.exceptions import UserError
from odoo.tests import common, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPlanningAcceptance(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="my_day_acceptance_user", groups="helpdesk.group_helpdesk_manager",
        )
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "My Day acceptance roster",
            "tz": "Europe/Amsterdam",
            "attendance_ids": [(5, 0, 0), *[(0, 0, {
                "name": label, "dayofweek": "0", "day_period": period,
                "hour_from": start, "hour_to": end,
            }) for label, period, start, end in (
                ("Ochtend", "morning", 9, 12), ("Middag", "afternoon", 13, 16),
            )]],
        })
        cls.employee = cls.env["hr.employee"].create({
            "name": "My Day acceptance employee", "user_id": cls.user.id,
            "resource_calendar_id": cls.calendar.id,
        })
        cls.model = cls.env["daadit.my.day"].with_user(cls.user)
        cls.tz = pytz.timezone("Europe/Amsterdam")

    def test_working_hours_opens_own_employee_for_hr_user(self):
        with self.assertRaises(UserError):
            self.model.get_working_hours_action()

        hr_user = new_test_user(self.env, login="my_day_roster_hr", groups="hr.group_hr_manager")
        employee = self.env["hr.employee"].create({
            "name": "My Day HR roster employee",
            "user_id": hr_user.id,
            "resource_calendar_id": self.calendar.id,
        })
        action = self.model.with_user(hr_user).get_working_hours_action()
        self.assertEqual(action["res_model"], "hr.employee")
        self.assertEqual(action["res_id"], employee.id)
        self.assertEqual(action["target"], "new")

    def test_roster_keeps_local_hours_across_daylight_saving_change(self):
        for day, offset in ((date(2026, 10, 19), 2), (date(2026, 11, 16), 1)):
            with self.subTest(day=day):
                self.assertEqual(self.model._work_intervals(self.employee, day, self.tz), [
                    [datetime(day.year, day.month, day.day, 9 - offset),
                     datetime(day.year, day.month, day.day, 12 - offset)],
                    [datetime(day.year, day.month, day.day, 13 - offset),
                     datetime(day.year, day.month, day.day, 16 - offset)],
                ])

    def test_leave_and_busy_calendar_event_remove_capacity(self):
        day = date(2026, 11, 16)
        self.env["resource.calendar.leaves"].create({
            "name": "Acceptance leave", "calendar_id": self.calendar.id,
            "resource_id": self.employee.resource_id.id,
            "date_from": datetime(2026, 11, 16, 8), "date_to": datetime(2026, 11, 16, 10),
        })
        self.env["calendar.event"].with_context(no_mail_to_attendees=True).create({
            "name": "Acceptance busy event", "user_id": self.user.id,
            "partner_ids": [(6, 0, self.user.partner_id.ids)], "show_as": "busy",
            "start": datetime(2026, 11, 16, 12), "stop": datetime(2026, 11, 16, 13),
        })
        self.assertEqual(self.model._free_gaps(self.employee, day, self.tz), [
            [datetime(2026, 11, 16, 10), datetime(2026, 11, 16, 11)],
            [datetime(2026, 11, 16, 13), datetime(2026, 11, 16, 15)],
        ])

    def test_urgent_ticket_precedes_normal_ticket(self):
        team = self.env["helpdesk.team"].create({"name": "My Day acceptance test"})
        normal, urgent = self.env["helpdesk.ticket"].create([
            {"name": "Acceptance normal", "team_id": team.id, "user_id": self.user.id},
            {"name": "Acceptance urgent", "team_id": team.id, "user_id": self.user.id,
             "priority": "3"},
        ])
        day = self.model._today(self.tz)
        queue = self.model._queue(self.employee, day, self.tz)
        self.assertEqual([i["res_id"] for i in queue if i["kind"] == "ticket"],
                         [urgent.id, normal.id])
