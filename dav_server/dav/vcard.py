"""vCard 3.0 <-> partner values. Pure functions, no ORM writes here."""
import base64
import hashlib
import io
import re

import vobject
from vobject.base import ParseError

from odoo.tools import html2plaintext

MAX_VCARD_BYTES = 4 * 1024 * 1024
MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_PHOTO_PIXELS = 16 * 1024 * 1024
MAX_TEXT_CHARS = 1024  # indexed text columns (name, email...) overflow PostgreSQL btree rows above ~2.7 KB

# TEL/EMAIL types that are compatible with the single Odoo `phone`/`email`
_PLAIN_TYPES = {'WORK', 'VOICE', 'PREF', 'INTERNET', 'X-INTERNET', ''}


class VCardError(ValueError):
    pass


def params_to_str(child):
    parts = []
    for key, values in child.params.items():
        if key.upper() in ('ENCODING', 'VALUE'):
            continue
        parts.append('%s=%s' % (key.upper(), ','.join(values)))
    return ';'.join(parts)


def str_to_params(text):
    params = {}
    for part in (text or '').split(';'):
        if '=' in part:
            key, _eq, val = part.partition('=')
            params[key.upper()] = [v for v in val.split(',') if v]
    return params


def _types(child):
    return {t.upper() for t in child.params.get('TYPE', [])}


def _clean(value):
    return (value or '').strip()


def split_name(name):
    parts = (name or '').split()
    if len(parts) <= 1:
        return '', name or ''
    return ' '.join(parts[:-1]), parts[-1]


def photo_bytes(partner):
    data = partner.image_256
    return base64.b64decode(data) if data else b''


def stable_uid(partner, db_uuid):
    return 'odoo-%s-partner-%d' % (db_uuid, partner.id)


# ------------------------------------------------------------------ build

def build(partner, extra, db_uuid, photos=True):
    """Serialize a partner as a vCard 3.0 string."""
    card = vobject.vCard()
    name = partner.name or partner.display_name or ''
    given, family = ('', name) if partner.is_company else split_name(name)

    card.add('n').value = vobject.vcard.Name(family=family, given=given)
    card.add('fn').value = name
    card.add('uid').value = (extra.uid if extra and extra.uid else stable_uid(partner, db_uuid))

    org = name if partner.is_company else (partner.parent_id.name or partner.company_name)
    extra_lines = (extra.lines or '') if extra else ''
    kept_org = _extra_org_first(extra_lines)
    if org and not (kept_org and kept_org.lower() == org.lower()):
        # no multi-component ORG from the client that still matches: generate ours
        extra_lines = _strip_org(extra_lines)
        card.add('org').value = [org]
    elif not org:
        extra_lines = _strip_org(extra_lines)
    if partner.is_company:
        card.add('x-abshowas').value = 'COMPANY'
    if partner.function:
        card.add('title').value = partner.function
    if partner.email:
        child = card.add('email')
        child.value = partner.email
        child.params = str_to_params(extra.email_params) if extra and extra.email_params else {'TYPE': ['INTERNET', 'WORK']}
    if partner.phone:
        child = card.add('tel')
        child.value = partner.phone
        child.params = str_to_params(extra.phone_params) if extra and extra.phone_params else {'TYPE': ['WORK', 'VOICE']}
    if any([partner.street, partner.street2, partner.city, partner.zip, partner.state_id, partner.country_id]):
        child = card.add('adr')
        child.value = vobject.vcard.Address(
            street=partner.street or '', extended=partner.street2 or '', city=partner.city or '',
            region=partner.state_id.name or '', code=partner.zip or '', country=partner.country_id.name or '')
        child.params = str_to_params(extra.adr_params) if extra and extra.adr_params else {'TYPE': ['WORK']}
    if partner.website:
        card.add('url').value = partner.website
    note = html2plaintext(partner.comment or '').strip()
    if note:
        card.add('note').value = note
    categories = partner.category_id.mapped('display_name')
    if categories:
        card.add('categories').value = categories
    if photos:
        data = photo_bytes(partner)
        if data:
            child = card.add('photo')
            child.value = data
            child.encoding_param = 'B'
            child.type_param = 'PNG' if data.startswith(b'\x89PNG') else 'JPEG'
    if partner.write_date:
        card.add('rev').value = partner.write_date.strftime('%Y-%m-%dT%H:%M:%SZ')

    text = card.serialize()
    if extra_lines:
        if not extra_lines.endswith('\r\n'):
            extra_lines += '\r\n'
        text = text.replace('END:VCARD', extra_lines + 'END:VCARD', 1)
    return text


