import base64
import logging
import os
from email.message import EmailMessage

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

logger = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed": "Closed",
}


def get_gmail_credentials() -> Credentials | None:

    # Built from a refresh token stored in .env (created once with
    # scripts/get_gmail_refresh_token.py). google-auth swaps it for a
    # short-lived access token automatically whenever one is needed.
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN")
    client_id = os.getenv("GMAIL_CLIENT_ID")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET")

    if not (refresh_token and client_id and client_secret):
        return None

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=GMAIL_SCOPES,
    )


def gmail_send_message(to: list[str], subject: str, body: str):
    """Send a plain-text email from GMAIL_SENDER through the Gmail API.

    Returns the sent message (including its id), or None if it wasn't sent.
    Never raises: this runs as a background task, after the response is sent.
    """

    recipients = sorted({address for address in to if address})

    if not recipients:
        return None

    creds = get_gmail_credentials()
    sender = os.getenv("GMAIL_SENDER")

    if not creds or not sender:
        logger.warning("Gmail is not configured, skipping email: %s", subject)
        return None

    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        message = EmailMessage()

        message.set_content(body)

        message["To"] = ", ".join(recipients)
        message["From"] = sender
        message["Subject"] = subject

        # encoded message
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        create_message = {"raw": encoded_message}
        # pylint: disable=E1101
        send_message = (
            service.users()
            .messages()
            .send(userId="me", body=create_message)
            .execute()
        )
        logger.info("Email sent, message id: %s", send_message["id"])
        print(f"Email sent: \"{subject}\" to {', '.join(recipients)} (message id: {send_message['id']})")

    except HttpError as error:
        logger.error("Gmail API error: %s", error)
        send_message = None

    except Exception:
        # e.g. the refresh token was revoked or expired
        logger.exception("Could not send email: %s", subject)
        send_message = None

    return send_message


def ticket_code(ticket_id: int) -> str:
    return f"#{ticket_id:04d}"


def ticket_created_mail(
    ticket_id: int,
    title: str,
    description: str,
    created_by_name: str,
    recipients: list[str]
):

    subject = f"New ticket {ticket_code(ticket_id)}: {title}"

    body = (
        "Hi,\n\n"
        f"A new ticket was created by {created_by_name}.\n\n"
        f"Ticket: {ticket_code(ticket_id)} \"{title}\"\n"
        "Status: Open\n\n"
        f"{description}\n\n"
        
    )

    gmail_send_message(recipients, subject, body)


def ticket_assigned_mail(
    tickets: list[tuple[int, str]],
    assignee_name: str,
    assigned_by_name: str,
    recipients: list[str]
):

    # One email per assign request, even when several tickets are assigned at once
    if len(tickets) == 1:
        ticket_id = tickets[0][0]
        subject = f"Ticket {ticket_code(ticket_id)} assigned to {assignee_name}"
    else:
        subject = f"{len(tickets)} tickets assigned to {assignee_name}"

    ticket_lines = "\n".join(
        f"  {ticket_code(ticket_id)} \"{title}\"" for ticket_id, title in tickets
    )

    body = (
        f"Hi {assignee_name},\n\n"
        f"{assigned_by_name} assigned you the following ticket(s):\n\n"
        f"{ticket_lines}\n\n"
    )

    gmail_send_message(recipients, subject, body)


def update_ticket_status_on_mail(
    ticket_id: int,
    title: str,
    new_status: str,
    updated_by_name: str,
    updated_by_email: str,
    recipients: list[str]
):

    status = STATUS_LABELS.get(new_status, new_status)
    completed = new_status in ("resolved", "closed")

    if completed:
        subject = f"Ticket {ticket_code(ticket_id)} completed ({status})"
    else:
        subject = f"Ticket {ticket_code(ticket_id)} is now {status}"

    body = (
        "Hi,\n\n"
        f"The status of ticket {ticket_code(ticket_id)} \"{title}\" was changed to {status}.\n\n"
        f"Changed by: {updated_by_name} <{updated_by_email}>\n\n"
        
    )

    gmail_send_message(recipients, subject, body)
