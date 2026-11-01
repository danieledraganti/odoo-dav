"""Read-only WebDAV folders of per-record files (typically the PDF of a document)."""
import datetime
import re

from odoo import fields

from . import vcard
from .resource import DavError, Resource

_ID_RE = re.compile(r'\[(\d+)\]\.[A-Za-z0-9]{1,8}$')
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class RecordFolder(Resource):
    """One file per record of `model`; nothing can be written or deleted."""
    is_collection = True

    model = None
    display = 'Files'
    extension = 'pdf'
    mimetype = 'application/pdf'
    window_param = 'reports_window_days'

    # ----------------------------------------------- to implement in children
    def scope_domain(self):
        raise NotImplementedError

    def record_label(self, record):
        return record.display_name

    def content(self, record):
        """Return the bytes of the file, or raise DavError(404)."""
        raise NotImplementedError

    def size(self, record):
        """Size in bytes when known without rendering the file, else None."""
        return None

    def etag_parts(self, record):
        return (record.id, record.write_date)

    # ------------------------------------------------------------------ helpers
    @property
    def env(self):
        return self.ctx.env

    def window_start(self):
        days = self.ctx.param_int(self.window_param, 365)
        return fields.Datetime.now() - datetime.timedelta(days=max(days, 1))

    def file_name(self, record):
        label = _UNSAFE.sub('_', self.record_label(record) or 'document').strip(' .')[:100]
        return '%s [%d].%s' % (label or 'document', record.id, self.extension)

    def display_name(self):
        return self.display

    def children(self):
        limit = self.ctx.param_int('max_reports', 5000)
        records = self.env[self.model].search(self.scope_domain(), limit=limit, order='id desc')
        return [RecordFile(self.ctx, self.path + (self.file_name(r),), r, self) for r in records]

    def get_child(self, name):
        match = _ID_RE.search(name)
        if not match:
            return None
        record = self.env[self.model].search(
            self.scope_domain() + [('id', '=', int(match.group(1)))], limit=1)
        if not record or self.file_name(record) != name:
            return None
        return RecordFile(self.ctx, self.path + (name,), record, self)


class RecordFile(Resource):

    def __init__(self, ctx, path, record, folder):
        super().__init__(ctx, path)
        self.record = record
        self.folder = folder

    def etag(self):
        return vcard.etag_of(*self.folder.etag_parts(self.record))

    def last_modified(self):
        return self.record.write_date

    def content_type(self):
        return self.folder.mimetype

    def content_length(self):
        return self.folder.size(self.record)

    def read(self):
        data = self.folder.content(self.record)
        if not data:
            raise DavError(404, 'No document')
        return data, self.folder.mimetype
