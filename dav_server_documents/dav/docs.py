"""WebDAV view of Odoo Documents. Everything runs as the DAV user (no sudo)."""
import hashlib
import logging
import mimetypes
import re

from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.dav_server.dav import xml
from odoo.addons.dav_server.dav.resource import NOT_FOUND, DavError, Resource
from odoo.addons.dav_server.dav.xml import q

_logger = logging.getLogger(__name__)

JUNK = re.compile(r'^(\.DS_Store|\._.*|~\$.*|.*\.tmp|Thumbs\.db|desktop\.ini|\.Spotlight-V100|'
                  r'\.Trashes|\.fseventsd|\.TemporaryItems|\.localized|\.hidden)$', re.I)
_DUP = re.compile(r' \((\d+)\)(\.[^./]*)?$')

AREAS = (('Company', 'COMPANY'), ('My Files', 'MY'), ('Shared with me', 'SHARED'))

# documents that make sense as WebDAV members
CHILD_DOMAIN = [('shortcut_document_id', '=', False),
                '|', '|', ('type', '=', 'folder'), ('type', '=', 'url'), ('attachment_id', '!=', False)]


def safe_name(name, doc_id):
    cleaned = re.sub(r'[\x00-\x1f/\\]', '_', (name or '').strip()).strip('.') if name else ''
    return cleaned or 'unnamed-%d' % doc_id


def with_suffix(name, doc_id):
    base, dot, ext = name.rpartition('.')
    return '%s (%d).%s' % (base, doc_id, ext) if dot and base else '%s (%d)' % (name, doc_id)


def strip_suffix(name, doc_id):
    match = _DUP.search(name)
    if match and int(match.group(1)) == doc_id:
        return name[:match.start()] + (match.group(2) or '')
    return name


