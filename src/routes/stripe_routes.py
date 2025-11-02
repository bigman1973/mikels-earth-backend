from flask import Blueprint, request, jsonify
import stripe
import os
from datetime import datetime
import secrets
from src.services.whatsapp_service import notify_new_order, notify_new_subscription
from src.services.email_service import notify_new_order_email, notify_new_subscription_email, send_customer_order_confirmation

stripe_bp = Blueprint('stripe', __name__, url_prefix='/api/stripe')

# Configurar Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

def generate_order_number():
    """Generate unique order number"""
    timestamp = datetime.now().strftime('%Y%m%d')
    random_part = secrets.token_hex(4).upper()
    return f'MKL-{timestamp}-{random_part}'

def generate_subscription_number():
    """Generate unique subscription number"""
    timestamp = datetime.now().strftime('%Y%m%d')
    random_part = secrets.token_hex(4).upper()
    return f'SUB-{timestamp}-{random_part}'

@stripe_bp.route('/config', methods=['GET'])
def get_config():
    """Get Stripe publishable key"""
    return jsonify({
        'publishableKey': os.getenv('STRIPE_PUBLISHABLE_KEY')
    })

@stripe_bp.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create Stripe Checkout session for one-time purchase"""
    try:
        data = request.json
        
        # Validate required fields
        if not data.get('items') or not data.get('customer_info'):
            return jsonify({'error': 'Missing required fields'}), 400
        
        items = data['items']
        customer_info = data['customer_info']
        
        # Generate order number
        order_number = generate_order_number()
        
        # Calculate total
        line_items = []
        for item in items:
            line_items.append({
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': item['name'],
                        'description': f"{item.get('weight', '')}",
                    },
                    'unit_amount': int(item['price'] * 100),  # Convert to cents
                },
                'quantity': item['quantity'],
            })
        
        # Create Stripe Checkout Session
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
        
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            success_url=f'{frontend_url}/order-success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{frontend_url}/checkout?cancelled=true',
            customer_email=customer_info['email'],
            metadata={
                'order_number': order_number,
                'customer_name': customer_info['name'],
                'customer_phone': customer_info.get('phone', ''),
                'shipping_address': customer_info['address'],
                'shipping_city': customer_info['city'],
                'shipping_postal_code': customer_info['postal_code'],
                'shipping_country': customer_info.get('country', 'España'),
                'customer_notes': customer_info.get('notes', '')
            }
        )
        
        return jsonify({
            'sessionId': session.id,
            'url': session.url,
            'order_number': order_number
        })
        
    except Exception as e:
        print(f"Error creating checkout session: {str(e)}")
        return jsonify({'error': str(e)}), 500


@stripe_bp.route('/create-subscription-checkout', methods=['POST'])
def create_subscription_checkout():
    """Create Stripe Checkout session for subscription"""
    try:
        data = request.json
        
        # Validate required fields
        if not data.get('item') or not data.get('customer_info'):
            return jsonify({'error': 'Missing required fields'}), 400
        
        item = data['item']
        customer_info = data['customer_info']
        frequency = item.get('subscription_frequency')
        
        if not frequency:
            return jsonify({'error': 'Subscription frequency is required'}), 400
        
        # Generate subscription number
        subscription_number = generate_subscription_number()
        
        # Map frequency to Stripe interval
        interval_mapping = {
            'weekly': {'interval': 'week', 'interval_count': 1},
            'biweekly': {'interval': 'week', 'interval_count': 2},
            'monthly': {'interval': 'month', 'interval_count': 1},
            'quarterly': {'interval': 'month', 'interval_count': 3},
            'semiannual': {'interval': 'month', 'interval_count': 6}
        }
        
        if frequency not in interval_mapping:
            return jsonify({'error': 'Invalid subscription frequency'}), 400
        
        interval_config = interval_mapping[frequency]
        
        # Create Stripe Price for subscription
        price = stripe.Price.create(
            unit_amount=int(item['price'] * 100),
            currency='eur',
            recurring=interval_config,
            product_data={
                'name': f"{item['name']} - Suscripción {frequency}",
            },
        )
        
        # Create Stripe Checkout Session for subscription
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
        
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price.id,
                'quantity': item['quantity'],
            }],
            mode='subscription',
            success_url=f'{frontend_url}/subscription-success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{frontend_url}/checkout?cancelled=true',
            customer_email=customer_info['email'],
            metadata={
                'subscription_number': subscription_number,
                'product_id': item['id'],
                'product_name': item['name'],
                'product_slug': item['slug'],
                'frequency': frequency,
                'customer_name': customer_info['name']
            }
        )
        
        return jsonify({
            'sessionId': session.id,
            'url': session.url,
            'subscription_number': subscription_number
        })
        
    except Exception as e:
        print(f"Error creating subscription checkout: {str(e)}")
        return jsonify({'error': str(e)}), 500


@stripe_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError as e:
        # Invalid payload
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        
        if session['mode'] == 'payment':
            # One-time payment completed
            order_number = session['metadata'].get('order_number')
            print(f"Order {order_number} paid successfully")
            
            # Obtener detalles del pedido
            try:
                line_items = stripe.checkout.Session.list_line_items(session['id'], limit=100)
                items = []
                for item in line_items.data:
                    items.append({
                        'name': item.description,
                        'quantity': item.quantity,
                        'price': item.amount_total / 100
                    })
                
                # Construir dirección completa
                address_parts = [
                    session['metadata'].get('shipping_address', ''),
                    session['metadata'].get('shipping_city', ''),
                    session['metadata'].get('shipping_postal_code', ''),
                    session['metadata'].get('shipping_country', '')
                ]
                full_address = ', '.join([part for part in address_parts if part])
                
                order_data = {
                    'order_number': order_number,
                    'customer_name': session['metadata'].get('customer_name', 'N/A'),
                    'customer_email': session.get('customer_details', {}).get('email', 'N/A'),
                    'customer_phone': session['metadata'].get('customer_phone', 'N/A'),
                    'items': items,
                    'total': session['amount_total'] / 100 if session.get('amount_total') else 0,
                    'shipping_address': full_address if full_address else 'No especificada'
                }
                
                # Enviar notificaciones por WhatsApp y Email
                notify_new_order(order_data)
                notify_new_order_email(order_data)
                
                # Enviar email de confirmación al cliente
                send_customer_order_confirmation(order_data)
            except Exception as e:
                print(f"Error sending order notification: {str(e)}")
        
        elif session['mode'] == 'subscription':
            # Subscription created
            subscription_number = session['metadata'].get('subscription_number')
            print(f"Subscription {subscription_number} activated")
            
            # Obtener detalles de la suscripción
            try:
                subscription_data = {
                    'subscription_number': subscription_number,
                    'customer_name': session['metadata'].get('customer_name', 'N/A'),
                    'customer_email': session.get('customer_details', {}).get('email', 'N/A'),
                    'product_name': session['metadata'].get('product_name', 'N/A'),
                    'frequency': session['metadata'].get('frequency', 'N/A'),
                    'price': session['amount_total'] / 100 if session.get('amount_total') else 0
                }
                
                # Enviar notificaciones por WhatsApp y Email
                notify_new_subscription(subscription_data)
                notify_new_subscription_email(subscription_data)
            except Exception as e:
                print(f"Error sending subscription notification: {str(e)}")
    
    elif event['type'] == 'invoice.payment_succeeded':
        # Recurring payment succeeded
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        print(f"Subscription {subscription_id} payment succeeded")
        # TODO: Send invoice email via Brevo
    
    elif event['type'] == 'customer.subscription.deleted':
        # Subscription cancelled
        subscription_obj = event['data']['object']
        subscription_id = subscription_obj['id']
        print(f"Subscription {subscription_id} cancelled")
        # TODO: Send cancellation confirmation email via Brevo
    
    return jsonify({'status': 'success'})


@stripe_bp.route('/session-status/<session_id>', methods=['GET'])
def get_session_status(session_id):
    """Get checkout session status"""
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        
        return jsonify({
            'status': session.status,
            'payment_status': session.payment_status,
            'customer_email': session.customer_details.email if session.customer_details else None,
            'metadata': session.metadata
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

