"""Additive form audit; deliberately NOT registered in Flask db.metadata.

Production schema changes require the reviewed SQL migration. Importing this
model or running the application's legacy db.create_all() never creates it.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, Index, Integer, String, text,
)
from sqlalchemy.orm import DeclarativeBase


class FormAuditBase(DeclarativeBase):
    pass


class FormAttempt(FormAuditBase):
    __tablename__ = 'form_attempts'

    id = Column(Integer, primary_key=True)
    endpoint = Column(String(10), nullable=False)
    ip_hash = Column(String(64), nullable=False)
    attempted_at = Column(DateTime, nullable=False)
    rate_consumed = Column(Boolean, nullable=False)
    email = Column(String(255), nullable=True)
    canonical_email = Column(String(255), nullable=True)
    duplicate = Column(Boolean, nullable=False, default=False)
    vip_claim = Column(Boolean, nullable=False, default=False)
    outcome = Column(String(30), nullable=False)

    __table_args__ = (
        Index('ix_form_attempts_ip_window', 'ip_hash', 'attempted_at'),
        Index('ix_form_attempts_identity', 'endpoint', 'canonical_email'),
        Index(
            'uq_form_attempts_vip_claim', 'canonical_email', unique=True,
            postgresql_where=text('vip_claim = TRUE'),
            sqlite_where=text('vip_claim = 1'),
        ),
    )
