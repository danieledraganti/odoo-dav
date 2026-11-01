from odoo import models


class DavProvider(models.AbstractModel):
    """Registry of DAV resource providers.

    Optional modules ``_inherit = 'dav.provider'`` and extend
    :meth:`_dav_mounts` / :meth:`_dav_features` (always calling ``super``).
    """
    _name = 'dav.provider'
    _description = 'DAV resource provider registry'

    def _dav_mounts(self, ctx):
        """Return the list of :class:`Resource` to mount in the tree.

        Each resource's ``path`` tells where it is mounted. Intermediate
        collections are created automatically.
        """
        from ..dav.contacts import AddressBook, Principal
        return [Principal(ctx, ('principals', ctx.login)),
                AddressBook(ctx, ('addressbooks', ctx.login, 'contacts'))]

    def _dav_features(self, ctx):
        """Tokens advertised in the ``DAV:`` response header."""
        return ['addressbook']
