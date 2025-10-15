from flask import Blueprint, request, jsonify
import stripe
import os
from datetime import datetime
import secrets

from ..models.order import db, Order, Subscription

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
        
        # Create order in database
        order = Order(
            order_number=generate_order_number(),
            customer_email=customer_info['email'],
            customer_name=customer_info['name'],
            customer_phone=customer_info.get('phone', ''),
            shipping_address=customer_info['address'],
            shipping_city=customer_info['city'],
            shipping_postal_code=customer_info['postal_code'],
            shipping_country=customer_info.get('country', 'España'),
            items=items,
            subtotal=sum(item['price'] * item['quantity'] for item in items),
            shipping_cost=0.0,  # TODO: Calculate shipping
            total=sum(item['price'] * item['quantity'] for item in items),
            payment_status='pending',
            order_status='processing',
            customer_notes=customer_info.get('notes', '')
        )
        
        db.session.add(order)
        db.session.commit()
        
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
                'order_id': order.id,
                'order_number': order.order_number
            }
        )
        
        # Update order with session ID
        order.stripe_checkout_session_id = session.id
        db.session.commit()
        
        return jsonify({
            'sessionId': session.id,
            'url': session.url,
            'order_number': order.order_number
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
        
        # Map frequency to Stripe interval
        interval_mapping = {
            'weekly': {'interval': 'week', 'interval_count': 1},
            'biweekly': {'interval': 'week', 'interval_count': 2},
            'monthly': {'interval': 'month', 'interval_count': 1},
            'bimonthly': {'interval': 'month', 'interval_count': 2}
        }
        
        if frequency not in interval_mapping:
            return jsonify({'error': 'Invalid subscription frequency'}), 400
        
        interval_config = interval_mapping[frequency]
        
        # Create or retrieve Stripe Price
        # In production, you should create these prices in Stripe Dashboard
        # For now, we'll create them dynamically
        price = stripe.Price.create(
            unit_amount=int(item['price'] * 100),
            currency='eur',
            recurring=interval_config,
            product_data={
                'name': f"{item['name']} - Suscripción {frequency}",
            },
        )
        
        # Create subscription record
        subscription = Subscription(
            subscription_number=generate_subscription_number(),
            customer_email=customer_info['email'],
            customer_name=customer_info['name'],
            product_id=item['id'],
            product_name=item['name'],
            product_slug=item['slug'],
            quantity=item['quantity'],
            unit_price=item['price'],
            frequency=frequency,
            status='pending',
            stripe_price_id=price.id
        )
        
        db.session.add(subscription)
        db.session.commit()
        
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
                'subscription_id': subscription.id,
                'subscription_number': subscription.subscription_number
            }
        )
        
        # Update subscription with session ID
        subscription.stripe_subscription_id = session.subscription
        db.session.commit()
        
        return jsonify({
            'sessionId': session.id,
            'url': session.url,
            'subscription_number': subscription.subscription_number
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
            order_id = session['metadata'].get('order_id')
            if order_id:
                order = Order.query.get(order_id)
                if order:
                    order.payment_status = 'paid'
                    order.stripe_payment_intent_id = session.get('payment_intent')
                    order.paid_at = datetime.utcnow()
                    db.session.commit()
                    
                    # TODO: Send confirmation email via Brevo
                    print(f"Order {order.order_number} paid successfully")
        
        elif session['mode'] == 'subscription':
            # Subscription created
            subscription_id = session['metadata'].get('subscription_id')
            if subscription_id:
                subscription = Subscription.query.get(subscription_id)
                if subscription:
                    subscription.status = 'active'
                    subscription.stripe_subscription_id = session.get('subscription')
                    subscription.stripe_customer_id = session.get('customer')
                    db.session.commit()
                    
                    # TODO: Send subscription confirmation email via Brevo
                    print(f"Subscription {subscription.subscription_number} activated")
    
    elif event['type'] == 'invoice.payment_succeeded':
        # Recurring payment succeeded
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        
        if subscription_id:
            subscription = Subscription.query.filter_by(
                stripe_subscription_id=subscription_id
            ).first()
            
            if subscription:
                # TODO: Create order for this subscription cycle
                # TODO: Send invoice email via Brevo
                print(f"Subscription {subscription.subscription_number} payment succeeded")
    
    elif event['type'] == 'customer.subscription.deleted':
        # Subscription cancelled
        subscription_obj = event['data']['object']
        subscription_id = subscription_obj['id']
        
        subscription = Subscription.query.filter_by(
            stripe_subscription_id=subscription_id
        ).first()
        
        if subscription:
            subscription.status = 'cancelled'
            subscription.cancelled_at = datetime.utcnow()
            db.session.commit()
            
            # TODO: Send cancellation confirmation email via Brevo
            print(f"Subscription {subscription.subscription_number} cancelled")
    
    return jsonify({'status': 'success'})


@stripe_bp.route('/session-status/<session_id>', methods=['GET'])
def get_session_status(session_id):
    """Get checkout session status"""
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        
        return jsonify({
            'status': session.status,
            'payment_status': session.payment_status,
            'customer_email': session.customer_details.email if session.customer_details else None
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

