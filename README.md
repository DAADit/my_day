# My Day for Odoo 19

My Day brings open work and upcoming appointments together. Add this repository to the Odoo add-ons path, refresh the Apps list, and choose the installation that fits the customer.

[Korte installatiehandleiding voor klanten met To-do of Helpdesk](INSTALLATIE.md)

| Add-on | Use it for | Requirements |
| --- | --- | --- |
| `daadit_my_day_core` | Open work, personal drag-and-drop priorities, employee filters, assignment, direct record access, and upcoming appointments | Odoo Web and Mail |
| `daadit_my_day` | The full day planner with working hours, automatic scheduling, focus blocks, time tracking, blocked-work follow-up, team priorities, and Gantt | Odoo 19 Enterprise with Calendar, Employees, Project, Helpdesk, Timesheets, Helpdesk Timesheets, and Planning |
| `daadit_my_day_microsoft` | Optional delegated Microsoft 365 mail import for the full planner | Full planner and a customer-owned Microsoft Entra application |

Choose one of the first two add-ons per database. They expose separate My Day launchers and store their personal priority order separately.

## Sources in the lightweight installation

| Source | Available when |
| --- | --- |
| To-do | Odoo To-do is installed. Odoo uses the Project task model internally for To-do records. |
| Project tasks | The Project task model is available. |
| Helpdesk | The Helpdesk ticket model is available. |
| Activities | Odoo Mail is available and the user can open the linked record. |
| Appointments | Odoo Calendar is installed. |

Missing sources are skipped. Installing a supported source later makes its records available without changing My Day configuration. Odoo's model permissions, record rules, and active companies determine which records each user can see or change. A separate add-on can extend `daadit.my.day.sources._sources()` to add another work source.

For Microsoft 365, configure the customer’s own Entra application as a web application with the HTTPS redirect path `/my-day/microsoft/callback`. Enable delegated `User.Read` and `Mail.Read`. Set the `daadit_my_day_microsoft.tenant_id`, `.client_id`, `.client_secret`, `.redirect_uri`, and `.allowed_user_ids` Odoo system parameters using that same prefix. The last parameter is a comma-separated list of internal Odoo user IDs. Each user grants delegated consent for their own mailbox. The repository contains no tenant credentials.

## Test installations

Use separate empty databases for each installation profile. The core test suite checks To-do and Helpdesk when those sources are installed.

```sh
odoo-bin -d my_day_empty -i daadit_my_day_core --test-tags /daadit_my_day_core --stop-after-init
odoo-bin -d my_day_todo -i project_todo,daadit_my_day_core --test-tags /daadit_my_day_core --stop-after-init
odoo-bin -d my_day_helpdesk -i helpdesk,daadit_my_day_core --test-tags /daadit_my_day_core --stop-after-init
```

The add-ons declare LGPL-3.0 licensing in their manifests.
