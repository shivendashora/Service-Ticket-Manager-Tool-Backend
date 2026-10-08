import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request, Form, File, UploadFile,BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, HttpUrl, TypeAdapter, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from jose import jwt, JWTError
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth

from models.main_models import (
    RegisterRequest,
    LoginRequest,
    AssignTicketsRequest,
    UpdateTicketRequest,
    UpdateUserRoleRequest,
    TicketStatus,
    UserRole,
)
from tables.main_tables import Base, Users, Tickets, TicketAttachments, assignee
from helper.helper_functions import (
    ticket_created_mail,
    ticket_assigned_mail,
    update_ticket_status_on_mail,
)


# ---------------------------------------------------------
# ENV
# ---------------------------------------------------------

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
TOKEN_EXPIRY_TIME = int(os.getenv("TOKEN_EXPIRY_TIME", "10"))

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

DB_URL = os.getenv("DB_URL")

# Where the React app lives: allowed by CORS and where Google login redirects back to
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

UPLOAD_DIR = Path(
    os.getenv("UPLOAD_DIR", Path(__file__).parent / "uploads")
)
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_IMAGES_PER_REQUEST = 10
MAX_LINKS_PER_REQUEST = 20


# ---------------------------------------------------------
# APP
# ---------------------------------------------------------

app = FastAPI()


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        FRONTEND_URL
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# SESSION MIDDLEWARE
# ---------------------------------------------------------

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
)


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

engine = create_engine(DB_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Alembic handles database migrations.
# Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------
# PASSWORD HASHING
# ---------------------------------------------------------

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(
    password: str,
    hashed_password: str
) -> bool:
    return pwd_context.verify(
        password,
        hashed_password
    )


# ---------------------------------------------------------
# JWT
# ---------------------------------------------------------

def create_access_token(user_id: int) -> str:

    expire = (
        datetime.now(timezone.utc)
        + timedelta(minutes=TOKEN_EXPIRY_TIME)
    )

    payload = {
        "sub": str(user_id),
        "exp": expire
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
):

    token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    try:

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        user_id = payload.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

    except JWTError:

        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )

    user = (
        db.query(Users)
        .filter(Users.id == int(user_id))
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=401,
            detail="User not found"
        )

    return user


def require_admin(
    current_user: Users = Depends(get_current_user)
):

    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins can perform this action"
        )

    return current_user


# ---------------------------------------------------------
# GOOGLE OAUTH
# ---------------------------------------------------------

oauth = OAuth()

oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,

    server_metadata_url=(
        "https://accounts.google.com/"
        ".well-known/openid-configuration"
    ),

    client_kwargs={
        "scope": "openid email profile"
    }
)


# ---------------------------------------------------------
# NORMAL REGISTRATION
# ---------------------------------------------------------

@app.post("/auth/register")
def register(
    data: RegisterRequest,
    db: Session = Depends(get_db)
):

    existing_user = (
        db.query(Users)
        .filter(Users.email == data.email)
        .first()
    )

    if existing_user:

        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    user = Users(
        name=data.name,
        email=data.email,
        hashed_password=hash_password(data.password),
        google_id=None,
        role="user"
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)

    response = JSONResponse({
        "message": "Account created successfully",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role
        }
    })

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=TOKEN_EXPIRY_TIME * 60
    )

    return response


# ---------------------------------------------------------
# NORMAL LOGIN
# ---------------------------------------------------------

@app.post("/auth/login")
def login(
    data: LoginRequest,
    db: Session = Depends(get_db)
):

    user = (
        db.query(Users)
        .filter(Users.email == data.email)
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    # Google-only account
    if not user.hashed_password:

        raise HTTPException(
            status_code=400,
            detail="This account uses Google login"
        )

    if not verify_password(
        data.password,
        user.hashed_password
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    token = create_access_token(user.id)

    response = JSONResponse({
        "message": "Login successful",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role
        }
    })

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=TOKEN_EXPIRY_TIME * 60
    )

    return response


# ---------------------------------------------------------
# GOOGLE LOGIN
# ---------------------------------------------------------

