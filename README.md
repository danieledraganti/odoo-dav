# Odoo DAV server (Odoo 19)

CardDAV / WebDAV / CalDAV access to Odoo, authenticated with standard Odoo API keys.

| Module | Needs | Exposes | Write |
|---|---|---|---|
| `dav_server` | `contacts` (Community) | auth, DAV engine, **contacts** (CardDAV, vCard 3.0, photos) | yes |
| `dav_server_documents` | `documents` (Enterprise) | Documents as files/folders (WebDAV) | yes |
| `dav_server_tasks` | `project` | tasks assigned to the user (CalDAV VTODO, includes personal to-dos of `project_todo`) and project milestones (VEVENT) | tasks yes, milestones no |
| `dav_server_knowledge` | `knowledge` (Enterprise) | articles as HTML files | **no, ever** |
| `dav_server_calendar` | `calendar` | events (CalDAV VEVENT; interviews and meetings are plain events) | non-recurring, organiser only |
| `dav_server_timeoff` | `hr_holidays` | time off (VEVENT) | **no, ever** |
| `dav_server_event` | `event` | Odoo events you run or are registered to (VEVENT) | **no, ever** |
| `dav_server_activities` | `mail` | activities assigned to you: to-dos, calls, CRM follow-ups (VTODO) | edit, complete, cancel; no create |
| `dav_server_sales` | `sale` | PDF of quotations and orders (WebDAV files) | **no, ever** |
| `dav_server_invoices` | `account` | PDF of posted customer invoices and credit notes (WebDAV files) | **no, ever** |
| `dav_server_employees` | `hr` | employee directory with work contact data (second CardDAV address book) | **no, ever** |

Python deps: `vobject`, `lxml`, `Pillow` (all already Odoo dependencies).

## Security model
- **Credentials**: HTTP Basic (`login:api_key`) or Bearer, using keys created in *Preferences → Account Security → API Keys*. Standard Odoo expiry rules apply. Sessions/cookies are never accepted. Failures feed Odoo's login cooldown.
- **Roles** (Settings → Users → DAV): `User` = read-only, `Administrator` = may write. A key of a user outside the group gets 403.
- **No sudo**: every read/write runs as the authenticated user; ACLs, record rules, multi-company and Documents permissions apply exactly as in the UI. Invisible records are 404.
- **TLS**: requests over plain HTTP are refused unless *Allow insecure DAV* is set (dev only). Behind a proxy run Odoo with `proxy_mode`.
- A standard API key is global: it also works on `/json/2` and XML-RPC. Use short expiries.
- Hardened XML parsing (no DTD/entities), `Depth: infinity` refused, body-size caps, path normalisation, `nosniff`/CSP headers, write audit log (`odoo.addons.dav_server.audit`).

## URLs
`/.well-known/carddav|caldav|webdav` → `/dav/`. Principal `/dav/principals/<login>/`, address books `/dav/addressbooks/<login>/{contacts,employees}/`, calendars `/dav/calendars/<login>/{tasks,milestones,events,timeoff,odoo-events,activities}/`, files `/dav/Files/`, knowledge `/dav/Knowledge/`, PDFs `/dav/Reports/{Sales,Invoices}/`. Fixed names are matched ignoring case: a request with another spelling (e.g. the former `/dav/files/`) is redirected (308) to the real one. A collection is only mounted when the user may read its model.

## Data-integrity rules (contacts)
- Companies, and non-`contact` addresses, are read-only; DELETE = archive (refused for users' partners and partners with children).
- `ORG` is matched (trim, case-insensitive, exact) against visible companies: exactly one match → `parent_id`; otherwise stored in `company_name`. Ambiguity never links; an existing link is never removed by DAV. A standalone contact with its own address/commercial data is not re-linked (Odoo would overwrite it).
- Contacts attached to a company do not write address fields (Odoo propagates them to the company); commercial fields are never written.
- Unmapped vCard lines (2nd phone/e-mail, IM, birthday, X-props...) are stored verbatim in `dav.vcard.extra` and re-emitted. `CATEGORIES`/`NOTE`/`PHOTO` are only touched when the client sends them.

## Other write rules
- Documents: DELETE = Odoo trash; moves stay inside the same area (Company / My Files / Shared); sibling-name clashes get ` (id)`; OS litter (`.DS_Store`, `._*`, `~$*`...) is discarded; `lock_uid` held by someone else → 423; LOCK is a stateless courtesy lock (needed by Finder/Office).
- Tasks: VTODO of tasks where the user is assignee (open + closed in the last 90 days); new VTODOs become personal tasks; only done/cancelled ↔ open transitions are written to `state`.
- Calendar: recurring events are exposed as plain occurrences and are read-only; attendees are never changed; creation sends no invitations.

## Language
Everything the client shows (collection names, descriptions, and Odoo data such as tags, activity types and project names) is in the language of the authenticated user (*Preferences → Language*). Collection names are translated through the usual Odoo code translations: add `i18n/<lang>.po` to the addon (Italian is included). URLs never change with the language, and the folder names inside `Files/` (`Company`, `My Files`, `Shared with me`) stay as they are, because file clients such as Finder show the URL, not a display name.

## Read-only feeds and activities
- Time off, Odoo events and milestones are read-only feeds; records that leave the feed (refused leave, cancelled, deleted) are reported as gone by `sync-collection`. In the time-off feed the leave type is only shown to the person concerned and to time-off officers.
- Activities: editing the summary, note and due date is written back; `STATUS:COMPLETED` marks the activity done (Odoo archives it); DELETE cancels it. Activities reassigned to someone else disappear at the next full resync.
- PDF folders: files are named `<number> [<id>].pdf`; the PDF is rendered on download (sale orders) or the stored one is served (posted invoices). Window and cap: `dav_server.reports_window_days` (365), `dav_server.max_reports` (5000).
- Employees: visibility follows the *Employee directory* (`hr.employee.public`); only the work fields are read from the employee record (name, work e-mail/phones, job title, company, department, photo).

## Known gaps
`*-query` filters are not evaluated (full list returned), `Range` on GET is limited to a single byte range, no `COPY`, `If:` header, PROPPATCH (always 403), custom-field (`x_*`/Properties) mapping to vCard `X-` props, tag editing over DAV.

## Status
Written against the Odoo 19 source but **not yet executed**: no running Odoo was available. Run `odoo -i dav_server,... --test-tags /dav_server`.
