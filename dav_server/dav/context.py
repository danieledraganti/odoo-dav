"""Per-request DAV context: user, settings and the resource tree."""
from urllib.parse import quote

from .resource import VirtualCollection

PREFIX = '/dav'


class DavContext:

    def __init__(self, env):
        # everything the client sees (names, descriptions, categories...) is in the user's language
        lang = env.user.lang
        if lang and env.context.get('lang') != lang:
            env = env(context=dict(env.context, lang=lang))
        self.env = env
        self.user = env.user
        self.login = env.user.login
        self.prefix = PREFIX
        self.can_write = env.user.has_group('dav_server.group_dav_manager')
        self.principal_href = '%s/principals/%s/' % (PREFIX, quote(self.login, safe='@'))
        self._root = None
        self._icp = env['ir.config_parameter'].sudo()
        self.db_uuid = self._icp.get_param('database.uuid') or 'odoo'

    def record_url(self, model, res_id):
        """Link that opens the record in the Odoo web client ('' when the base URL is unknown)."""
        base = (self._icp.get_param('web.base.url') or '').rstrip('/')
        return '%s/odoo/%s/%d' % (base, model, res_id) if base and model and res_id else ''

    def can_read(self, model):
        """Whether the user may read `model` at all (optional collections are hidden otherwise)."""
        return model in self.env and self.env[model].has_access('read')

    # ------------------------------------------------------------ settings
    def param(self, key, default=None):
        value = self._icp.get_param('dav_server.' + key)
        return default if value in (None, False, '') else value

    def param_int(self, key, default):
        try:
            return int(self.param(key, default))
        except (TypeError, ValueError):
            return default

    def param_bool(self, key, default=False):
        value = self.param(key)
        if value is None:
            return default
        return str(value).lower() in ('1', 'true', 'yes')

    # ---------------------------------------------------------------- tree
    @property
    def root(self):
        if self._root is None:
            self._root = self._build_tree()
        return self._root

    def _build_tree(self):
        root = VirtualCollection(self, (), display='Odoo')
        mounts = self.env['dav.provider']._dav_mounts(self)
        self.features = set(self.env['dav.provider']._dav_features(self))
        for resource in mounts:
            node = root
            # create the intermediate virtual collections
            for depth in range(1, len(resource.path)):
                seg = resource.path[depth - 1]
                child = node.get_child(seg)
                if child is None:
                    child = VirtualCollection(self, resource.path[:depth])
                    node.add(child)
                node = child
            node.add(resource)
        return root

    @property
    def features_header(self):
        self.root  # noqa: B018  (make sure features are computed)
        return ', '.join(['1', '3'] + sorted(self.features))

    def canonical_segments(self, segments):
        """`segments` with the fixed (structural) names spelled as in the tree, ignoring case."""
        node, out = self.root, []
        for index, seg in enumerate(segments):
            if not isinstance(node, VirtualCollection):
                return out + list(segments[index:])
            name = node.canonical_name(seg)
            out.append(name)
            node = node.get_child(name)
            if node is None:
                return out + list(segments[index + 1:])
        return out

    def has_path(self, *path):
        return self.resolve(path) is not None

    def resolve(self, segments):
        node = self.root
        for seg in segments:
            node = node.get_child(seg)
            if node is None:
                return None
        return node

    def href_to_segments(self, href):
        """Map an absolute href (as found in REPORT bodies) to path segments."""
        from urllib.parse import unquote, urlsplit
        path = unquote(urlsplit(href).path)
        if not (path == self.prefix or path.startswith(self.prefix + '/')):
            return None
        segments = [s for s in path[len(self.prefix):].split('/') if s]
        if any(s in ('.', '..') or '\x00' in s for s in segments):
            return None
        return segments