@app.get("/auth/google/login")
async def google_login(request: Request):

    return await oauth.google.authorize_redirect(
        request,
        GOOGLE_REDIRECT_URI
    )


# ---------------------------------------------------------
# GOOGLE CALLBACK
# ---------------------------------------------------------

@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    db: Session = Depends(get_db)
):

    # Exchange authorization code for Google tokens
    token = await oauth.google.authorize_access_token(
        request
    )

    # Get Google user information
    user_info = token["userinfo"]

    google_id = user_info["sub"]
    email = user_info["email"]
    name = user_info.get("name") or email

    # -----------------------------------------------------
    # STEP 1: Find by Google ID
    # -----------------------------------------------------

    user = (
        db.query(Users)
        .filter(
            Users.google_id == google_id
        )
        .first()
    )

    # -----------------------------------------------------
    # STEP 2: If Google ID doesn't exist,
    #         check email
    # -----------------------------------------------------

    if not user:

        user = (
            db.query(Users)
            .filter(
                Users.email == email
            )
            .first()
        )

    # -----------------------------------------------------
    # STEP 3: Existing account
    # -----------------------------------------------------

    if user:

        # Link Google account if it isn't linked yet
        if not user.google_id:

            user.google_id = google_id

            db.commit()
            db.refresh(user)

    # -----------------------------------------------------
    # STEP 4: New account
    # -----------------------------------------------------

    else:

        user = Users(
            name=name,
            email=email,
            hashed_password=None,
            google_id=google_id,
            role="user"
        )

        db.add(user)
        db.commit()
        db.refresh(user)

    # -----------------------------------------------------
    # STEP 5: Create YOUR JWT
    # -----------------------------------------------------

    access_token = create_access_token(
        user.id
    )

    # -----------------------------------------------------
    # STEP 6: Redirect to React
    # -----------------------------------------------------

    response = RedirectResponse(
        url=FRONTEND_URL
    )

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=TOKEN_EXPIRY_TIME * 60
    )

    return response


# ---------------------------------------------------------
# CURRENT USER
# ---------------------------------------------------------

@app.get("/auth/me")
def get_me(
    current_user: Users = Depends(get_current_user)
):

    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role
    }


# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------

@app.post("/auth/logout")
def logout():

    response = JSONResponse({
        "message": "Logout successful"
    })

    response.delete_cookie(
        key="access_token"
    )

    return response


# ---------------------------------------------------------
# TICKET HELPERS
# ---------------------------------------------------------

def get_active_ticket(db: Session, ticket_id: int) -> Tickets:

    ticket = (
        db.query(Tickets)
        .filter(
            Tickets.id == ticket_id,
            Tickets.deleted_at.is_(None)
        )
        .first()
    )

    if not ticket:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    return ticket


def get_active_assignments(db: Session, ticket_ids: list[int]):

    if not ticket_ids:
        return []

    return (
        db.query(assignee, Users)
        .join(Users, Users.id == assignee.user_id)
        .filter(
            assignee.ticket_id.in_(ticket_ids),
            assignee.deleted_at.is_(None)
        )
        .all()
    )


def is_assigned_to(db: Session, ticket_id: int, user_id: int) -> bool:

    return (
        db.query(assignee)
        .filter(
            assignee.ticket_id == ticket_id,
            assignee.user_id == user_id,
            assignee.deleted_at.is_(None)
        )
        .first()
        is not None
    )


def serialize_user(user: Users) -> dict:

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role
    }


def serialize_attachment(attachment: TicketAttachments) -> dict:

    return {
        "id": attachment.id,
        "type": attachment.type,
        "url": (
            attachment.url
            if attachment.type == "link"
            else f"/attachments/{attachment.id}/file"
        ),
        "file_name": attachment.file_name,
        "content_type": attachment.content_type,
        "size": attachment.size,
        "uploaded_by": attachment.uploaded_by,
        "created_at": attachment.created_at
    }


