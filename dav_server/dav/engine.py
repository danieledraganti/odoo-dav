"""PROPFIND / REPORT handling shared by all resource types."""
from . import xml
from .resource import NOT_FOUND, DavError
from .xml import q

D = lambda n: q('D', n)  # noqa: E731


def _parse_or_400(body):
    try:
        return xml.parse(body)
    except xml.XMLError as exc:
        raise DavError(400, str(exc))


def requested_props(prop_el):
    return [child.tag for child in prop_el if isinstance(child.tag, str)] if prop_el is not None else []


def render(resource, names=None, propname=False):
    """Build the multistatus entry of `resource` for the given property names."""
    ok, missing = [], []
    if names is None:
        names = resource.allprop_names()
    for name in names:
        if propname:
            ok.append((name, None))
            continue
        value = resource.prop(name)
        if value is NOT_FOUND:
            missing.append(name)
        else:
            ok.append((name, value))
    return {'href': resource.href, 'ok': ok, 'missing': missing}


def propfind(resource, depth, body):
    root = _parse_or_400(body)
    names, propname = None, False  # None -> allprop
    if root is not None:
        if root.tag != D('propfind'):
            raise DavError(400, 'Expected DAV:propfind')
        kids = [c.tag for c in root if isinstance(c.tag, str)]
        if D('propname') in kids:
            propname = True
        elif D('prop') in kids:
            names = requested_props(root.find(D('prop')))
    targets = [resource]
    if depth == '1' and resource.is_collection:
        targets += resource.children()
    return xml.multistatus([render(r, names, propname) for r in targets])


# ---------------------------------------------------------------- reports

def report(ctx, resource, body):
    root = _parse_or_400(body)
    if root is None:
        raise DavError(400, 'Missing REPORT body')
    tag = root.tag
    if tag not in resource.reports:
        raise DavError(403, 'Unsupported report', (D('supported-report'),))
    prop_el = root.find(D('prop'))
    names = requested_props(prop_el) if prop_el is not None else None

    if tag == D('sync-collection'):
        token_el = root.find(D('sync-token'))
        token = (token_el.text or '').strip() if token_el is not None else ''
        changed, deleted, new_token = resource.sync(token)
        responses = [render(r, names) for r in changed]
        responses += [{'href': h, 'status': 404} for h in deleted]
        return xml.multistatus(responses, sync_token=new_token)

    if tag.endswith('-multiget'):
        responses = []
        for href_el in root.findall(D('href')):
            segments = ctx.href_to_segments((href_el.text or '').strip())
            target = ctx.resolve(segments) if segments is not None else None
            # a multiget may only reach resources inside the REPORT target
            if (target is None or target.is_collection
                    or target.path[:len(resource.path)] != resource.path):
                responses.append({'href': (href_el.text or '').strip(), 'status': 404})
            else:
                responses.append(render(target, names))
        return xml.multistatus(responses)

    # *-query: return every member, clients filter (superset is allowed
    # by the protocols for the filters we do not evaluate)
    return xml.multistatus([render(r, names) for r in resource.query(root)])
