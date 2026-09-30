"""Canonical email identity for newsletter welcome-coupon eligibility."""
import re


GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def normalize_email_address(value):
    """Return a trimmed, lowercase email address, rejecting malformed inputs."""
    if not isinstance(value, str):
        return None

    # Whitespace is not valid inside an email address. Removing it also handles
    # accidental spaces when a user pastes an address into the newsletter form.
    email = re.sub(r"\s+", "", value).lower()
    if not email or email.count("@") != 1:
        return None

    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return None

    return f"{local}@{domain}"


def newsletter_email_key(value):
    """Build the stable identity used to issue at most one welcome coupon.

    Gmail treats dots and plus-tags in the local part as aliases. Other domains
    are left untouched because their alias rules are provider-specific.
    """
    email = normalize_email_address(value)
    if not email:
        return None

    local, domain = email.rsplit("@", 1)
    if domain in GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"

    return f"{local}@{domain}"