def serialize_ticket(
    ticket: Tickets,
    assignees: list[dict] | None = None,
    attachments: list[dict] | None = None
) -> dict:

    return {
        "id": ticket.id,
        "title": ticket.title,
        "description": ticket.description,
        "status": ticket.status,
        "estimated_time": ticket.estimated_time,
        "created_by": ticket.created_by,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "assignees": assignees or [],
        "attachments": attachments or []
    }


def serialize_tickets_with_assignees(db: Session, tickets: list[Tickets]) -> list[dict]:

    ticket_ids = [t.id for t in tickets]

    assignees_by_ticket: dict[int, list[dict]] = {}

    for assignment, user in get_active_assignments(db, ticket_ids):
        assignees_by_ticket.setdefault(assignment.ticket_id, []).append({
            **serialize_user(user),
            "assigned_by": assignment.assigned_by,
            "assigned_at": assignment.created_at
        })

    attachments_by_ticket: dict[int, list[dict]] = {}

    if ticket_ids:
        attachments = (
            db.query(TicketAttachments)
            .filter(
                TicketAttachments.ticket_id.in_(ticket_ids),
                TicketAttachments.deleted_at.is_(None)
            )
            .order_by(TicketAttachments.id)
            .all()
        )

        for attachment in attachments:
            attachments_by_ticket.setdefault(attachment.ticket_id, []).append(
                serialize_attachment(attachment)
            )

    return [
        serialize_ticket(
            ticket,
            assignees_by_ticket.get(ticket.id),
            attachments_by_ticket.get(ticket.id)
        )
        for ticket in tickets
    ]


# ---------------------------------------------------------
# ATTACHMENT HELPERS
# ---------------------------------------------------------

# Detect images from their bytes, never trust the client's content type
IMAGE_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
]

url_validator = TypeAdapter(HttpUrl)


def detect_image_type(data: bytes) -> tuple[str, str] | None:

    for signature, content_type, extension in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return content_type, extension

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"

    return None


def validate_links(links: list[str] | None) -> list[str]:

    # Swagger sends empty strings for blank list items
    cleaned = [link.strip() for link in links or [] if link and link.strip()]

    if len(cleaned) > MAX_LINKS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"You can attach at most {MAX_LINKS_PER_REQUEST} links"
        )

    for link in cleaned:

        if len(link) > 2048:
            raise HTTPException(
                status_code=422,
                detail="Links must be at most 2048 characters"
            )

        try:
            url_validator.validate_python(link)
        except ValidationError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid link: {link}"
            )

    return cleaned


def read_images(images: list[UploadFile] | None) -> list[dict]:

    # Skip file parts sent without a file name
    images = [image for image in images or [] if image.filename]

    if len(images) > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"You can attach at most {MAX_IMAGES_PER_REQUEST} images"
        )

    read = []

    for image in images:

        data = image.file.read(MAX_IMAGE_SIZE + 1)

        if len(data) > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"{image.filename} is larger than 5 MB"
            )

        detected = detect_image_type(data)

        if not detected:
            raise HTTPException(
                status_code=415,
                detail=f"{image.filename} is not a PNG, JPEG, GIF or WebP image"
            )

        content_type, extension = detected

        read.append({
            "data": data,
            "file_name": Path(image.filename).name[:255],
            "content_type": content_type,
            "extension": extension
        })

    return read


def add_attachments(
    db: Session,
    ticket_id: int,
    uploaded_by: int,
    links: list[str],
    images: list[dict]
) -> list[Path]:

    # Returns the files written so the caller can remove them if the commit fails

    for link in links:
        db.add(TicketAttachments(
            ticket_id=ticket_id,
            type="link",
            url=link,
            uploaded_by=uploaded_by
        ))

    written: list[Path] = []

    (UPLOAD_DIR / "tickets").mkdir(parents=True, exist_ok=True)

    try:

        for image in images:

            relative_path = f"tickets/{uuid.uuid4().hex}{image['extension']}"
            path = UPLOAD_DIR / relative_path
            path.write_bytes(image["data"])
            written.append(path)

            db.add(TicketAttachments(
                ticket_id=ticket_id,
                type="image",
                file_path=relative_path,
                file_name=image["file_name"],
                content_type=image["content_type"],
                size=len(image["data"]),
                uploaded_by=uploaded_by
            ))

    except Exception:
        remove_files(written)
        raise

    return written


