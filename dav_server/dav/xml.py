"""Hardened XML helpers for the DAV engine (no entities, no DTD, no network)."""
import functools

from lxml import etree

DAV = 'DAV:'
CARDDAV = 'urn:ietf:params:xml:ns:carddav'
CALDAV = 'urn:ietf:params:xml:ns:caldav'
CS = 'http://calendarserver.org/ns/'
ODOO = 'urn:odoo:dav'
APPLE = 'http://apple.com/ns/ical/'

NSMAP = {'D': DAV, 'A': CARDDAV, 'C': CALDAV, 'CS': CS, 'O': ODOO, 'IC': APPLE}

MAX_XML_BYTES = 1024 * 1024

# qname helper: q('D', 'prop') -> '{DAV:}prop'
_NS = {k: v for k, v in NSMAP.items()}


def q(prefix, local):
    return '{%s}%s' % (_NS[prefix], local)


class XMLError(ValueError):
    pass


def _parser():
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        dtd_validation=False, huge_tree=False, remove_comments=True,
        remove_pis=True, remove_blank_text=True,
    )


def parse(body):
    """Parse a request body. Returns the root element, or None for an empty body.

    Raises XMLError on malformed documents and on any DOCTYPE/ENTITY.
    """
    if body is None or not body.strip():
        return None
    if len(body) > MAX_XML_BYTES:
        raise XMLError('XML body too large')
    lowered = body[:4096].lower()
    if b'<!doctype' in lowered or b'<!entity' in lowered:
        raise XMLError('DTDs are not allowed')
    try:
        tree = etree.fromstring(body, _parser()).getroottree()
    except etree.XMLSyntaxError as exc:
        raise XMLError('Malformed XML') from exc
    if tree.docinfo.doctype or tree.docinfo.internalDTD or tree.docinfo.externalDTD:
        raise XMLError('DTDs are not allowed')
    return tree.getroot()


def el(qname, value=None, **attrs):
    """Build an element. `value` may be a str, an element, or a list of them."""
    node = etree.Element(qname, nsmap=None, **attrs)
    set_value(node, value)
    return node


def set_value(node, value):
    if value is None:
        return node
    if isinstance(value, str):
        node.text = value
    elif isinstance(value, bytes):
        node.text = value.decode()
    elif isinstance(value, etree._Element):
        node.append(value)
    else:
        for item in value:
            if isinstance(item, str):
                node.text = (node.text or '') + item
            else:
                node.append(item)
    return node


def href(path):
    return el(q('D', 'href'), path)


def serialize(root):
    return etree.tostring(root, xml_declaration=True, encoding='utf-8')


def multistatus(responses, sync_token=None):
    """Build a 207 body.

    :param responses: iterable of dicts
        ``{'href': str, 'ok': [(qname, value)], 'missing': [qname], 'status': int|None}``
        If ``status`` is set, the response has no propstat but a bare status
        (used for deleted resources in sync-collection and for errors).
    """
    root = etree.Element(q('D', 'multistatus'), nsmap=NSMAP)
    for resp in responses:
        r = etree.SubElement(root, q('D', 'response'))
        etree.SubElement(r, q('D', 'href')).text = resp['href']
        if resp.get('status'):
            etree.SubElement(r, q('D', 'status')).text = status_line(resp['status'])
            continue
        if resp.get('ok'):
            ps = etree.SubElement(r, q('D', 'propstat'))
            prop = etree.SubElement(ps, q('D', 'prop'))
            for name, value in resp['ok']:
                node = etree.SubElement(prop, name)
                set_value(node, value)
            etree.SubElement(ps, q('D', 'status')).text = status_line(200)
        if resp.get('missing'):
            ps = etree.SubElement(r, q('D', 'propstat'))
            prop = etree.SubElement(ps, q('D', 'prop'))
            for name in resp['missing']:
                etree.SubElement(prop, name)
            etree.SubElement(ps, q('D', 'status')).text = status_line(resp.get('missing_status', 404))
        if not resp.get('ok') and not resp.get('missing'):
            ps = etree.SubElement(r, q('D', 'propstat'))
            etree.SubElement(ps, q('D', 'prop'))
            etree.SubElement(ps, q('D', 'status')).text = status_line(200)
    if sync_token is not None:
        etree.SubElement(root, q('D', 'sync-token')).text = sync_token
    return serialize(root)


def error_body(*precondition_qnames):
    root = etree.Element(q('D', 'error'), nsmap=NSMAP)
    for name in precondition_qnames:
        etree.SubElement(root, name)
    return serialize(root)


_REASONS = {
    200: 'OK', 201: 'Created', 204: 'No Content', 207: 'Multi-Status',
    403: 'Forbidden', 404: 'Not Found', 409: 'Conflict', 412: 'Precondition Failed',
    424: 'Failed Dependency', 507: 'Insufficient Storage',
}


@functools.lru_cache(maxsize=None)
def status_line(code):
    return 'HTTP/1.1 %d %s' % (code, _REASONS.get(code, ''))
