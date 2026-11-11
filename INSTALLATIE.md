# Mijn Dag installeren met To-do of Helpdesk

Gebruik de lichte module `daadit_my_day_core` op Odoo 19 via Odoo.sh of een eigen Odoo-server. De module heeft Odoo Web en Mail nodig. Voor Helpdesk heb je Odoo Enterprise nodig.

1. Download `daadit_my_day_core-v19.0.1.0.0.zip` uit de release en pak het bestand uit. Zet op Odoo.sh de map `daadit_my_day_core` in de Git-repository van je Odoo-project en wacht tot de build klaar is. Zet op een eigen server de map in een bestaande map uit `addons_path` en herstart Odoo.
2. Open **Apps** als beheerder. Activeer de ontwikkelaarsmodus als **Apps-lijst bijwerken** ontbreekt. Werk de lijst bij, zoek op `daadit_my_day_core` en activeer **Mijn Dag** (of **My Day** in een Engelstalige omgeving).
3. Open **Mijn Dag** met je gewone interne gebruikersaccount en controleer **Mijn prioriteiten**.

| Je gebruikt | Controle na installatie |
| --- | --- |
| Alleen To-do | Maak in To-do een open item op je naam. Open het daarna vanuit Mijn Dag. Odoo gebruikt het Project-model om To-do-items op te slaan. |
| Alleen Helpdesk | Wijs een open ticket aan jezelf toe. Open het daarna vanuit Mijn Dag met een account dat toegang heeft tot het ticket. |

Mijn Dag herkent de geïnstalleerde bron automatisch. De bestaande Odoo-toegangsrechten bepalen welk werk je ziet en mag aanpassen.

[Odoo-apps installeren](https://www.odoo.com/documentation/19.0/applications/general/apps_modules.html) · [Eigen modules op Odoo.sh](https://www.odoo.com/documentation/19.0/administration/odoo_sh/getting_started/create.html)
