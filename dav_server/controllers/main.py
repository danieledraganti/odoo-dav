import logging
import uuid
from urllib.parse import urlsplit

from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from ..dav import engine, xml
from ..dav.context import DavContext
from ..dav.resource import DavError
from ..dav.xml import q

_logger = logging.getLogger(__name__)
_audit = logging.getLogger('odoo.addons.dav_server.audit')

ALL_METHODS = ['OPTIONS', 'GET', 'HEAD', 'PROPFIND', 'PROPPATCH', 'REPORT',
               'PUT', 'DELETE', 'MKCOL', 'MOVE', 'COPY', 'LOCK', 'UNLOCK']
WRITE_METHODS = {'PUT', 'DELETE', 'MKCOL', 'MOVE', 'COPY', 'PROPPATCH', 'LOCK', 'UNLOCK'}
FORM_TYPES = ('application/x-www-form-urlencoded', 'multipart/form-data')

SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Cache-Control': 'private, no-cache',
    'Content-Security-Policy': "default-src 'none'; sandbox",
    'Referrer-Policy': 'no-referrer',
}


def _max_body(_controller):
    icp = request.env['ir.config_parameter'].sudo()
    try:
        mb = int(icp.get_param('dav_server.max_upload_mb') or 100)
    except ValueError:
        mb = 100
    return max(mb, 1) * 1024 * 1024


