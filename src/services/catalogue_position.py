"""Catalogue position reuses WebProduct.display_order; no campaign SKU ranking.

Legacy zero/negative/NULL values have no explicit position. They follow every
positive position; ties and unranked products keep deterministic ID order.
"""
from sqlalchemy import case

MAX_POSITION = 2_147_483_647


def catalogue_position(value):
    return value if type(value) is int and 0 < value <= MAX_POSITION else None


def validate_position_payload(data):
    """Omitted is untouched; explicit NULL clears; strict positive JSON integer."""
    if 'displayOrder' not in data:
        return
    value = data['displayOrder']
    if value is not None and catalogue_position(value) is None:
        raise ValueError('displayOrder debe ser un entero positivo o null para borrar la posición.')


def catalogue_order_by(model):
    ranked = model.display_order > 0
    return (
        case((ranked, 0), else_=1).asc(),
        case((ranked, model.display_order), else_=None).asc(),
        model.id.asc(),
    )
