"""Additive analytics table, deliberately NOT registered in db.metadata.

Apply migrations/20261105_vip_signup_conversions.sql explicitly after approval.
No runtime schema creation, historic backfill, clear email, IP, UA or raw token.
"""
from sqlalchemy import (MetaData, Table, Column, String, Integer, DateTime,
                        UniqueConstraint, CheckConstraint)

conversion_metadata = MetaData()
vip_signup_conversions = Table(
    'vip_signup_conversions', conversion_metadata,
    Column('id', String(36), primary_key=True),
    Column('campaign', String(80), nullable=False),
    Column('email_hash', String(64), nullable=False),
    Column('signup_id', Integer, nullable=False),
    Column('event_id', String(36), nullable=False, unique=True),
    Column('list_id', String(100), nullable=False),
    Column('profile_id', String(100)),
    Column('pre_checked_at', DateTime, nullable=False),
    Column('post_checked_at', DateTime),
    Column('created_at', DateTime, nullable=False),
    Column('expires_at', DateTime, nullable=False),
    Column('confirmed_at', DateTime),
    Column('last_checked_at', DateTime),
    Column('capi_state', String(20), nullable=False, server_default='pending'),
    Column('capi_attempts', Integer, nullable=False, server_default='0'),
    Column('capi_attempted_at', DateTime),
    Column('capi_sent_at', DateTime),
    Column('capi_reason', String(40)),
    UniqueConstraint('campaign', 'email_hash', name='uq_vip_conversion_campaign_email'),
    UniqueConstraint('signup_id', name='uq_vip_conversion_signup'),
    CheckConstraint("capi_state IN ('pending','sending','sent','failed','unknown')",
                    name='ck_vip_conversion_capi_state'),
    CheckConstraint('capi_attempts >= 0 AND capi_attempts <= 2',
                    name='ck_vip_conversion_attempts'),
    CheckConstraint('(profile_id IS NULL AND post_checked_at IS NULL) OR '
                    '(profile_id IS NOT NULL AND post_checked_at IS NOT NULL)',
                    name='ck_vip_conversion_post_evidence'),
    CheckConstraint('confirmed_at IS NULL OR '
                    '(profile_id IS NOT NULL AND post_checked_at IS NOT NULL)',
                    name='ck_vip_conversion_confirmation_evidence'),
    CheckConstraint('post_checked_at IS NULL OR post_checked_at >= pre_checked_at',
                    name='ck_vip_conversion_evidence_order'),
)