def remove_files(paths: list[Path]):

    for path in paths:
        path.unlink(missing_ok=True)


def can_view_ticket(db: Session, ticket_id: int, user: Users) -> bool:

    return (
        user.role == "admin"
        or is_assigned_to(db, ticket_id, user.id)
    )


# ---------------------------------------------------------
# EMAIL HELPERS
# ---------------------------------------------------------

def get_ticket_recipient_emails(db: Session, ticket: Tickets) -> list[str]:

    # Everyone involved in a ticket: the admin who created it and its assignees
    emails = [user.email for _, user in get_active_assignments(db, [ticket.id])]

    if ticket.created_by:
        creator = db.query(Users).filter(Users.id == ticket.created_by).first()
        if creator:
            emails.append(creator.email)

    return emails


# ---------------------------------------------------------
# CREATE TICKET
# ---------------------------------------------------------

# Sent as multipart/form-data so images can be uploaded
# with the ticket. links and images are optional.

@app.post("/tickets", status_code=201)
def create_ticket(
    background_tasks: BackgroundTasks,
    title: str = Form(min_length=1),
    description: str = Form(min_length=1),
    estimated_time: int | None = Form(default=None, ge=0),
    links: list[str] | None = Form(default=None),
    images: list[UploadFile] | None = File(default=None),
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    # Validate everything before touching the database or disk
    valid_links = validate_links(links)
    valid_images = read_images(images)

    ticket = Tickets(
        title=title,
        description=description,
        estimated_time=estimated_time,
        status="open",
        created_by=current_user.id
    )

    db.add(ticket)
    db.flush()

    written = add_attachments(
        db,
        ticket.id,
        current_user.id,
        valid_links,
        valid_images
    )

    try:
        db.commit()
    except Exception:
        db.rollback()
        remove_files(written)
        raise

    db.refresh(ticket)

    # A new ticket has no assignees yet (assigning is a separate request),
    # so the creator is notified now and assignees when they're assigned
    background_tasks.add_task(
        ticket_created_mail,
        ticket.id,
        ticket.title,
        ticket.description,
        current_user.name,
        [current_user.email]
    )

    return {
        "message": "Ticket created successfully",
        "ticket": serialize_tickets_with_assignees(db, [ticket])[0]
    }


# ---------------------------------------------------------
# ADD ATTACHMENTS TO AN EXISTING TICKET
# ---------------------------------------------------------

@app.post("/tickets/{ticket_id}/attachments", status_code=201)
def add_ticket_attachments(
    ticket_id: int,
    links: list[str] | None = Form(default=None),
    images: list[UploadFile] | None = File(default=None),
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    ticket = get_active_ticket(db, ticket_id)

    valid_links = validate_links(links)
    valid_images = read_images(images)

    if not valid_links and not valid_images:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one link or image"
        )

    written = add_attachments(
        db,
        ticket.id,
        current_user.id,
        valid_links,
        valid_images
    )

    try:
        db.commit()
    except Exception:
        db.rollback()
        remove_files(written)
        raise

    return {
        "message": "Attachments added successfully",
        "ticket": serialize_tickets_with_assignees(db, [ticket])[0]
    }


# ---------------------------------------------------------
# REMOVE ATTACHMENT (soft delete)
# ---------------------------------------------------------

@app.delete("/tickets/{ticket_id}/attachments/{attachment_id}")
def delete_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    get_active_ticket(db, ticket_id)

    updated = (
        db.query(TicketAttachments)
        .filter(
            TicketAttachments.id == attachment_id,
            TicketAttachments.ticket_id == ticket_id,
            TicketAttachments.deleted_at.is_(None)
        )
        .update({"deleted_at": datetime.utcnow()})
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Attachment not found"
        )

    db.commit()

    return {
        "message": "Attachment removed successfully"
    }


# ---------------------------------------------------------
# VIEW AN IMAGE ATTACHMENT
# Only users who can see the ticket can load its images
# ---------------------------------------------------------

@app.get("/attachments/{attachment_id}/file")
def get_attachment_file(
    attachment_id: int,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    attachment = (
        db.query(TicketAttachments)
        .join(Tickets, Tickets.id == TicketAttachments.ticket_id)
        .filter(
            TicketAttachments.id == attachment_id,
            TicketAttachments.type == "image",
            TicketAttachments.deleted_at.is_(None),
            Tickets.deleted_at.is_(None)
        )
        .first()
    )

    if (
        not attachment
        or not can_view_ticket(db, attachment.ticket_id, current_user)
    ):
        raise HTTPException(
            status_code=404,
            detail="Attachment not found"
        )

    upload_root = UPLOAD_DIR.resolve()
    path = (UPLOAD_DIR / attachment.file_path).resolve()

    if upload_root not in path.parents or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Attachment file is missing"
        )

    return FileResponse(
        path,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff"
        }
    )


