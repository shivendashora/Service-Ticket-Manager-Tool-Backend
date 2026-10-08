from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer,String
from sqlalchemy.orm import declarative_base

Base = declarative_base()



class BaseColumns(Base):
    __abstract__ = True

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

class Users(BaseColumns):
    __tablename__ = "users"

    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)
    google_id = Column(String, unique=True, nullable=True)
    role = Column(String, nullable=False) 


class Tickets(BaseColumns):
    __tablename__ = "tickets"

    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    status = Column(String, default="open")
    estimated_time = Column(Integer, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class TicketAttachments(BaseColumns):
    __tablename__ = "ticket_attachments"

    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    type = Column(String, nullable=False)  # "link" or "image"
    url = Column(String, nullable=True)  # set for links
    file_path = Column(String, nullable=True)  # set for images, relative to the uploads dir
    file_name = Column(String, nullable=True)  # original name of the uploaded image
    content_type = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class assignee(BaseColumns):
    __tablename__ = "assignee"

    ticket_id = Column(Integer,ForeignKey("tickets.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_by = Column(Integer,ForeignKey("users.id"), nullable=True)






