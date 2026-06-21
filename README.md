# crustula
RESTful cookie jar

## Install

`pip install -r requirements.txt`

## Run

FastAPI CLI

or

`uvicorn --port 28778 crustula.main:app`

## Migrate

After upgrading, bring an existing `crustula.db` up to date (adds the `account` table and the
new `jar.headers` / `jar.account_id` columns). Required for any DB created before these changes;
safe to run repeatedly:

```
python -m crustula.migrate                # migrates ./crustula.db
python -m crustula.migrate path/to.db     # a specific file
```

## Test

`pytest`

pylint

## API changes (for consumers)

`GET /cookies/` no longer returns a Call object wrapping a Jar; it returns a flattened payload.
Map your existing code as follows:

| Old (`{id, domain, jar: {...}}`) | New (`{call_id, jar_id, domain, cookies, headers, account}`) |
|---|---|
| `resp.id` (the call id)          | `resp.call_id` |
| `resp.jar.id`                    | `resp.jar_id` |
| `resp.jar.domain` / `resp.domain`| `resp.domain` |
| `resp.jar.cookies`               | `resp.cookies` |
| *(new)*                          | `resp.headers` — other request headers, e.g. `User-Agent` |
| *(new)*                          | `resp.account` — linked credential account, if any |

Other notes:

- `PATCH /calls/{id}` is backward compatible (same `success` / `url` / `timestamp` fields). It
  now *additionally* accepts `cookies` (raw Netscape txt) or `curl_cmd` to refresh the linked
  jar's cookies after a request.
- `GET /cookies/` accepts optional `account_id` / `label` query params to select a specific
  user's jar for a domain.
- New endpoints: `/accounts/` (credential CRUD) and `GET /domains/{domain}` (single-domain
  detail). See the endpoint table in `CLAUDE.md`.