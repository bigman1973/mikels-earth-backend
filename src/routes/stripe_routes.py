from flask import Blueprint, request, jsonify
import stripe
import os
from datetime import datetime
import secrets
from src.services.whatsapp_service import notify_new_order, notify_new_subscription
from src.services.email_dispatcher import dispatch_order_notification, dispatch_order_confirmation, dispatch_subscription_notification, dispatch_started_checkout_event

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
        discount_code = data.get('discount_code')
        discount_amount = data.get('discount_amount', 0)
        
        # ===== VALIDACIÓN DE PRECIOS CONTRA LA BASE DE DATOS =====
        # Evita que se pueda comprar a un precio desactualizado
        from src.models.web_product import WebProduct
        price_errors = []
        for item in items:
            product_id = item.get('id')
            item_slug = item.get('slug')
            item_price = item.get('price', 0)
            
            # Buscar el producto en la DB por ID o slug
            db_product = None
            if product_id:
                db_product = WebProduct.query.get(product_id)
            if not db_product and item_slug:
                db_product = WebProduct.query.filter_by(slug=item_slug).first()
            
            if db_product:
                # Comparar precio (tolerancia de 0.01€ por redondeos)
                if abs(float(item_price) - float(db_product.price)) > 0.01:
                    price_errors.append({
                        'product': item.get('name', db_product.name),
                        'sent_price': item_price,
                        'current_price': db_product.price
                    })
                    # Corregir el precio al actual de la DB
                    item['price'] = float(db_product.price)
            # Si no se encuentra el producto, se permite (puede ser envío, etc.)
        
        if price_errors:
            # Devolver error con los precios actualizados para que el frontend actualice el carrito
            return jsonify({
                'error': 'PRICE_MISMATCH',
                'message': 'Algunos precios han cambiado. Tu carrito se ha actualizado con los precios actuales.',
                'price_updates': price_errors
            }), 409
        # ===== FIN VALIDACIÓN DE PRECIOS =====
        
        # Generate order number
        order_number = generate_order_number()
        
        # Calculate subtotal and total
        subtotal = sum(item['price'] * item['quantity'] for item in items)
        total = subtotal - discount_amount
        
        # Create line items
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
        
        session_params = {
            'payment_method_types': ['card'],
            'line_items': line_items,
            'mode': 'payment',
            'success_url': f'{frontend_url}/order-success?session_id={{CHECKOUT_SESSION_ID}}',
            'cancel_url': f'{frontend_url}/checkout?cancelled=true',
            'customer_email': customer_info['email'],
            'shipping_address_collection': {
                'allowed_countries': ['ES', 'PT', 'FR', 'DE', 'IT', 'GB', 'AT', 'BE', 'NL', 'IE']
            },
            'phone_number_collection': {
                'enabled': True
            },
            'metadata': {
                'order_number': order_number,
                'customer_name': customer_info['name'],
                'customer_phone': customer_info.get('phone', ''),
                'shipping_address': customer_info['address'],
                'shipping_city': customer_info['city'],
                'shipping_postal_code': customer_info['postal_code'],
                'shipping_country': customer_info.get('country', 'España'),
                'customer_notes': customer_info.get('notes', ''),
                'discount_code': discount_code or '',
                'discount_amount': str(discount_amount),
                'subtotal': str(subtotal),
                'total': str(total),
                'needs_invoice': str(data.get('needs_invoice', False)),
                'fiscal_name': (data.get('invoice_data') or {}).get('fiscalName', ''),
                'fiscal_nif': (data.get('invoice_data') or {}).get('nif', ''),
                'fiscal_address': (data.get('invoice_data') or {}).get('fiscalAddress', ''),
                'fiscal_city': (data.get('invoice_data') or {}).get('fiscalCity', ''),
                'fiscal_postal_code': (data.get('invoice_data') or {}).get('fiscalPostalCode', '')
            }
        }
        
        # Apply discount if exists
        if discount_code and discount_amount > 0:
            # Create a coupon in Stripe for this specific checkout
            coupon = stripe.Coupon.create(
                amount_off=int(discount_amount * 100),  # Convert to cents
                currency='eur',
                duration='once',
                name=discount_code
            )
            session_params['discounts'] = [{'coupon': coupon.id}]
        
        session = stripe.checkout.Session.create(**session_params)
        
        # Track "Started Checkout" en Klaviyo para el Flow de carrito abandonado
        try:
            checkout_data = {
                'customer_email': customer_info['email'],
                'customer_name': customer_info.get('name', ''),
                'customer_phone': customer_info.get('phone', ''),
                'items': items,
                'subtotal': subtotal,
                'total': total,
                'discount_code': discount_code or '',
                'discount_amount': discount_amount,
                'order_number': order_number,
                'checkout_url': f"{frontend_url}/checkout"
            }
            dispatch_started_checkout_event(
                email=checkout_data['customer_email'],
                customer_name=checkout_data['customer_name'],
                items=checkout_data['items'],
                total=checkout_data['total'],
                checkout_url=checkout_data['checkout_url'],
                items_html='',
                cart_token=checkout_data.get('order_number', '')
            )
        except Exception as checkout_err:
            print(f"\u26a0\ufe0f Error tracking started checkout: {checkout_err}")
            # No bloquear el checkout si falla el tracking
        
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
    except (stripe._error.SignatureVerificationError, Exception) as e:
        # Invalid signature or missing header
        print(f"Webhook signature verification failed: {str(e)}")
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
                    # amount_total es el total de la línea (precio × cantidad)
                    # Guardamos el precio unitario para que el desglose sea correcto
                    unit_price = (item.amount_total / 100) / item.quantity if item.quantity else item.amount_total / 100
                    items.append({
                        'name': item.description,
                        'quantity': item.quantity,
                        'price': round(unit_price, 2)
                    })
                
                # Extraer dirección de envío de Stripe shipping_details (prioridad)
                # Esto funciona cuando el cliente usa Link o rellena en Stripe Checkout
                stripe_shipping = session.get('shipping_details') or {}
                stripe_shipping_address = stripe_shipping.get('address') or {}
                stripe_shipping_name = stripe_shipping.get('name', '')
                
                # Extraer teléfono de customer_details (recopilado por phone_number_collection)
                customer_details = session.get('customer_details') or {}
                stripe_phone = customer_details.get('phone', '') or ''
                
                # Prioridad: datos de Stripe > metadata del frontend
                shipping_line = stripe_shipping_address.get('line1', '') or session['metadata'].get('shipping_address', '')
                shipping_line2 = stripe_shipping_address.get('line2', '') or ''
                shipping_city = stripe_shipping_address.get('city', '') or session['metadata'].get('shipping_city', '')
                shipping_postal = stripe_shipping_address.get('postal_code', '') or session['metadata'].get('shipping_postal_code', '')
                shipping_country = stripe_shipping_address.get('country', '') or session['metadata'].get('shipping_country', 'España')
                shipping_state = stripe_shipping_address.get('state', '') or ''
                customer_phone = stripe_phone or session['metadata'].get('customer_phone', '')
                customer_name = stripe_shipping_name or session['metadata'].get('customer_name', 'N/A')
                
                # Construir dirección completa
                address_parts = [
                    shipping_line,
                    shipping_line2,
                    shipping_city,
                    shipping_state,
                    shipping_postal,
                    shipping_country
                ]
                full_address = ', '.join([part for part in address_parts if part])
                
                # Extraer datos adicionales de metadata
                subtotal_str = session['metadata'].get('subtotal', '0')
                discount_code = session['metadata'].get('discount_code', '')
                discount_amount_str = session['metadata'].get('discount_amount', '0')
                
                try:
                    subtotal = float(subtotal_str) if subtotal_str else 0
                except (ValueError, TypeError):
                    subtotal = 0
                
                try:
                    discount_amount = float(discount_amount_str) if discount_amount_str else 0
                except (ValueError, TypeError):
                    discount_amount = 0
                
                total = session['amount_total'] / 100 if session.get('amount_total') else 0
                
                order_data = {
                    'order_number': order_number,
                    'customer_name': customer_name,
                    'customer_email': customer_details.get('email', '') or session.get('customer_email', 'N/A'),
                    'customer_phone': customer_phone or 'No proporcionado',
                    'items': items,
                    'subtotal': subtotal if subtotal else total,
                    'total': total,
                    'shipping_address': full_address if full_address else 'No especificada',
                    'shipping_address_line': shipping_line,
                    'shipping_city': shipping_city,
                    'shipping_postal_code': shipping_postal,
                    'shipping_country': shipping_country,
                    'discount_code': discount_code,
                    'discount_amount': discount_amount,
                    'customer_notes': session['metadata'].get('customer_notes', ''),
                    'stripe_checkout_session_id': session['id'],
                    'stripe_payment_intent_id': session.get('payment_intent', '')
                }
                
                # Extraer datos de facturación de metadata
                needs_invoice_str = session['metadata'].get('needs_invoice', 'False')
                needs_invoice = needs_invoice_str.lower() == 'true'
                fiscal_name = session['metadata'].get('fiscal_name', '')
                fiscal_nif = session['metadata'].get('fiscal_nif', '')
                fiscal_address = session['metadata'].get('fiscal_address', '')
                fiscal_city = session['metadata'].get('fiscal_city', '')
                fiscal_postal_code = session['metadata'].get('fiscal_postal_code', '')
                
                # Añadir datos de facturación al order_data para notificaciones
                order_data['needs_invoice'] = needs_invoice
                order_data['invoice_data'] = {
                    'fiscalName': fiscal_name,
                    'nif': fiscal_nif,
                    'fiscalAddress': fiscal_address,
                    'fiscalCity': fiscal_city,
                    'fiscalPostalCode': fiscal_postal_code
                } if needs_invoice else None
                
                # Guardar pedido en la base de datos
                try:
                    from src.models.order import Order
                    from src.models.user import db
                    new_order = Order(
                        order_number=order_number,
                        customer_email=order_data['customer_email'],
                        customer_name=order_data['customer_name'],
                        customer_phone=order_data['customer_phone'],
                        shipping_address=shipping_line,
                        shipping_city=shipping_city,
                        shipping_postal_code=shipping_postal,
                        shipping_country=shipping_country,
                        items=items,
                        subtotal=order_data['subtotal'],
                        shipping_cost=0 if total >= 40 else 4.95,
                        total=total,
                        stripe_payment_intent_id=order_data.get('stripe_payment_intent_id', ''),
                        stripe_checkout_session_id=session['id'],
                        payment_status='paid',
                        order_status='processing',
                        customer_notes=order_data.get('customer_notes', ''),
                        needs_invoice=needs_invoice,
                        fiscal_name=fiscal_name if needs_invoice else None,
                        fiscal_nif=fiscal_nif if needs_invoice else None,
                        fiscal_address=fiscal_address if needs_invoice else None,
                        fiscal_city=fiscal_city if needs_invoice else None,
                        fiscal_postal_code=fiscal_postal_code if needs_invoice else None
                    )
                    new_order.paid_at = datetime.utcnow()
                    db.session.add(new_order)
                    db.session.commit()
                    print(f"✅ Order {order_number} saved to database (invoice: {needs_invoice})")
                except Exception as db_error:
                    print(f"⚠️ Error saving order to database: {str(db_error)}")
                    # No fallar el webhook por error de BBDD
                
                # Enviar notificaciones por WhatsApp
                notify_new_order(order_data)
                
                # Enviar notificaciones por Email (Klaviyo + Brevo fallback)
                dispatch_order_notification(order_data)
                dispatch_order_confirmation(order_data)
                
                # Generar cupón de 10% para próxima compra y enviar evento a Klaviyo
                try:
                    from src.services.email_dispatcher import dispatch_post_purchase_event
                    dispatch_post_purchase_event(order_data)
                except Exception as coupon_post_err:
                    print(f"⚠️ Error dispatching post-purchase event: {coupon_post_err}")
                
                # Marcar cupón como usado si se usó uno (todos los tipos)
                discount_code = session['metadata'].get('discount_code', '').strip()
                print(f"📌 [WEBHOOK] Order {order_number} - discount_code in metadata: '{discount_code}'")
                if discount_code:
                    try:
                        # Marcar cupón como usado usando el modelo Coupon (PostgreSQL)
                        from src.models.coupon import Coupon
                        from src.models.user import db as coupon_db
                        coupon_obj = Coupon.query.filter(
                            coupon_db.func.lower(Coupon.code) == discount_code.lower().strip()
                        ).first()
                        if coupon_obj:
                            coupon_obj.mark_as_used()
                            print(f"✅ [WEBHOOK] Coupon '{discount_code}' marked as used for {order_data['customer_email']} (id={coupon_obj.id}, active={coupon_obj.active}, used={coupon_obj.used})")
                        else:
                            print(f"⚠️ [WEBHOOK] Coupon '{discount_code}' NOT FOUND in DB - checking all similar codes...")
                            # Intentar búsqueda más flexible (por si hay diferencias de formato)
                            similar = Coupon.query.filter(
                                Coupon.code.ilike(f"%{discount_code}%")
                            ).all()
                            if similar:
                                print(f"   Similar codes found: {[c.code for c in similar]}")
                                # Usar el primer match exacto (case-insensitive)
                                for s in similar:
                                    if s.code.lower().strip() == discount_code.lower().strip():
                                        s.mark_as_used()
                                        print(f"   ✅ Matched and marked: {s.code}")
                                        break
                    except Exception as coupon_error:
                        print(f"❌ [WEBHOOK] Error marking coupon as used: {str(coupon_error)}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"ℹ️ [WEBHOOK] Order {order_number} - No discount code used")
                
                # Marcar carritos abandonados de este email como convertidos
                try:
                    from src.models.abandoned_cart import AbandonedCart
                    from src.models.user import db
                    carts = AbandonedCart.query.filter_by(
                        email=order_data['customer_email'],
                        converted=False
                    ).all()
                    for cart in carts:
                        cart.converted = True
                    if carts:
                        db.session.commit()
                        print(f"✅ {len(carts)} abandoned cart(s) marked as converted for {order_data['customer_email']}")
                except Exception as cart_err:
                    print(f"⚠️ Error marking abandoned carts as converted: {cart_err}")
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
                
                # Enviar notificaciones por WhatsApp y Email (Klaviyo + Brevo fallback)
                notify_new_subscription(subscription_data)
                dispatch_subscription_notification(subscription_data)
            except Exception as e:
                print(f"Error sending subscription notification: {str(e)}")
    
    elif event['type'] == 'invoice.payment_succeeded':
        # Recurring payment succeeded
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        print(f"Subscription {subscription_id} payment succeeded")
        # TODO: Send invoice email
    
    elif event['type'] == 'charge.refunded':
        # Pago reembolsado (total o parcial)
        charge = event['data']['object']
        payment_intent_id = charge.get('payment_intent', '')
        amount_refunded = charge.get('amount_refunded', 0) / 100  # cents to euros
        amount_total = charge.get('amount', 0) / 100
        is_full_refund = (amount_refunded >= amount_total)
        
        print(f"Refund detected: PI={payment_intent_id}, refunded={amount_refunded}€, total={amount_total}€, full={is_full_refund}")
        
        try:
            from src.models.order import Order
            from src.models.user import db
            
            # Buscar pedido por payment_intent_id
            order = Order.query.filter_by(stripe_payment_intent_id=payment_intent_id).first()
            
            if order:
                if is_full_refund:
                    order.payment_status = 'refunded'
                    order.order_status = 'cancelled'
                else:
                    order.payment_status = 'partially_refunded'
                
                order.admin_notes = (order.admin_notes or '') + f'\nReembolso Stripe: {amount_refunded}€ de {amount_total}€ ({"total" if is_full_refund else "parcial"}) - {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}'
                db.session.commit()
                print(f"✅ Order {order.order_number} marked as {'refunded' if is_full_refund else 'partially_refunded'}")
            else:
                print(f"⚠️ No order found for payment_intent: {payment_intent_id}")
        except Exception as refund_err:
            print(f"⚠️ Error processing refund webhook: {refund_err}")
    
    elif event['type'] == 'payment_intent.canceled':
        # Pago cancelado antes de completarse
        pi = event['data']['object']
        payment_intent_id = pi.get('id', '')
        
        print(f"Payment intent cancelled: {payment_intent_id}")
        
        try:
            from src.models.order import Order
            from src.models.user import db
            
            order = Order.query.filter_by(stripe_payment_intent_id=payment_intent_id).first()
            if order:
                order.payment_status = 'cancelled'
                order.order_status = 'cancelled'
                order.admin_notes = (order.admin_notes or '') + f'\nPago cancelado en Stripe - {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}'
                db.session.commit()
                print(f"✅ Order {order.order_number} marked as cancelled")
        except Exception as cancel_err:
            print(f"⚠️ Error processing cancellation webhook: {cancel_err}")
    
    elif event['type'] == 'customer.subscription.deleted':
        # Subscription cancelled
        subscription_obj = event['data']['object']
        subscription_id = subscription_obj['id']
        print(f"Subscription {subscription_id} cancelled")
        # TODO: Send cancellation confirmation email
    
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