class DavController(http.Controller):

    @http.route(['/.well-known/carddav', '/.well-known/caldav', '/.well-known/webdav'],
                type='http', auth='none', methods=ALL_METHODS, csrf=False, save_session=False)
    def well_known(self, **kw):
        return request.redirect('/dav/', code=301, local=True)

    @http.route(['/dav', '/dav/', '/dav/<path:path>'], type='http', auth='dav',
                methods=ALL_METHODS, csrf=False, save_session=False,
                max_content_length=_max_body)
    def dav(self, path='', **kw):
        httpreq = request.httprequest
        method = httpreq.method
        ctx = DavContext(request.env)
        status = 500
        try:
            response = self._handle(ctx, method, path)
        except DavError as exc:
            response = self._error(exc)
        except AccessError as exc:
            _logger.info('DAV access denied: %s', exc)
            response = self._error(DavError(403, 'Forbidden'))
        except MissingError:
            response = self._error(DavError(404, 'Not found'))
        except (UserError, ValidationError) as exc:
            response = self._error(DavError(422, str(exc)))
        status = response.status_code
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if method in WRITE_METHODS:
            _audit.info('DAV %s /dav/%s user=%s status=%s', method, path, ctx.login, status)
        return response

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _error(exc):
        if exc.preconditions:
            body, ctype = xml.error_body(*exc.preconditions), 'application/xml; charset=utf-8'
        else:
            body, ctype = (exc.message or '').encode(), 'text/plain; charset=utf-8'
        headers = {'Content-Type': ctype}
        if exc.status == 405:
            headers['Allow'] = 'OPTIONS, GET, HEAD, PROPFIND, REPORT'
        return request.make_response(body, headers=list(headers.items()), status=exc.status)

    @staticmethod
    def _segments(path):
        segments = [s for s in path.split('/') if s]
        for seg in segments:
            if seg in ('.', '..') or '\x00' in seg or len(seg) > 255:
                raise DavError(400, 'Invalid path')
        return segments

    @staticmethod
    def _body(limit=xml.MAX_XML_BYTES):
        httpreq = request.httprequest
        if httpreq.mimetype in FORM_TYPES:
            raise DavError(415, 'Unsupported content type')
        data = httpreq.get_data()
        if len(data) > limit:
            raise DavError(413, 'Body too large')
        return data

    @staticmethod
    def _multistatus(body):
        return request.make_response(
            body, headers=[('Content-Type', 'application/xml; charset=utf-8')], status=207)

    @staticmethod
    def _check_preconditions(resource):
        headers = request.httprequest.headers
        if_match, if_none = headers.get('If-Match'), headers.get('If-None-Match')
        etag = resource.etag() if resource is not None else None
        quoted = '"%s"' % etag if etag else None
        if if_none and if_none.strip() == '*' and resource is not None:
            raise DavError(412, 'Resource already exists')
        if if_match:
            tags = [t.strip() for t in if_match.split(',')]
            if resource is None or ('*' not in tags and (quoted is None or quoted not in tags
                                                          and 'W/' + quoted not in tags)):
                raise DavError(412, 'ETag mismatch')

    # ------------------------------------------------------------ dispatch
    def _handle(self, ctx, method, path):
        if method in WRITE_METHODS and not ctx.can_write:
            raise DavError(403, 'Read-only DAV access')
        segments = self._segments(path)
        resource = ctx.resolve(segments)
        dav_header = ('DAV', ctx.features_header)

        if method == 'OPTIONS':
            allow = ['OPTIONS', 'GET', 'HEAD', 'PROPFIND', 'REPORT']
            if ctx.can_write and (resource is None or resource.writable):
                allow += ['PUT', 'DELETE', 'MKCOL', 'MOVE', 'PROPPATCH']
            return request.make_response(b'', headers=[dav_header, ('Allow', ', '.join(allow)),
                                                       ('Content-Length', '0')])

        if method == 'PROPFIND':
            if resource is None:
                raise DavError(404, 'Not found')
            depth = request.httprequest.headers.get('Depth', '0').strip().lower()
            if depth == 'infinity':
                raise DavError(403, 'Depth: infinity is not supported', (q('D', 'propfind-finite-depth'),))
            if depth not in ('0', '1'):
                raise DavError(400, 'Invalid Depth')
            return self._multistatus(engine.propfind(resource, depth, self._body()))

        if method == 'REPORT':
            if resource is None:
                raise DavError(404, 'Not found')
            return self._multistatus(engine.report(ctx, resource, self._body()))

        if method in ('GET', 'HEAD'):
            if resource is None:
                raise DavError(404, 'Not found')
            if resource.is_collection:
                raise DavError(405, 'Collections cannot be fetched')
            data, ctype = resource.read()
            headers = [('Content-Type', ctype), ('Content-Length', str(len(data)))]
            if resource.etag():
                headers.append(('ETag', '"%s"' % resource.etag()))
            modified = resource.prop(q('D', 'getlastmodified'))
            if isinstance(modified, str):
                headers.append(('Last-Modified', modified))
            headers.append(('Content-Disposition', 'attachment'))
            return request.make_response(data, headers=headers)

        if method == 'PROPPATCH':
            if resource is None:
                raise DavError(404, 'Not found')
            root = engine._parse_or_400(self._body())
            names = [c.tag for act in (root if root is not None else []) for p in act for c in p
                     if isinstance(c.tag, str)]
            return self._multistatus(xml.multistatus([{
                'href': resource.href, 'ok': [], 'missing': names, 'missing_status': 403}]))

        if method == 'PUT':
            return self._put(ctx, segments, resource)

        if method == 'DELETE':
            if resource is None:
                raise DavError(404, 'Not found')
            self._check_preconditions(resource)
            with request.env.cr.savepoint():
                resource.delete()
            return request.make_response(b'', status=204)

        if method == 'MKCOL':
            if resource is not None:
                raise DavError(405, 'Already exists')
            parent = ctx.resolve(segments[:-1]) if segments else None
            if parent is None or not parent.is_collection:
                raise DavError(409, 'Parent does not exist')
            with request.env.cr.savepoint():
                parent.mkcol(segments[-1])
            return request.make_response(b'', status=201)

        if method == 'MOVE':
            return self._move(ctx, segments, resource)

        if method == 'LOCK' and '2' in ctx.features:
            return self._lock(ctx, segments, resource)
        if method == 'UNLOCK' and '2' in ctx.features:
            return request.make_response(b'', status=204)
        raise DavError(405, 'Method not allowed')

    def _lock(self, ctx, segments, resource):
        """Stateless 'courtesy' lock: lets clients that insist on LOCK (macOS
        Finder, Office) write. Real conflicts are guarded by `lock_uid`
        checks and ETags in the resources."""
        created = resource is None
        if created:
            parent = ctx.resolve(segments[:-1]) if segments else None
            if parent is None or not parent.is_collection:
                raise DavError(409, 'Parent does not exist')
        token = 'opaquelocktoken:%s' % uuid.uuid4()
        D = lambda n: q('D', n)  # noqa: E731
        active = xml.el(D('activelock'), [
            xml.el(D('locktype'), xml.el(D('write'))),
            xml.el(D('lockscope'), xml.el(D('exclusive'))),
            xml.el(D('depth'), '0'),
            xml.el(D('owner'), xml.el(D('href'), ctx.login)),
            xml.el(D('timeout'), 'Second-3600'),
            xml.el(D('locktoken'), xml.el(D('href'), token)),
        ])
        body = xml.el(D('prop'), xml.el(D('lockdiscovery'), active))
        return request.make_response(
            xml.serialize(body),
            headers=[('Content-Type', 'application/xml; charset=utf-8'), ('Lock-Token', '<%s>' % token)],
            status=201 if created else 200)

    def _put(self, ctx, segments, resource):
        if not segments:
            raise DavError(405, 'Cannot write here')
        if resource is not None and resource.is_collection:
            raise DavError(405, 'Cannot PUT on a collection')
        self._check_preconditions(resource)
        data = self._body(limit=_max_body(None))
        ctype = request.httprequest.mimetype or 'application/octet-stream'
        if resource is not None:
            with request.env.cr.savepoint():
                resource.put(data, ctype, {})
            updated = ctx.resolve(segments)
            status, target = 204, updated
        else:
            parent = ctx.resolve(segments[:-1])
            if parent is None or not parent.is_collection:
                raise DavError(409, 'Parent does not exist')
            with request.env.cr.savepoint():
                target = parent.create_child(segments[-1], data, ctype, {})
            status = 201
        headers = []
        if target is not None and target.etag():
            headers.append(('ETag', '"%s"' % target.etag()))
        return request.make_response(b'', headers=headers, status=status)

    def _move(self, ctx, segments, resource):
        if resource is None:
            raise DavError(404, 'Not found')
        dest = request.httprequest.headers.get('Destination')
        if not dest:
            raise DavError(400, 'Missing Destination')
        parts = urlsplit(dest)
        if parts.netloc and parts.netloc != request.httprequest.host:
            raise DavError(502, 'Cross-server MOVE is not supported')
        dest_segments = ctx.href_to_segments(dest)
        if not dest_segments:
            raise DavError(400, 'Invalid Destination')
        existing = ctx.resolve(dest_segments)
        if existing is not None and request.httprequest.headers.get('Overwrite', 'T').upper() == 'F':
            raise DavError(412, 'Destination exists')
        parent = ctx.resolve(dest_segments[:-1])
        if parent is None or not parent.is_collection:
            raise DavError(409, 'Destination parent does not exist')
        with request.env.cr.savepoint():
            if existing is not None:
                existing.delete()
            resource.move(parent, dest_segments[-1])
        return request.make_response(b'', status=204 if existing is not None else 201)
