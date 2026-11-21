# RoadRec backend (FastAPI)

    cd backend
    pip install -r requirements.txt
    uvicorn app.main:app --reload        # API docs at /docs
    python -m pytest -q                  # 7 tests

## Configuration (environment)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Default `sqlite:///./roadrec.db`; use Postgres when deployed |
| `SECRET_KEY` | Signs sign-in tokens. **Required** when `ENV=production` |
| `INVITE_CODE` | Needed to register insurer/authority accounts. Unset means those roles cannot register |
| `MAX_UPLOAD_BYTES` | Default 5 MB |
| `CORS_ORIGINS` | Comma-separated; default `*` (tighten before the live demo) |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Optional graph sync (untested against a live Neo4j) |

## Roles

- **fleet_manager** (open registration): registers vehicles, uploads events, reads reports, creates and revokes share links. Sees only their own fleet.
- **insurer / authority** (invite code): open a share link's tamper-checked evidence report with a case summary.

## Endpoints

| Endpoint | Who |
|---|---|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | all |
| `POST/GET /vehicles` | fleet manager |
| `POST /events` (multipart: `vehicle_id`, `file`, optional `recorded_at`, `claimed_sha256`) | fleet manager |
| `GET /events`, `GET /events/{id}`, `GET /events/{id}/verify` | fleet manager (own events) |
| `POST /events/{id}/share`, `DELETE /shares/{token}` | fleet manager |
| `GET /shared/{token}` | anyone with the link: read-only report, integrity result, no raw file |
| `GET /shared/{token}/evidence-report` | insurer / authority: report + integrity + case summary |

Errors are `{"detail": {"code", "message"}}`: `file_too_large` (413), `duplicate_upload` (409), `too_little_data`, `bad_header`, `bad_row`, `bad_time`, `not_text`, `empty_file` (422), `share_expired` (410).

The server hashes every file itself; a client-supplied fingerprint is only compared against it (`match`/`mismatch`/`not_provided`).
`/verify` and the evidence report re-hash the stored file each time, so later tampering with the database copy shows up.
