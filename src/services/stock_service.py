"""Transactional web-stock allocation and immutable audit movements."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import func

from src.models.stock import StockMovement, StockReservation
from src.models.user import db
from src.models.web_product import WebProduct


class StockUnavailableError(ValueError):
    """Raised before Checkout creation when sellable web stock is insufficient."""

    def __init__(self, unavailable):
        self.unavailable = unavailable
        names = ', '.join(item['name'] for item in unavailable)
        super().__init__(f'Sin existencias suficientes: {names}')


def _now():
    return datetime.utcnow()


def expire_stale_reservations(now=None):
    now = now or _now()
    return StockReservation.query.filter(
        StockReservation.status == 'active',
        StockReservation.expires_at <= now,
    ).update({'status': 'expired'}, synchronize_session=False)


def active_reservation_totals(product_ids: Iterable[int], now=None):
    ids = [product_id for product_id in product_ids if product_id is not None]
    if not ids:
        return {}
    now = now or _now()
    rows = db.session.query(
        StockReservation.product_id,
        func.coalesce(func.sum(StockReservation.quantity), 0),
    ).filter(
        StockReservation.product_id.in_(ids),
        StockReservation.status == 'active',
        StockReservation.expires_at > now,
    ).group_by(StockReservation.product_id).all()
    return {product_id: int(quantity) for product_id, quantity in rows}


def available_stock_by_product_ids(product_ids: Iterable[int], products_by_id=None, now=None):
    ids = [product_id for product_id in product_ids if product_id is not None]
    if not ids:
        return {}
    products_by_id = products_by_id or {
        product.id: product for product in WebProduct.query.filter(WebProduct.id.in_(ids)).all()
    }
    totals = active_reservation_totals(ids, now=now)
    return {
        product_id: max(0, int(product.stock or 0) - totals.get(product_id, 0))
        for product_id, product in products_by_id.items()
    }


def reserve_checkout_stock(items, checkout_token, expires_at=None):
    """Reserve each validated cart quantity until its Stripe session expires."""
    now = _now()
    expires_at = expires_at or now + timedelta(minutes=30)
    requested = Counter()
    display = {}
    for item in items:
        product_id = item.get('id')
        quantity = int(item.get('quantity') or 0)
        if not product_id or quantity < 1:
            raise StockUnavailableError([{'name': item.get('name') or 'Producto', 'available': 0}])
        requested[int(product_id)] += quantity
        display[int(product_id)] = item.get('name') or 'Producto'

    expire_stale_reservations(now=now)
    products = {
        product.id: product
        for product in WebProduct.query.filter(WebProduct.id.in_(requested)).with_for_update().all()
    }
    available = available_stock_by_product_ids(requested, products_by_id=products, now=now)
    unavailable = []
    for product_id, quantity in requested.items():
        product = products.get(product_id)
        sellable = product and product.active and not product.sold_out
        if not sellable or available.get(product_id, 0) < quantity:
            unavailable.append({
                'name': display[product_id],
                'available': available.get(product_id, 0) if sellable else 0,
                'requested': quantity,
            })

    if unavailable:
        db.session.rollback()
        raise StockUnavailableError(unavailable)

    for product_id, quantity in requested.items():
        db.session.add(StockReservation(
            checkout_token=checkout_token,
            product_id=product_id,
            quantity=quantity,
            status='active',
            expires_at=expires_at,
        ))
    db.session.flush()


def bind_reservation_to_session(checkout_token, stripe_session_id):
    reservations = StockReservation.query.filter_by(
        checkout_token=checkout_token,
        status='active',
    ).all()
    for reservation in reservations:
        reservation.stripe_session_id = stripe_session_id
    db.session.commit()


def release_checkout_reservation(checkout_token):
    StockReservation.query.filter_by(
        checkout_token=checkout_token,
        status='active',
    ).update({'status': 'released'}, synchronize_session=False)
    db.session.commit()


def consume_paid_reservation(checkout_token, order_id, reference):
    """Deduct reserved stock once, immediately after payment confirmation."""
    reservations = StockReservation.query.filter_by(
        checkout_token=checkout_token,
        status='active',
    ).with_for_update().all()
    if not reservations:
        return []

    product_ids = [reservation.product_id for reservation in reservations]
    products = {
        product.id: product
        for product in WebProduct.query.filter(WebProduct.id.in_(product_ids)).with_for_update().all()
    }
    movements = []
    for reservation in reservations:
        product = products.get(reservation.product_id)
        if product is None or int(product.stock or 0) < reservation.quantity:
            raise StockUnavailableError([{
                'name': product.name if product else 'Producto eliminado',
                'available': int(product.stock or 0) if product else 0,
                'requested': reservation.quantity,
            }])
        before = int(product.stock or 0)
        after = before - reservation.quantity
        product.stock = after
        product.sold_out = after == 0
        reservation.status = 'consumed'
        reservation.order_id = order_id
        reservation.consumed_at = _now()
        movement = StockMovement(
            order_id=order_id,
            product_id=product.id,
            quantity_delta=-reservation.quantity,
            reason='payment_capture',
            reference=reference,
            stock_before=before,
            stock_after=after,
        )
        db.session.add(movement)
        movements.append(movement)
    db.session.flush()
    return movements


def restock_fully_refunded_order(order_id, reference):
    """Reverse each payment capture at most once after a full Stripe refund."""
    captures = StockMovement.query.filter_by(
        order_id=order_id,
        reason='payment_capture',
    ).with_for_update().all()
    if not captures:
        return []

    capture_ids = [movement.id for movement in captures]
    already_reversed = {
        movement.reversal_of_id
        for movement in StockMovement.query.filter(
            StockMovement.reversal_of_id.in_(capture_ids)
        ).all()
    }
    products = {
        product.id: product
        for product in WebProduct.query.filter(
            WebProduct.id.in_([movement.product_id for movement in captures])
        ).with_for_update().all()
    }
    reversals = []
    for capture in captures:
        if capture.id in already_reversed:
            continue
        product = products.get(capture.product_id)
        if product is None:
            continue
        before = int(product.stock or 0)
        restored = abs(int(capture.quantity_delta))
        after = before + restored
        product.stock = after
        product.sold_out = False
        reversal = StockMovement(
            order_id=order_id,
            product_id=product.id,
            quantity_delta=restored,
            reason='refund_restock',
            reference=reference,
            reversal_of_id=capture.id,
            stock_before=before,
            stock_after=after,
        )
        db.session.add(reversal)
        reversals.append(reversal)
    db.session.flush()
    return reversals


def adjust_web_stock(product, quantity, reason, reference=None):
    """Apply an authorized catalogue correction with an immutable audit row."""
    before = int(product.stock or 0)
    after = int(quantity)
    if after < 0:
        raise ValueError('El stock web no puede ser negativo.')
    delta = after - before
    if delta == 0:
        return None
    product.stock = after
    product.sold_out = after == 0
    movement = StockMovement(
        # Catalogue corrections are not customer orders.
        order_id=None,
        product_id=product.id,
        quantity_delta=delta,
        reason=reason,
        reference=reference,
        stock_before=before,
        stock_after=after,
    )
    db.session.add(movement)
    db.session.flush()
    return movement
