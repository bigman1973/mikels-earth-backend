"""Thank-you page confirmation, not a signup or arbitrary Meta proxy."""
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

from src.services.vip_conversion_confirmation import confirm_vip_conversion

vip_conversion_bp = Blueprint('vip_conversion', __name__)


def _confirmation_http_status(result):
    """Only a valid candidate awaiting live evidence is retryable, not confirmed."""
    return 202 if result.get('pending') is True and result.get('confirmed') is False else 200


@vip_conversion_bp.route('/api/vip/conversion', methods=['POST'])
def confirm_conversion():
    """Only a signed receipt and literal Cookiebot marketing=true may confirm."""
    result = {'confirmed': False}
    try:
        request.max_content_length = 4096
        data = request.get_json(silent=True)
        if (not request.args and isinstance(data, dict)
                and set(data) == {'token', 'meta_marketing_consent'}
                and data.get('meta_marketing_consent') is True):
            result = confirm_vip_conversion(
                data['token'], marketing_consent=data['meta_marketing_consent'],
                user_agent=request.headers.get('User-Agent', ''))
    except (HTTPException, Exception):
        # Do not let the application's general error handler expose raw errors.
        result = {'confirmed': False}
    response = jsonify(result)
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Pragma'] = 'no-cache'
    return response, _confirmation_http_status(result)