def dav_errors(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (AccessError, UserError, ValidationError) as exc:
            raise DavError(403, str(exc))
    return wrapper


class DocsCollection(Resource):
    """Common behaviour of the area roots and real folders."""
    is_collection = True
    writable = True
    reports = ()
    scope = None            # COMPANY / MY / SHARED
    folder = None           # documents.document record or None for area roots

    @property
    def env(self):
        return self.ctx.env

    def _list_domain(self):
        raise NotImplementedError

    def _documents(self):
        return self.env['documents.document'].search(self._list_domain() + CHILD_DOMAIN, order='id')

    def _entries(self):
        """List of (displayed name, document), de-duplicating sibling names."""
        docs = self._documents()
        seen, entries = set(), []
        for doc in docs:
            name = safe_name(doc.name, doc.id)
            key = name.casefold()
            if key in seen:
                name = with_suffix(name, doc.id)
                key = name.casefold()
            seen.add(key)
            entries.append((name, doc))
        return entries

    def children(self):
        return [self._make(name, doc) for name, doc in self._entries()]

    def get_child(self, name):
        for entry_name, doc in self._entries():
            if entry_name == name:
                return self._make(entry_name, doc)
        return None

    def _make(self, name, doc):
        if doc.type == 'folder':
            return FolderResource(self.ctx, self.path + (name,), doc, self.scope)
        return FileResource(self.ctx, self.path + (name,), doc, self.scope)

    # -------------------------------------------------------------- writing
    def _create_vals(self):
        if self.folder:
            return {'folder_id': self.folder.id}
        if self.scope in ('COMPANY', 'MY'):
            return {'user_folder_id': self.scope}
        raise DavError(403, "Cannot create items in 'Shared with me'")

    @dav_errors
    def mkcol(self, name):
        vals = dict(self._create_vals(), name=name, type='folder')
        self.env['documents.document'].create(vals)

    @dav_errors
    def create_child(self, name, data, content_type, request_info):
        if JUNK.match(name):
            return None  # OS litter: accepted and discarded
        Doc = self.env['documents.document']
        vals = dict(self._create_vals(), name=name, type='binary', raw=data)
        mime = mimetypes.guess_type(name)[0]
        if mime:
            vals['mimetype'] = mime
        if self.folder and self.folder.alias_tag_ids:
            vals['tag_ids'] = [(6, 0, self.folder.alias_tag_ids.ids)]
        doc = Doc.create(vals)
        if not doc.attachment_id:  # empty files: make sure an attachment exists
            doc.attachment_id = self.env['ir.attachment'].create(
                {'name': name, 'raw': b'', 'mimetype': mime or 'application/octet-stream'})
        return FileResource(self.ctx, self.path + (name,), doc, self.scope)


class DocsRoot(Resource):
    is_collection = True

    def display_name(self):
        return 'Documents'

    def children(self):
        return [self._area(label, scope) for label, scope in AREAS]

    def get_child(self, name):
        for label, scope in AREAS:
            if label == name:
                return self._area(label, scope)
        return None

    def _area(self, label, scope):
        return Area(self.ctx, self.path + (label,), scope)


class Area(DocsCollection):

    def __init__(self, ctx, path, scope):
        super().__init__(ctx, path)
        self.scope = scope

    def display_name(self):
        return self.path[-1]

    def _list_domain(self):
        return [('user_folder_id', 'in', [self.scope])]


class FolderResource(DocsCollection):

    def __init__(self, ctx, path, doc, scope):
        super().__init__(ctx, path)
        self.folder = doc
        self.scope = scope

    def display_name(self):
        return self.folder.name

    def last_modified(self):
        return self.folder.write_date

    def _list_domain(self):
        return [('folder_id', '=', self.folder.id)]

    def prop(self, name):
        if name == q('O', 'tags'):
            return ', '.join(self.folder.tag_ids.mapped('name')) if self.folder.tag_ids else NOT_FOUND
        return super().prop(name)

    # --- write operations on the folder itself
    @dav_errors
    def delete(self):
        self.folder.action_archive()

    @dav_errors
    def move(self, dest_parent, dest_name):
        _move_document(self, dest_parent, dest_name)


class FileResource(Resource):
    writable = True

    def __init__(self, ctx, path, doc, scope):
        super().__init__(ctx, path)
        self.doc = doc
        self.scope = scope

    def display_name(self):
        return self.name

    def etag(self):
        d = self.doc
        return hashlib.sha1(('%s|%s' % (d.id, d.checksum or d.write_date)).encode()).hexdigest()

    def last_modified(self):
        return self.doc.write_date

    def content_type(self):
        if self.doc.type == 'url':
            return 'text/plain; charset=utf-8'
        return self.doc.mimetype or 'application/octet-stream'

    def content_length(self):
        if self.doc.type == 'url':
            return len(self._content())
        return self.doc.file_size or 0

    def prop(self, name):
        if name == q('D', 'creationdate'):
            return self.doc.create_date.strftime('%Y-%m-%dT%H:%M:%SZ') if self.doc.create_date else NOT_FOUND
        if name == q('O', 'tags'):
            return ', '.join(self.doc.tag_ids.mapped('name')) if self.doc.tag_ids else NOT_FOUND
        return super().prop(name)

    def _content(self):
        if self.doc.type == 'url':
            return ('[InternetShortcut]\r\nURL=%s\r\n' % (self.doc.url or '')).encode()
        return self.doc.raw or b''

    def read(self):
        return self._content(), self.content_type()

    # ---------------------------------------------------------------- write
    def _check_lock(self):
        lock = self.doc.lock_uid
        if lock and lock != self.ctx.user:
            raise DavError(423, 'Locked by %s' % lock.name)

    @dav_errors
    def put(self, data, content_type, request_info):
        if self.doc.type == 'url':
            raise DavError(403, 'Link documents are read-only')
        self._check_lock()
        self.doc.write({'raw': data})

    @dav_errors
    def delete(self):
        self._check_lock()
        self.doc.action_archive()

    @dav_errors
    def move(self, dest_parent, dest_name):
        self._check_lock()
        _move_document(self, dest_parent, dest_name)


def _move_document(resource, dest_parent, dest_name):
    doc = resource.folder if resource.is_collection else resource.doc
    # moving re-syncs the sharing rules from the new parent: stay in the same area
    if dest_parent.path[:2] != resource.path[:2]:
        raise DavError(403, 'Moving between Company, My Files and Shared areas is not allowed')
    vals = {}
    if dest_name != resource.name:
        vals['name'] = strip_suffix(dest_name, doc.id)
    dest_folder = getattr(dest_parent, 'folder', None)
    if (dest_folder.id if dest_folder else False) != (doc.folder_id.id or False):
        if dest_parent.scope == 'SHARED' and not dest_folder:
            raise DavError(403, "Cannot move items to the root of 'Shared with me'")
        vals['folder_id'] = dest_folder.id if dest_folder else False
    if vals:
        doc.write(vals)
