# Odoo DAV server (Odoo 19)

CardDAV / WebDAV / CalDAV access to Odoo, authenticated with standard Odoo API keys.

| Module | Needs | Exposes | Write |
|---|---|---|---|
| `dav_server` | `contacts` (Community) | auth, DAV engine, **contacts** (CardDAV, vCard 3.0, photos) | yes |
| `dav_server_documents` | `documents` (Enterprise) | Documents as files/folders (WebDAV) | yes |
| `dav_server_tasks` | `project` | tasks assigned to the user (CalDAV VTODO) | yes |
| `dav_server_knowledge` | `knowledge` (Enterprise) | articles as HTML files | **no, ever** |
| `dav_server_calendar` | `calendar` | events (CalDAV VEVENT) | non-recurring, organiser only |

Python deps: `vobject`, `lxml`, `Pillow` (all already Odoo dependencies).

## Security model
- **Credentials**: HTTP Basic (`login:api_key`) or Bearer, using keys created in *Preferences → Account Security → API Keys*. Standard Odoo expiry rules apply. Sessions/cookies are never accepted. Failures feed Odoo's login cooldown.
- **Roles** (Settings → Users → DAV): `User` = read-only, `Administrator` = may write. A key of a user outside the group gets 403.
- **No sudo**: every read/write runs as the authenticated user; ACLs, record rules, multi-company and Documents permissions apply exactly as in the UI. Invisible records are 404.
- **TLS**: requests over plain HTTP are refused unless *Allow insecure DAV* is set (dev only). Behind a proxy run Odoo with `proxy_mode`.
- A standard API key is global: it also works on `/json/2` and XML-RPC. Use short expiries.
- Hardened XML parsing (no DTD/entities), `Depth: infinity` refused, body-size caps, path normalisation, `nosniff`/CSP headers, write audit log (`odoo.addons.dav_server.audit`).

## URLs
`/.well-known/carddav|caldav|webdav` → `/dav/`. Principal `/dav/principals/<login>/`, address book `/dav/addressbooks/<login>/contacts/`, calendars `/dav/calendars/<login>/{tasks,events}/`, files `/dav/files/`, knowledge `/dav/knowledge/`.

## Data-integrity rules (contacts)
- Companies, and non-`contact` addresses, are read-only; DELETE = archive (refused for users' partners and partners with children).
- `ORG` is matched (trim, case-insensitive, exact) against visible companies: exactly one match → `parent_id`; otherwise stored in `company_name`. Ambiguity never links; an existing link is never removed by DAV. A standalone contact with its own address/commercial data is not re-linked (Odoo would overwrite it).
- Contacts attached to a company do not write address fields (Odoo propagates them to the company); commercial fields are never written.
- Unmapped vCard lines (2nd phone/e-mail, IM, birthday, X-props...) are stored verbatim in `dav.vcard.extra` and re-emitted. `CATEGORIES`/`NOTE`/`PHOTO` are only touched when the client sends them.

## Other write rules
- Documents: DELETE = Odoo trash; moves stay inside the same area (Company / My Files / Shared); sibling-name clashes get ` (id)`; OS litter (`.DS_Store`, `._*`, `~$*`...) is discarded; `lock_uid` held by someone else → 423; LOCK is a stateless courtesy lock (needed by Finder/Office).
- Tasks: VTODO of tasks where the user is assignee (open + closed in the last 90 days); new VTODOs become personal tasks; only done/cancelled ↔ open transitions are written to `state`.
- Calendar: recurring events are exposed as plain occurrences and are read-only; attendees are never changed; creation sends no invitations.

## Known gaps
`*-query` filters are not evaluated (full list returned), no `Range`, `COPY`, `If:` header, PROPPATCH (always 403), custom-field (`x_*`/Properties) mapping to vCard `X-` props, tag editing over DAV.

## Status
Written against the Odoo 19 source but **not yet executed**: no running Odoo was available. Run `odoo -i dav_server,... --test-tags /dav_server`.
