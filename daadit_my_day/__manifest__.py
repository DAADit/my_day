# -*- coding: utf-8 -*-
{
    "name": "My Day",
    "summary": "Work from one timeline for appointments, tickets, tasks and personal work.",
    "description": """
My Day
======
Open My Day to see what you are working on, in what order and why.
The schedule sits on the left and the current ticket or task is on the
right. Use "Done and next" to move on to the next item.

Work blocks are Planning shifts linked to a ticket or task. Working hours,
time off and appointments determine availability. A shift with the
"No helpdesk" role keeps the day free of tickets.
""",
    "version": "19.0.1.10.0",
    "category": "Productivity",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": [
        "web",
        "calendar",
        "hr",
        "project",
        "hr_timesheet",
        "helpdesk",
        "helpdesk_timesheet",
        "planning",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/my_day_security.xml",
        "data/planning_role_data.xml",
        "data/ir_cron.xml",
        "views/planning_role_views.xml",
        "views/my_day_action.xml",
        "views/helpdesk_team_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "daadit_my_day/static/src/js/my_day_gantt.js",
            "daadit_my_day/static/src/js/my_day_priorities.js",
            "daadit_my_day/static/src/js/my_day_action.js",
            "daadit_my_day/static/src/xml/my_day_gantt.xml",
            "daadit_my_day/static/src/xml/my_day_priorities.xml",
            "daadit_my_day/static/src/xml/my_day_action.xml",
            "daadit_my_day/static/src/scss/my_day.scss",
        ],
    },
    "installable": True,
    "application": True,
}