# ---------------------------------------------------------
# LIST TICKETS
# Admins see every ticket, users see tickets assigned to them
# ---------------------------------------------------------

@app.get("/tickets")
def list_tickets(
    status: TicketStatus | None = None,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    query = (
        db.query(Tickets)
        .filter(Tickets.deleted_at.is_(None))
    )

    if current_user.role != "admin":
        assigned_ticket_ids = (
            db.query(assignee.ticket_id)
            .filter(
                assignee.user_id == current_user.id,
                assignee.deleted_at.is_(None)
            )
        )
        query = query.filter(Tickets.id.in_(assigned_ticket_ids))

    if status:
        query = query.filter(Tickets.status == status)

    tickets = query.order_by(Tickets.created_at.desc()).all()

    return {
        "tickets": serialize_tickets_with_assignees(db, tickets)
    }


# ---------------------------------------------------------
# GET SINGLE TICKET
# ---------------------------------------------------------

@app.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    ticket = get_active_ticket(db, ticket_id)

    # Users only see tickets assigned to them
    if (
        current_user.role != "admin"
        and not is_assigned_to(db, ticket_id, current_user.id)
    ):
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    return {
        "ticket": serialize_tickets_with_assignees(db, [ticket])[0]
    }


# ---------------------------------------------------------
# UPDATE TICKET
# Admins can update any field, assigned users only the status
# ---------------------------------------------------------

@app.patch("/tickets/{ticket_id}")
def update_ticket(
    ticket_id: int,
    data: UpdateTicketRequest,
    background_tasks: BackgroundTasks,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    ticket = get_active_ticket(db, ticket_id)

    changes = data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="No fields to update"
        )

    if current_user.role != "admin":

        if not is_assigned_to(db, ticket_id, current_user.id):
            raise HTTPException(
                status_code=404,
                detail="Ticket not found"
            )

        if set(changes) != {"status"}:
            raise HTTPException(
                status_code=403,
                detail="You can only update the status of your tickets"
            )

    for field in ("title", "description", "status"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status_code=400,
                detail=f"{field} cannot be null"
            )

    old_status = ticket.status

    for field, value in changes.items():
        setattr(ticket, field, value)

    db.commit()
    db.refresh(ticket)

    # Runs after the response is sent; only for a saved, real status change.
    # Plain values are passed because the db session may be closed by then.
    if "status" in changes and ticket.status != old_status:
        background_tasks.add_task(
            update_ticket_status_on_mail,
            ticket.id,
            ticket.title,
            ticket.status,
            current_user.name,
            current_user.email,
            # The creator, every assignee and whoever made the change
            get_ticket_recipient_emails(db, ticket) + [current_user.email]
        )

    return {
        "message": "Ticket updated successfully",
        "ticket": serialize_tickets_with_assignees(db, [ticket])[0]
    }


# ---------------------------------------------------------
# DELETE TICKET (soft delete)
# ---------------------------------------------------------

@app.delete("/tickets/{ticket_id}")
def delete_ticket(
    ticket_id: int,
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    ticket = get_active_ticket(db, ticket_id)

    now = datetime.utcnow()

    ticket.deleted_at = now

    (
        db.query(assignee)
        .filter(
            assignee.ticket_id == ticket_id,
            assignee.deleted_at.is_(None)
        )
        .update({"deleted_at": now})
    )

    db.commit()

    return {
        "message": "Ticket deleted successfully"
    }


# ---------------------------------------------------------
# ASSIGN TICKETS
# ---------------------------------------------------------

@app.post("/assign-tickets")
def assign_tickets(
    data: AssignTicketsRequest,
    background_tasks: BackgroundTasks,
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    assigned_tickets = set(data.ticket_id)

    if not assigned_tickets:
        raise HTTPException(
            status_code=400,
            detail="No tickets provided"
        )

    to_user = (
        db.query(Users)
        .filter(
            Users.id == data.to_user_id,
            Users.deleted_at.is_(None)
        )
        .first()
    )

    if not to_user:
        raise HTTPException(
            status_code=404,
            detail="User to assign not found"
        )

    titles_by_id = {
        ticket.id: ticket.title
        for ticket in db.query(Tickets)
        .filter(
            Tickets.id.in_(assigned_tickets),
            Tickets.deleted_at.is_(None)
        )
        .all()
    }
    missing_ids = assigned_tickets - set(titles_by_id)

    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Tickets not found: {sorted(missing_ids)}"
        )

    # Skip tickets already assigned to this user
    already_assigned = {
        assignment.ticket_id
        for assignment in db.query(assignee)
        .filter(
            assignee.ticket_id.in_(assigned_tickets),
            assignee.user_id == data.to_user_id,
            assignee.deleted_at.is_(None)
        )
        .all()
    }

    newly_assigned = sorted(assigned_tickets - already_assigned)

    for ticket_id in newly_assigned:
        db.add(assignee(
            ticket_id=ticket_id,
            user_id=data.to_user_id,
            assigned_by=current_user.id
        ))

    db.commit()

    # The new task reaches the assigned user, with the assigning admin in copy
    if newly_assigned:
        background_tasks.add_task(
            ticket_assigned_mail,
            [(ticket_id, titles_by_id[ticket_id]) for ticket_id in newly_assigned],
            to_user.name,
            current_user.name,
            [to_user.email, current_user.email]
        )

    return {
        "message": "tickets assigned successfully",
        "assigned": newly_assigned,
        "already_assigned": sorted(already_assigned)
    }


# ---------------------------------------------------------
# UNASSIGN TICKET
# ---------------------------------------------------------

@app.delete("/tickets/{ticket_id}/assignees/{user_id}")
def unassign_ticket(
    ticket_id: int,
    user_id: int,
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    get_active_ticket(db, ticket_id)

    updated = (
        db.query(assignee)
        .filter(
            assignee.ticket_id == ticket_id,
            assignee.user_id == user_id,
            assignee.deleted_at.is_(None)
        )
        .update({"deleted_at": datetime.utcnow()})
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="This user is not assigned to the ticket"
        )

    db.commit()

    return {
        "message": "User unassigned successfully"
    }


# ---------------------------------------------------------
# LIST USERS (admin)
# ---------------------------------------------------------

@app.get("/users")
def list_users(
    role: UserRole | None = None,
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    query = (
        db.query(Users)
        .filter(Users.deleted_at.is_(None))
    )

    if role:
        query = query.filter(Users.role == role)

    users = query.order_by(Users.name).all()

    return {
        "users": [serialize_user(user) for user in users]
    }


# ---------------------------------------------------------
# CHANGE USER ROLE (admin)
# ---------------------------------------------------------

@app.patch("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    data: UpdateUserRoleRequest,
    current_user: Users = Depends(require_admin),
    db: Session = Depends(get_db)
):

    # Prevents an admin from locking themselves out
    if user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot change your own role"
        )

    user = (
        db.query(Users)
        .filter(
            Users.id == user_id,
            Users.deleted_at.is_(None)
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    user.role = data.role

    db.commit()
    db.refresh(user)

    return {
        "message": "Role updated successfully",
        "user": serialize_user(user)
    }