def _strip_org(lines):
    return re.sub(r'^ORG[:;][^\r\n]*(?:\r\n[ \t][^\r\n]*)*\r\n?', '', lines, flags=re.M)


def _extra_org_first(lines):
    match = re.search(r'^ORG[^:]*:([^;\r\n]*)', lines, re.M)
    return match.group(1).strip() if match else ''


# ------------------------------------------------------------------ parse

class Parsed:
    def __init__(self):
        self.uid = None
        self.fn = ''
        self.is_company = False
        self.org = ''
        self.title = ''
        self.email = self.email_params = self.email_raw = None
        self.phone = self.phone_params = self.phone_raw = None
        self.adr = None
        self.adr_params = self.adr_raw = None
        self.url = ''
        self.note = None
        self.categories = []
        self.photo = None           # raw bytes if a usable inline photo is present
        self.extra = []             # raw serialized lines kept verbatim


def parse(text):
    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise VCardError('vCard must be UTF-8') from exc
    if len(text) > MAX_VCARD_BYTES:
        raise VCardError('vCard too large')
    try:
        card = vobject.readOne(text)
    except (ParseError, StopIteration, ValueError, AttributeError) as exc:
        raise VCardError('Invalid vCard') from exc
    if card.name.upper() != 'VCARD':
        raise VCardError('Not a vCard')

    out = Parsed()
    tels, emails, adrs = [], [], []
    for child in card.getChildren():
        name = child.name.upper()
        if name in ('VERSION', 'PRODID', 'REV', 'N'):
            continue
        if name == 'FN':
            out.fn = _clean(child.value)
        elif name == 'UID':
            out.uid = _clean(child.value)[:255] or None
        elif name == 'X-ABSHOWAS':
            out.is_company = _clean(child.value).upper() == 'COMPANY'
        elif name == 'ORG' and not out.org:
            comps = child.value if isinstance(child.value, list) else [child.value]
            out.org = _clean(comps[0] if comps else '')
            if len([c for c in comps if c]) > 1:
                out.extra.append(child.serialize())
        elif name == 'TITLE':
            out.title = _clean(child.value)
        elif name == 'EMAIL':
            emails.append(child)
        elif name == 'TEL':
            tels.append(child)
        elif name == 'ADR':
            adrs.append(child)
        elif name == 'URL' and not out.url:
            out.url = _clean(child.value)
        elif name == 'NOTE' and out.note is None:
            out.note = _clean(child.value)
        elif name == 'CATEGORIES':
            vals = child.value if isinstance(child.value, list) else [child.value]
            out.categories += [_clean(v) for v in vals if _clean(v)]
        elif name == 'PHOTO' and out.photo is None and isinstance(child.value, bytes) \
                and len(child.value) <= MAX_PHOTO_BYTES:
            out.photo = child.value
        else:
            out.extra.append(child.serialize())

    def pick(children, plain_only):
        chosen = None
        for child in children:
            if not plain_only or _types(child) <= _PLAIN_TYPES:
                chosen = child
                break
        return chosen or (children[0] if children else None)

    primary_email = pick(emails, False)
    primary_tel = pick(tels, True)
    primary_adr = adrs[0] if adrs else None
    for children, primary in ((emails, primary_email), (tels, primary_tel), (adrs, primary_adr)):
        for child in children:
            if child is not primary:
                out.extra.append(child.serialize())
    if primary_email is not None:
        out.email = _clean(primary_email.value)
        out.email_params = params_to_str(primary_email)
        out.email_raw = primary_email.serialize()
    if primary_tel is not None:
        out.phone = _clean(primary_tel.value)
        out.phone_params = params_to_str(primary_tel)
        out.phone_raw = primary_tel.serialize()
    if primary_adr is not None and hasattr(primary_adr.value, 'street'):
        a = primary_adr.value
        out.adr = {
            'street': _clean(a.street), 'street2': _clean(a.extended), 'city': _clean(a.city),
            'state': _clean(a.region), 'zip': _clean(a.code), 'country': _clean(a.country),
        }
        out.adr_params = params_to_str(primary_adr)
        out.adr_raw = primary_adr.serialize()
    if not out.fn:
        raise VCardError('FN is required')
    return out


def clean_photo(data):
    """Validate and re-encode an incoming photo. Returns base64 bytes or None."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PHOTO_PIXELS:
            return None
        img.load()
        img = img.convert('RGB')
        img.thumbnail((1920, 1920))
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=85)
    except Exception:  # noqa: BLE001 - any decoding problem means "ignore the photo"
        return None
    return base64.b64encode(buf.getvalue())


def etag_of(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode())
        h.update(b'\x00')
    return h.hexdigest()
