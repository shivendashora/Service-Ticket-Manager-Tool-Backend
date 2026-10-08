# Service Ticket Manager — Backend

REST API for a customer-support ticketing system, built with **FastAPI**, **SQLAlchemy** and **PostgreSQL**.

Admins create tickets, attach screenshots and links, and assign them to agents. Agents see only the tickets assigned to them and move them through their lifecycle. Users sign in with email and password or with Google. Authentication is a JWT stored in an httpOnly cookie.

Frontend: [ServicesTicketManagerTool](https://github.com/shivendashora/ServicesTicketManagerTool)

---

## Contents

- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Database schema](#database-schema)
- [Roles and permissions](#roles-and-permissions)
- [Ticket lifecycle](#ticket-lifecycle)
- [Authentication flows](#authentication-flows)
- [Attachments](#attachments)
- [API reference](#api-reference)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Database migrations](#database-migrations)
- [Known limitations](#known-limitations)

---

## Tech stack

| Concern            | Library                                   |
| ------------------ | ----------------------------------------- |
| Web framework      | FastAPI + Uvicorn                         |
| ORM / migrations   | SQLAlchemy 2 + Alembic                    |
| Database           | PostgreSQL (`psycopg2`)                   |
| Password hashing   | passlib (bcrypt)                          |
| Tokens             | python-jose (JWT, HS256 by default)       |
| Google sign-in     | Authlib (OpenID Connect)                  |
| Validation         | Pydantic v2 (`EmailStr`, `HttpUrl`, …)    |
| File uploads       | python-multipart                          |

---

## Architecture

```mermaid
flowchart LR
    subgraph Client
        FE["React frontend<br/>(Vite, :5173)"]
    end

    subgraph API["FastAPI app (:8000)"]
        MW["Middleware<br/>CORS · Session"]
        AUTH["Auth routes<br/>/auth/*"]
        TIX["Ticket routes<br/>/tickets · /assign-tickets"]
        ATT["Attachment routes<br/>/tickets/{id}/attachments<br/>/attachments/{id}/file"]
        USR["User routes<br/>/users"]
        DEP["Dependencies<br/>get_db · get_current_user · require_admin"]
    end

    DB[("PostgreSQL")]
    FS[["uploads/ folder<br/>(image files)"]]
    G["Google OAuth"]

    FE -- "JSON / multipart<br/>+ access_token cookie" --> MW
    MW --> AUTH & TIX & ATT & USR
    AUTH & TIX & ATT & USR --> DEP
    DEP --> DB
    ATT --> FS
    AUTH <--> G
```

Every protected route goes through the same dependency chain:

```mermaid
flowchart LR
    R[Request] --> C{"access_token<br/>cookie present?"}
    C -- no --> E1["401 Not authenticated"]
    C -- yes --> D{"JWT valid and<br/>not expired?"}
    D -- no --> E2["401 Invalid or expired token"]
    D -- yes --> U{"User exists?"}
    U -- no --> E3["401 User not found"]
    U -- yes --> A{"Admin-only route?"}
    A -- "no" --> OK["Handler runs"]
    A -- "yes, role = admin" --> OK
    A -- "yes, role = user" --> E4["403 Only admins can<br/>perform this action"]
```

---

## Database schema

All tables share four base columns from `BaseColumns`: `id`, `created_at`, `updated_at` and `deleted_at`. Rows are **soft-deleted**: `deleted_at` is set and every query filters on `deleted_at IS NULL`.

```mermaid
erDiagram
    USERS ||--o{ ASSIGNEE : "is assigned"
    USERS |o--o{ ASSIGNEE : "assigns (assigned_by)"
    USERS |o--o{ TICKETS : "creates (created_by)"
    USERS |o--o{ TICKET_ATTACHMENTS : "uploads (uploaded_by)"
    TICKETS ||--o{ ASSIGNEE : "has"
    TICKETS ||--o{ TICKET_ATTACHMENTS : "has"

    USERS {
        int id PK
        string name "not null"
        string email UK "not null, indexed"
        string hashed_password "null for Google-only accounts"
        string google_id UK "null for email-only accounts"
        string role "user | admin"
        datetime created_at
        datetime updated_at
        datetime deleted_at "soft delete"
    }

    TICKETS {
        int id PK
        string title "not null"
        string description "not null"
        string status "open | in_progress | resolved | closed"
        int estimated_time "hours, nullable"
        int created_by FK "users.id"
        datetime created_at
        datetime updated_at
        datetime deleted_at "soft delete"
    }

    ASSIGNEE {
        int id PK
        int ticket_id FK "tickets.id"
        int user_id FK "users.id"
        int assigned_by FK "users.id, nullable"
        datetime created_at "used as assigned_at"
        datetime updated_at
        datetime deleted_at "set on unassign"
    }

    TICKET_ATTACHMENTS {
        int id PK
        int ticket_id FK "tickets.id, indexed"
        string type "link | image"
        string url "links only"
        string file_path "images only, relative to UPLOAD_DIR"
        string file_name "original upload name"
        string content_type "detected from file bytes"
        int size "bytes"
        int uploaded_by FK "users.id"
        datetime created_at
        datetime updated_at
        datetime deleted_at "soft delete"
    }
```

### Tables at a glance

| Table                | Purpose                                                                                     |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `users`              | Accounts. A user can have a password, a linked Google account, or both. `role` drives permissions. |
| `tickets`            | Support tickets. `created_by` records the admin who created it.                             |
| `assignee`           | Many-to-many link between tickets and users. A ticket can have several assignees. Unassigning soft-deletes the row, so assignment history is kept. |
| `ticket_attachments` | Images (stored on disk) and links (stored as URLs) attached to a ticket.                    |

### Relationship notes

- **Ticket ↔ User is many-to-many** through `assignee`. The same user is never assigned twice to the same ticket: `/assign-tickets` skips pairs that already exist.
- **Deleting a ticket** soft-deletes the ticket *and* all of its active assignments in one transaction.
- **Google sign-in links accounts by email.** If someone registered with email/password and later signs in with Google using the same address, `google_id` is added to the existing row instead of creating a new user.

---

## Roles and permissions

There are two roles: `user` (shown as "Agent" in the frontend) and `admin`. Every new account, whether from registration or Google, starts as `user`.

| Action                                     | `user` (agent)                 | `admin` |
| ------------------------------------------ | :----------------------------: | :-----: |
| Register / log in / view own profile       | ✅                             | ✅      |
| List tickets                               | Only tickets assigned to them  | All     |
| View a ticket                              | Only if assigned (else `404`)  | ✅      |
| Change ticket **status**                   | Only if assigned               | ✅      |
| Change title / description / estimate      | ❌ `403`                       | ✅      |
| Create / delete tickets                    | ❌                             | ✅      |
| Add / remove attachments                   | ❌                             | ✅      |
| View image attachments                     | Only on assigned tickets       | ✅      |
| Assign / unassign users                    | ❌                             | ✅      |
| List users / change roles                  | ❌                             | ✅ (not their own role) |

> Tickets that aren't assigned to an agent return `404` rather than `403`, so agents can't tell which ticket IDs exist.

---

## Ticket lifecycle

New tickets always start as `open`. The API allows a ticket to move between **any** two statuses. The diagram shows the intended flow.

```mermaid
stateDiagram-v2
    [*] --> open : POST /tickets
    open --> in_progress : agent starts work
    in_progress --> resolved : fix delivered
    resolved --> closed : confirmed
    resolved --> in_progress : reopened
    closed --> open : reopened
    open --> closed : won't fix / duplicate
    closed --> [*] : DELETE /tickets/{id} (soft delete)
```

---

## Authentication flows

The API never returns the token in the response body. It sets it as an **httpOnly cookie** named `access_token` (`SameSite=Lax`), which the browser then sends automatically. Clients must send requests with credentials, for example `fetch(url, { credentials: 'include' })`.

### Email and password

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as FastAPI
    participant DB as PostgreSQL

    B->>API: POST /auth/login {email, password}
    API->>DB: SELECT user WHERE email = ?
    DB-->>API: user row
    alt no user or wrong password
        API-->>B: 401 Invalid email or password
    else Google-only account (no password)
        API-->>B: 400 This account uses Google login
    else valid
        API->>API: create JWT {sub: user.id, exp}
        API-->>B: 200 {user} + Set-Cookie: access_token (httpOnly)
    end
    B->>API: GET /tickets (cookie sent automatically)
    API-->>B: 200 {tickets}
```

`POST /auth/register` works the same way: it creates the user with role `user` and logs them in immediately.

### Google OAuth

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as FastAPI
    participant G as Google
    participant DB as PostgreSQL

    B->>API: GET /auth/google/login
    API-->>B: 302 redirect to Google (state stored in session cookie)
    B->>G: User signs in and consents
    G-->>B: 302 to GOOGLE_REDIRECT_URI?code=…
    B->>API: GET /auth/google/callback?code=…
    API->>G: Exchange code for tokens + userinfo
    G-->>API: {sub, email, name}
    API->>DB: Find user by google_id, then by email
    alt existing user without google_id
        API->>DB: Link google_id to that user
    else no user found
        API->>DB: INSERT user (role = user, no password)
    end
    API-->>B: 302 to http://localhost:5173 + Set-Cookie: access_token
```

Log out with `POST /auth/logout`, which deletes the cookie.

---

## Attachments

Tickets can carry **images** (uploaded files) and **links** (URLs). They can be sent when the ticket is created or added later. Both endpoints take `multipart/form-data`.

```mermaid
flowchart TD
    REQ["multipart/form-data<br/>links[] + images[]"] --> L{"Validate links"}
    L -- "> 20 links" --> X1["400"]
    L -- "not http(s) or > 2048 chars" --> X2["422"]
    L --> I{"Validate images"}
    I -- "> 10 images" --> X3["400"]
    I -- "> 5 MB" --> X4["413"]
    I -- "magic bytes not PNG/JPEG/GIF/WebP" --> X5["415"]
    I --> W["Write files to uploads/tickets/uuid.ext"]
    W --> D["INSERT ticket_attachments rows"]
    D --> C{"COMMIT ok?"}
    C -- yes --> OK["201 + ticket with attachments"]
    C -- no --> RB["Rollback + delete written files"]
```

Security measures:

- **The file type comes from the file's bytes, not from the client.** `detect_image_type` checks magic numbers, so a script renamed to `.png` is rejected.
- **Files get random names** (`uuid4`). The original name is only stored in the database, so an upload can't overwrite another file or escape the folder.
- **Images are served through an authenticated route,** `GET /attachments/{id}/file`. It checks that the caller can see the ticket, confirms the resolved path is still inside `UPLOAD_DIR`, and sends `X-Content-Type-Options: nosniff`.
- **Removing an attachment is a soft delete.** The file stays on disk.

---

## API reference

Interactive docs are available at **`/docs`** (Swagger UI) and **`/redoc`** when the server is running.

🔓 = public · 🔑 = any logged-in user · 👑 = admin only

### Auth

| Method | Path                     |    | Body                         | Description                                |
| ------ | ------------------------ | -- | ---------------------------- | ------------------------------------------ |
| POST   | `/auth/register`         | 🔓 | `{name, email, password}`    | Create an account and log in               |
| POST   | `/auth/login`            | 🔓 | `{email, password}`          | Log in and set the `access_token` cookie   |
| GET    | `/auth/google/login`     | 🔓 | —                            | Start Google sign-in (redirect)            |
| GET    | `/auth/google/callback`  | 🔓 | —                            | Google redirects here; sets cookie, redirects to frontend |
| GET    | `/auth/me`               | 🔑 | —                            | Current user `{id, name, email, role}`     |
| POST   | `/auth/logout`           | 🔓 | —                            | Clear the cookie                           |

### Tickets

| Method | Path                                         |    | Body                                                         | Description |
| ------ | -------------------------------------------- | -- | ------------------------------------------------------------ | ----------- |
| GET    | `/tickets?status=`                           | 🔑 | —                                                            | List tickets, newest first. Admins get all; agents get their assigned tickets. Optional `status` filter. |
| POST   | `/tickets`                                   | 👑 | **multipart**: `title`, `description`, `estimated_time?`, `links[]?`, `images[]?` | Create a ticket (status `open`) |
| GET    | `/tickets/{id}`                              | 🔑 | —                                                            | One ticket with assignees and attachments |
| PATCH  | `/tickets/{id}`                              | 🔑 | `{title?, description?, estimated_time?, status?}`          | Update. Agents may only send `status`. |
| DELETE | `/tickets/{id}`                              | 👑 | —                                                            | Soft-delete the ticket and its assignments |

### Attachments

| Method | Path                                          |    | Body                                 | Description |
| ------ | --------------------------------------------- | -- | ------------------------------------ | ----------- |
| POST   | `/tickets/{id}/attachments`                   | 👑 | **multipart**: `links[]?`, `images[]?` | Add at least one link or image |
| DELETE | `/tickets/{id}/attachments/{attachment_id}`   | 👑 | —                                    | Remove an attachment (soft delete) |
| GET    | `/attachments/{attachment_id}/file`           | 🔑 | —                                    | Stream an image (only if you can see the ticket) |

### Assignments

| Method | Path                                    |    | Body                                   | Description |
| ------ | --------------------------------------- | -- | -------------------------------------- | ----------- |
| POST   | `/assign-tickets`                       | 👑 | `{ticket_id: [1, 2, 3], to_user_id: 5}` | Assign one or more tickets to a user. Returns `assigned` and `already_assigned` ID lists. |
| DELETE | `/tickets/{id}/assignees/{user_id}`     | 👑 | —                                      | Unassign a user |

### Users

| Method | Path                    |    | Body                 | Description |
| ------ | ----------------------- | -- | -------------------- | ----------- |
| GET    | `/users?role=`          | 👑 | —                    | List users by name. Optional `role` filter (`user` / `admin`). |
| PATCH  | `/users/{id}/role`      | 👑 | `{role}`             | Change a user's role. Admins can't change their own. |

### Example ticket response

```json
{
  "ticket": {
    "id": 12,
    "title": "Customer cannot reset password",
    "description": "Reset email never arrives for gmail addresses.",
    "status": "in_progress",
    "estimated_time": 4,
    "created_by": 1,
    "created_at": "2026-10-08T05:12:44.120931",
    "updated_at": "2026-10-08T06:30:02.004812",
    "assignees": [
      {
        "id": 5,
        "name": "Jane Cooper",
        "email": "jane@example.com",
        "role": "user",
        "assigned_by": 1,
        "assigned_at": "2026-10-08T05:13:10.551204"
      }
    ],
    "attachments": [
      {
        "id": 3,
        "type": "image",
        "url": "/attachments/3/file",
        "file_name": "error-screen.png",
        "content_type": "image/png",
        "size": 84213,
        "uploaded_by": 1,
        "created_at": "2026-10-08T05:12:44.130551"
      },
      {
        "id": 4,
        "type": "link",
        "url": "https://status.example.com/incidents/42",
        "file_name": null,
        "content_type": null,
        "size": null,
        "uploaded_by": 1,
        "created_at": "2026-10-08T05:12:44.130551"
      }
    ]
  }
}
```

Timestamps are UTC without a timezone suffix.

### Errors

Errors follow FastAPI's standard shape:

```json
{ "detail": "Ticket not found" }
```

Validation errors (`422`) return a list instead:

```json
{ "detail": [{ "loc": ["body", "email"], "msg": "value is not a valid email address", "type": "value_error" }] }
```

---

## Project structure

```
.
├── main.py                  # FastAPI app: config, middleware, auth, every route
├── models/
│   └── main_models.py       # Pydantic request schemas + TicketStatus / UserRole literals
├── tables/
│   └── main_tables.py       # SQLAlchemy models: Users, Tickets, assignee, TicketAttachments
├── helper/
│   └── helper_functions.py  # Older helpers (not imported by main.py)
├── alembic/
│   ├── env.py               # Uses tables.main_tables.Base.metadata
│   └── versions/            # Migration history (see below)
├── alembic.ini              # Alembic config (sqlalchemy.url)
├── uploads/                 # Uploaded images (git-ignored, created automatically)
├── requirements.txt
└── .env.example             # Copy to .env and fill in
```

---

## Getting started

### Prerequisites

- Python 3.10+
- PostgreSQL 13+

### 1. Install

```bash
git clone https://github.com/shivendashora/Service-ticket-manager-backend.git
cd Service-ticket-manager-backend

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env   # then edit the values
```

Create the database named in `DB_URL`, for example:

```sql
CREATE DATABASE "service-ticket-platform";
```

Also update `sqlalchemy.url` in `alembic.ini` if your database URL is different.

### 3. Create the tables

The first migration (`462e15ae23a2`) is empty, because the original tables were created with `create_all`. On a **new, empty database**, create the schema from the models and then mark every migration as applied:

```bash
python -c "from sqlalchemy import create_engine; from dotenv import load_dotenv; import os; load_dotenv(); from tables.main_tables import Base; Base.metadata.create_all(create_engine(os.environ['DB_URL']))"
alembic stamp head
```

On an **existing database** that's already tracked by Alembic, just run:

```bash
alembic upgrade head
```

### 4. Run

```bash
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000/docs.

### 5. Create the first admin

Every new account starts as `user`, and only an admin can promote others. Register through the API or the frontend, then promote yourself once in SQL:

```sql
UPDATE users SET role = 'admin' WHERE email = 'you@example.com';
```

After that, admins can manage roles from the frontend's Team page (`PATCH /users/{id}/role`).

---

## Configuration

| Variable               | Required | Default            | Description |
| ---------------------- | :------: | ------------------ | ----------- |
| `SECRET_KEY`           | ✅       | —                  | Signs JWTs and the OAuth session cookie. Use a long random string. |
| `ALGORITHM`            |          | `HS256`            | JWT algorithm |
| `TOKEN_EXPIRY_TIME`    |          | `10`               | Token and cookie lifetime in **minutes**. There's no refresh token, so users must log in again after this. |
| `DB_URL`               | ✅       | —                  | SQLAlchemy URL, e.g. `postgresql://user:pass@localhost:5432/db` |
| `GOOGLE_CLIENT_ID`     | for Google | —                | OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | for Google | —                | OAuth client secret |
| `GOOGLE_REDIRECT_URI`  | for Google | —                | Must match the Google console, e.g. `http://localhost:8000/auth/google/callback` |
| `UPLOAD_DIR`           |          | `./uploads`        | Where image attachments are written |

> Variable names are case-sensitive on macOS and Linux. Use the uppercase names above.

Fixed limits in `main.py`: images up to **5 MB** each, at most **10 images** and **20 links** per request.

---

## Database migrations

```mermaid
flowchart LR
    A["462e15ae23a2<br/>initial tables<br/>(empty, created via create_all)"]
    B["c675e542c875<br/>users: + google_id (unique)<br/>hashed_password nullable"]
    C["94a58e11a945<br/>assignee: + assigned_by"]
    D["b3f1d2a7c9e4<br/>tickets: + created_by FK users.id"]
    E["d8e2a4f6b1c3<br/>+ ticket_attachments table"]
    A --> B --> C --> D --> E
```

To create a new migration after changing `tables/main_tables.py`:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

---

## Known limitations

- **CORS and the OAuth redirect are hardcoded** to `http://localhost:5173` in `main.py`. Change both before deploying anywhere else.
- **Cookies are sent with `secure=False`** so they work over plain HTTP in development. Set `secure=True` when serving over HTTPS.
- **Tokens can't be refreshed.** When `TOKEN_EXPIRY_TIME` passes, the user has to log in again.
- **Soft-deleted attachments stay on disk.** Nothing removes them from `uploads/` yet.
- **Status changes aren't restricted.** Any status can follow any other.
