"""Base classes of the DAV resource tree."""
import email.utils
import datetime
from urllib.parse import quote

from . import xml
from .xml import q

NOT_FOUND = object()


class DavError(Exception):
    """Raised by resources; converted to an HTTP response by the controller."""

    def __init__(self, status, message='', preconditions=()):
        super().__init__(message)
        self.status = status
        self.message = message
        self.preconditions = preconditions


def http_date(dt):
    if not dt:
        return None
    return email.utils.format_datetime(dt.replace(tzinfo=datetime.timezone.utc), usegmt=True)


def iso_z(dt):
    return dt.strftime('%Y%m%dT%H%M%SZ') if dt else ''


class Resource:
    is_collection = False
    #: reports a collection understands: list of qnames
    reports = ()
    #: whether write methods may reach this resource at all
    writable = False

    def __init__(self, ctx, path):
        self.ctx = ctx
        self.path = tuple(path)

    # ------------------------------------------------------------ identity
    @property
    def href(self):
        base = self.ctx.prefix + ''.join('/' + quote(seg, safe='@') for seg in self.path)
        if self.is_collection and not base.endswith('/'):
            base += '/'
        return base or '/'

    @property
    def name(self):
        return self.path[-1] if self.path else ''

    # ---------------------------------------------------------- properties
    def display_name(self):
        return self.name

    def resource_types(self):
        return [xml.el(q('D', 'collection'))] if self.is_collection else []

    def etag(self):
        return None

    def last_modified(self):
        return None

    def content_type(self):
        return None

    def content_length(self):
        return None

    def allprop_names(self):
        names = [q('D', 'resourcetype'), q('D', 'displayname')]
        if not self.is_collection:
            names += [q('D', 'getetag'), q('D', 'getcontenttype'), q('D', 'getcontentlength'),
                      q('D', 'getlastmodified')]
        return names

    def prop(self, name):
        """Return the value of a property, or NOT_FOUND."""
        D = lambda n: q('D', n)  # noqa: E731
        if name == D('resourcetype'):
            return self.resource_types()
        if name == D('displayname'):
            return self.display_name()
        if name == D('getetag'):
            tag = self.etag()
            return '"%s"' % tag if tag else NOT_FOUND
        if name == D('getlastmodified'):
            return http_date(self.last_modified()) or NOT_FOUND
        if name == D('getcontenttype'):
            return self.content_type() or NOT_FOUND
        if name == D('getcontentlength'):
            length = self.content_length()
            return str(length) if length is not None else NOT_FOUND
        if name in (D('current-user-principal'), D('owner')):
            return xml.href(self.ctx.principal_href)
        if name == D('supported-report-set'):
            return [xml.el(q('D', 'supported-report'),
                           xml.el(q('D', 'report'), xml.el(r))) for r in self.reports]
        if name == D('current-user-privilege-set'):
            privs = [xml.el(q('D', 'privilege'), xml.el(D('read')))]
            if self.writable and self.ctx.can_write:
                privs += [xml.el(q('D', 'privilege'), xml.el(D(p)))
                          for p in ('write', 'write-content', 'bind', 'unbind')]
            return privs
        if name == D('supportedlock') and '2' in getattr(self.ctx, 'features', ()):
            return [xml.el(D('lockentry'), [xml.el(D('lockscope'), xml.el(D('exclusive'))),
                                            xml.el(D('locktype'), xml.el(D('write')))])]
        if name == D('lockdiscovery') and '2' in getattr(self.ctx, 'features', ()):
            return []
        if name == D('principal-collection-set'):
            return xml.href(self.ctx.prefix + '/principals/')
        return NOT_FOUND

    # ------------------------------------------------------------ children
    def children(self):
        return []

    def get_child(self, name):
        return None

    # ------------------------------------------------------------- reading
    def read(self):
        """Return (bytes, content_type) for GET."""
        raise DavError(405, 'Not a readable resource')

    # ------------------------------------------------------------- writing
    def put(self, data, content_type, request_info):
        raise DavError(405)

    def delete(self):
        raise DavError(405)

    def create_child(self, name, data, content_type, request_info):
        raise DavError(405)

    def mkcol(self, name):
        raise DavError(405)

    def move(self, dest_parent, dest_name):
        raise DavError(405)

    # -------------------------------------------------------------- reports
    def query(self, root):
        """Members matching a *-query REPORT (filters are left to the client)."""
        return self.children()

    def sync(self, token):
        raise DavError(403, 'sync-collection not supported', (q('D', 'valid-sync-token'),))


class VirtualCollection(Resource):
    """Read-only structural collection (/dav/, /dav/addressbooks/, ...)."""
    is_collection = True

    def __init__(self, ctx, path, display=None):
        super().__init__(ctx, path)
        self._children = {}
        self._display = display

    def add(self, resource):
        self._children[resource.name] = resource

    def display_name(self):
        return self._display or self.name

    def children(self):
        return list(self._children.values())

    def get_child(self, name):
        return self._children.get(name)
