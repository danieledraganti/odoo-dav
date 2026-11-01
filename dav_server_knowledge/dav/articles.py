"""Read-only WebDAV view of Knowledge articles.

An article without children is ``<title>.html``; an article with children is a
folder ``<title>/`` holding ``index.html`` (its own body) plus its children.
Writing is intentionally not supported: article bodies are edited by the
collaborative editor and cannot be round-tripped through plain files.
"""
import hashlib
import html
import re

from odoo.tools.translate import LazyTranslate

from odoo.addons.dav_server.dav.resource import DavError, Resource
from odoo.addons.dav_server.dav.xml import q

BASE_DOMAIN = [('is_template', '=', False)]
INDEX = 'index.html'


def safe_name(name, article_id):
    cleaned = re.sub(r'[\x00-\x1f/\\]', '_', (name or '').strip()).strip('.')
    return cleaned or 'untitled-%d' % article_id


def render_html(article):
    title = html.escape(article.name or '')
    body = article.body or ''
    return ('<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>%s</title></head>'
            '<body><h1>%s</h1>\n%s\n</body></html>' % (title, title, body)).encode('utf-8')


_lt = LazyTranslate(__name__)


class ArticleList(Resource):
    """Shared listing logic: a collection of articles under `parent` (or roots)."""
    is_collection = True

    def _parent_domain(self):
        raise NotImplementedError

    def _entries(self):
        articles = self.ctx.env['knowledge.article'].search(
            BASE_DOMAIN + self._parent_domain(), order='sequence, id')
        seen, out = set(), []
        for art in articles:
            name = safe_name(art.name, art.id)
            if art.child_ids.filtered(lambda c: not c.is_template):
                key = name.casefold()
            else:
                key = (name + '.html').casefold()
            if key in seen:
                name = '%s (%d)' % (name, art.id)
                key = name.casefold() if art.child_ids else (name + '.html').casefold()
            seen.add(key)
            out.append((name, art))
        return out

    def children(self):
        return [self._make(n, a) for n, a in self._entries()]

    def get_child(self, name):
        for entry_name, art in self._entries():
            if name in (entry_name, entry_name + '.html'):
                res = self._make(entry_name, art)
                if res.name == name:
                    return res
        return None

    def _make(self, name, art):
        if art.child_ids:
            return ArticleFolder(self.ctx, self.path + (name,), art)
        return ArticleFile(self.ctx, self.path + (name + '.html',), art)


class KnowledgeRoot(ArticleList):
    def display_name(self):
        return self.tr(_lt('Knowledge'))

    def _parent_domain(self):
        return [('parent_id', '=', False)]


class ArticleFolder(ArticleList):
    def __init__(self, ctx, path, article):
        super().__init__(ctx, path)
        self.article = article

    def display_name(self):
        return self.article.name

    def last_modified(self):
        return self.article.write_date

    def _parent_domain(self):
        return [('parent_id', '=', self.article.id)]

    def children(self):
        return [ArticleFile(self.ctx, self.path + (INDEX,), self.article)] + super().children()

    def get_child(self, name):
        if name == INDEX:
            return ArticleFile(self.ctx, self.path + (INDEX,), self.article)
        return super().get_child(name)


class ArticleFile(Resource):

    def __init__(self, ctx, path, article):
        super().__init__(ctx, path)
        self.article = article

    def etag(self):
        a = self.article
        return hashlib.sha1(('%s|%s|%s' % (a.id, a.write_date, a.name)).encode()).hexdigest()

    def last_modified(self):
        return self.article.write_date

    def content_type(self):
        return 'text/html; charset=utf-8'

    def content_length(self):
        return None

    def read(self):
        return render_html(self.article), self.content_type()
