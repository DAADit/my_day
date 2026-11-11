{
    "name": "My Day",
    "summary": "See your open work from the Odoo apps you use.",
    "version": "19.0.1.0.0",
    "category": "Productivity",
    "author": "DAADit",
    "license": "LGPL-3",
    "depends": ["web", "mail"],
    "data": ["views/my_day.xml"],
    "assets": {"web.assets_backend": [
        "daadit_my_day_core/static/src/my_day.js",
        "daadit_my_day_core/static/src/my_day.xml",
    ]},
    "application": True,
    "installable": True,
}
