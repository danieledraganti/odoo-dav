import base64
import binascii
import logging

from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Forbidden, Unauthorized

from odoo import models
from odoo.exceptions import AccessDenied
from odoo.http import request

_logger = logging.getLogger(__name__)

GROUP_DAV = 'dav_server.group_dav_user'


def _challenge(message='Authentication required'):
    return Unauthorized(message, www_authenticate=WWWAuthenticate('basic', {'realm': 'Odoo DAV'}))


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_method_dav(cls):
        """Authenticate a DAV request with an Odoo API key.

        - ``Authorization: Basic base64(login:api_key)`` or ``Bearer <api_key>``.
        - Sessions/cookies are never used and never saved.
        - The key must belong to an active user of the DAV group; for Basic
          the login must be the one of the key's owner.
        - Failed attempts feed the standard Odoo login cooldown.
        """
        icp = request.env['ir.config_parameter'].sudo()
        insecure_ok = str(icp.get_param('dav_server.allow_insecure', '')).lower() in ('1', 'true')
        if not insecure_ok and request.httprequest.scheme != 'https':
            raise Forbidden('HTTPS is required for DAV access.')

        header = request.httprequest.headers.get('Authorization', '')
        scheme, _sep, value = header.partition(' ')
        scheme, value = scheme.lower(), value.strip()
        login = key = None
        if scheme == 'basic':
            try:
                decoded = base64.b64decode(value, validate=True).decode('utf-8')
            except (binascii.Error, UnicodeDecodeError, ValueError):
                raise _challenge()
            login, _colon, key = decoded.partition(':')
        elif scheme == 'bearer':
            key = value
        if not key:
            raise _challenge()

        users = request.env['res.users']
        try:
            with users._assert_can_auth(user=login):
                uid = request.env['res.users.apikeys']._check_credentials(scope='dav', key=key)
                if not uid:
                    raise AccessDenied()
                if login is not None and users.sudo().browse(uid).login != login:
                    raise AccessDenied()
        except AccessDenied:
            raise _challenge()

        # switch to the user first: a cold group-definitions cache cannot be computed
        # on the bare request env (no default env on the transaction yet)
        request.update_env(user=uid)
        if not request.env.user.has_group(GROUP_DAV):
            raise Forbidden('This user is not allowed to use DAV.')
        request.update_context(**request.env.user.context_get())
        request.update_context(allowed_company_ids=request.env.user.company_ids.ids)
        request.session.can_save = False
