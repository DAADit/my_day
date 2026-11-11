{
    "name": "My Day — Microsoft 365",
    "summary": "Select Microsoft 365 messages as work in My Day.",
    "version": "19.0.1.0.4",
    "category": "Productivity",
    "author": "DAADit",
    "license": "LGPL-3",
    "depends": ["daadit_my_day"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "views/mail_import.xml",
    ],
    "installable": True,
}
