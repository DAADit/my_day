# Mijn Dag

Eén app waarin een medewerker de hele dag gevoed wordt en werkt. Links staat de
dag, rechts het gewone formulier van het ticket of de taak.

## Hoe de dag gevuld wordt

- Tijd: werkrooster en verlof, min afspraken uit de Agenda.
- Volgorde: urgent, concept-antwoord klaar, verlopen deadlines, deadlines deze
  week, beoordelingen en hoge prioriteit, deadlines binnen twee weken, overige
  tickets en daarna taken zonder deadline.
- Blokken zijn `planning.slot`-records in concept. Ze staan bewust niet in de
  Agenda, want de nachtelijke urencontrole boekt agenda-items als uren.
- Een dienst met een rol met "Geen helpdesk op deze dag" houdt de dag vrij van
  tickets. Staat er een project op, dan komen alleen taken van dat project.

## Automatisch

- Elke nacht wordt al het open werk over de komende vijf werkdagen ingedeeld.
  Elk kwartier komt nieuw werk erbij; urgent werk direct na het huidige blok.
- Openen van een blok start de klok, wisselen stopt hem. Per blok is er één
  urenregel die meegroeit. Komt er tien minuten geen teken van leven van het
  scherm, dan telt de tijd daarna niet mee.
- Een terugkerend schermsignaal kan een verlopen klok niet herstellen: de
  sessie stopt op het laatste teken van leven. De medewerker kan daarna
  hervatten. Een nieuw schermsignaal geldt alleen voor het geopende blok,
  zodat een oud tabblad geen andere klok in leven houdt.
- Geen uren op eigen werk, op intern werk en op klanten in
  `daadit_my_day.no_hours_partner_ids`, of als "Geen uren" aanstaat.

## Instellingen (systeemparameters, in minuten of procenten)

`daadit_my_day.ticket_minutes` (30), `daadit_my_day.concept_minutes` (5),
`daadit_my_day.task_minutes` (60), `daadit_my_day.buffer_percent` (10),
`daadit_my_day.timesheet_rounding` (5).
