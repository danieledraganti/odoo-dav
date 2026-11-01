{
    'name': 'DAV Server',
    'summary': 'CardDAV / WebDAV / CalDAV access to Odoo data using API keys',
    'description': """
Exposes Odoo data to native DAV clients, authenticated with the standard
Odoo API keys. Everything runs as the authenticated user (no sudo): access
rights and record rules are always those of the Odoo UI.

This core module provides the authentication, the DAV engine and the CardDAV
address book (contacts). Optional modules add Documents, Tasks, Knowledge
and Calendar.
    """,
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['base', 'contacts'],
    'external_dependencies': {'python': ['vobject', 'lxml']},
    'data': [
        'security/dav_security.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
}
