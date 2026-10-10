"""Public Meta catalog feed derived from the live web catalogue.

The feed intentionally reads only active, public WebProduct records.  Price,
availability, SKU and images therefore stay aligned with the actual checkout
catalogue without manual spreadsheet uploads.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import requests
from flask import Blueprint, Response, abort, send_file
from PIL import Image

from src.models.web_product import WebProduct

meta_catalog_bp = Blueprint("meta_catalog", __name__)

STOREFRONT_URL = "https://www.mikels.es"
API_URL = "https://api.mikels.es"
ALLOWED_IMAGE_HOSTS = {"www.mikels.es", "res.cloudinary.com"}
IMAGE_TIMEOUT_SECONDS = 20


def _public_products():
    return WebProduct.query.filter_by(
        active=True,
        visible_in_store=True,
    ).order_by(WebProduct.display_order, WebProduct.id).all()


def _main_image_source(product: WebProduct) -> str:
    return str(product.image or (product.images or [""])[0] or "").strip()


def _absolute_image_source(source: str) -> str:
    if source.startswith("/"):
        return f"{STOREFRONT_URL}{source}"
    return source


def _feed_image_url(product: WebProduct) -> str:
    return f"{API_URL}/api/meta-catalog/images/{quote(str(product.sku or ''), safe='')}.jpg"


def _price_string(value) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{amount:.2f} EUR"


def _availability(product: WebProduct) -> str:
    return "in stock" if not product.sold_out and int(product.stock or 0) > 0 else "out of stock"


def _description(product: WebProduct) -> str:
    return " ".join(str(product.description or product.long_description or product.name).split())


def _product_type(product: WebProduct) -> str:
    category = str(product.category or "Productos")
    if category.lower() == "aceites":
        return "Alimentación > Aceite de oliva"
    if category.lower() == "conservas":
        return "Alimentación > Conservas"
    return f"Alimentación > {category}"


@meta_catalog_bp.route("/meta-catalog/products.csv", methods=["GET"])
def get_meta_catalog_feed():
    """Return the public CSV feed for Meta Commerce Manager."""
    rows = []
    for product in _public_products():
        sku = str(product.sku or "").strip()
        source = _main_image_source(product)
        if not sku or not source:
            continue
        rows.append({
            "id": sku,
            "title": str(product.name or sku),
            "description": _description(product),
            "availability": _availability(product),
            "condition": "new",
            "price": _price_string(product.price),
            "link": f"{STOREFRONT_URL}/producto/{quote(str(product.slug), safe='')}",
            "image_link": _feed_image_url(product),
            "brand": "Mikel's Fruit",
            "product_type": _product_type(product),
        })

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=[
        "id", "title", "description", "availability", "condition", "price",
        "link", "image_link", "brand", "product_type",
    ])
    writer.writeheader()
    writer.writerows(rows)
    response = Response(stream.getvalue(), mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = "inline; filename=meta-mikels-fruit-products.csv"
    response.headers["Cache-Control"] = "public, max-age=900"
    return response


@meta_catalog_bp.route("/meta-catalog/images/<sku>.jpg", methods=["GET"])
def get_meta_catalog_image(sku: str):
    """Serve the catalogue primary image as a Meta-compatible JPEG.

    Existing source files are never changed.  The deterministic conversion only
    ensures a stable JPEG endpoint for images that were originally WebP.
    """
    product = WebProduct.query.filter_by(
        sku=sku,
        active=True,
        visible_in_store=True,
    ).first()
    if not product:
        abort(404)

    source = _absolute_image_source(_main_image_source(product))
    if not source:
        abort(404)

    from urllib.parse import urlparse
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        abort(404)

    try:
        upstream = requests.get(source, timeout=IMAGE_TIMEOUT_SECONDS, allow_redirects=False)
        upstream.raise_for_status()
        with Image.open(io.BytesIO(upstream.content)) as image:
            rgb_image = image.convert("RGB")
            output = io.BytesIO()
            rgb_image.save(output, format="JPEG", quality=90, optimize=True)
    except (requests.RequestException, OSError, ValueError):
        abort(502)

    output.seek(0)
    response = send_file(
        output,
        mimetype="image/jpeg",
        download_name=f"{sku}.jpg",
        max_age=86400,
    )
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response
