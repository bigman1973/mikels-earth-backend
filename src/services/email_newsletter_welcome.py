"""
Email de bienvenida para suscriptores del newsletter
"""
import os
import requests
from datetime import datetime


def send_newsletter_welcome_email(email):
    """
    Envía email de bienvenida con la historia de Jordi y código de descuento BIENVENIDA10
    """
    subject = "🌿 Bienvenido a la familia Mikel's Earth + Tu regalo (10% descuento)"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ 
                font-family: 'Georgia', 'Times New Roman', serif; 
                line-height: 1.8; 
                color: #333; 
                margin: 0;
                padding: 0;
                background-color: #f5f5f5;
            }}
            .container {{ 
                max-width: 650px; 
                margin: 0 auto; 
                background-color: #ffffff;
            }}
            .header {{ 
                background-color: #2d5016; 
                color: white; 
                padding: 40px 30px; 
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 28px;
                font-weight: normal;
            }}
            .content {{ 
                padding: 40px 30px;
                background-color: #ffffff;
            }}
            .content p {{
                margin: 0 0 20px 0;
                font-size: 16px;
                line-height: 1.8;
            }}
            .highlight {{ 
                background-color: #f0f7e9; 
                padding: 25px; 
                border-left: 4px solid #2d5016; 
                margin: 30px 0;
            }}
            .highlight p {{
                margin: 5px 0;
            }}
            .section-title {{
                font-size: 22px;
                color: #2d5016;
                margin: 40px 0 20px 0;
                font-weight: bold;
            }}
            .image-container {{
                text-align: center;
                margin: 30px 0;
            }}
            .image-container img {{
                max-width: 100%;
                height: auto;
                border-radius: 8px;
            }}
            .image-caption {{
                font-size: 14px;
                color: #666;
                font-style: italic;
                margin-top: 10px;
            }}
            .products {{
                margin: 30px 0;
            }}
            .product-item {{
                margin: 20px 0;
                padding: 15px 0;
                border-bottom: 1px solid #e0e0e0;
            }}
            .product-item:last-child {{
                border-bottom: none;
            }}
            .product-title {{
                font-size: 18px;
                color: #2d5016;
                font-weight: bold;
                margin-bottom: 8px;
            }}
            .discount-box {{
                background: linear-gradient(135deg, #2d5016 0%, #4a7c28 100%);
                color: white;
                padding: 30px;
                text-align: center;
                margin: 40px 0;
                border-radius: 8px;
            }}
            .discount-box h2 {{
                margin: 0 0 15px 0;
                font-size: 24px;
            }}
            .discount-code {{
                background-color: white;
                color: #2d5016;
                padding: 15px 30px;
                font-size: 24px;
                font-weight: bold;
                letter-spacing: 2px;
                border-radius: 5px;
                display: inline-block;
                margin: 15px 0;
            }}
            .btn {{ 
                display: inline-block; 
                padding: 15px 40px; 
                background-color: #2d5016; 
                color: white !important; 
                text-decoration: none; 
                border-radius: 5px; 
                margin: 20px 0;
                font-size: 16px;
                font-weight: bold;
            }}
            .btn:hover {{
                background-color: #4a7c28;
            }}
            .newsletter-content {{
                background-color: #fafafa;
                padding: 25px;
                border-radius: 8px;
                margin: 30px 0;
            }}
            .newsletter-content ul {{
                list-style: none;
                padding: 0;
                margin: 15px 0;
            }}
            .newsletter-content li {{
                padding: 10px 0;
                border-bottom: 1px solid #e0e0e0;
            }}
            .newsletter-content li:last-child {{
                border-bottom: none;
            }}
            .footer {{ 
                background-color: #f9f9f9; 
                padding: 30px; 
                text-align: center; 
                font-size: 14px; 
                color: #666;
                border-top: 1px solid #e0e0e0;
            }}
            .footer p {{
                margin: 5px 0;
            }}
            .contact-info {{
                background-color: #f0f7e9;
                padding: 20px;
                border-radius: 8px;
                margin: 30px 0;
            }}
            .contact-info p {{
                margin: 8px 0;
            }}
            .social-impact {{
                background-color: #fff9e6;
                padding: 25px;
                border-left: 4px solid #f4a261;
                margin: 30px 0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🌿 Bienvenido a la familia Mikel's Earth</h1>
            </div>
            
            <div class="content">
                <p>Hola,</p>
                
                <p>Soy Jordi.</p>
                
                <p>Cada mañana, cuando veo a mis hijos Roger y Ares desayunar en nuestra cocina, siento algo especial.</p>
                
                <p>Roger, que es más como yo, se coge el pan que sabe a pan de la panadería de Alcarràs (que también tiene más de tres generaciones) y le pone nuestro aceite Mikel's, untándolo con cuidado, como he hecho yo toda mi vida.</p>
                
                <p>Ares abre dos frascos: el de paraguayo y el de nectarina. Y empieza a mezclarlos, creando sus propias recetas.</p>
                
                <p><strong>Sonrío.</strong></p>
                
                <div class="highlight">
                    <p>Porque en ese momento siento dos cosas:</p>
                    <p><strong>1. Están disfrutando.</strong> De verdad. No están comiendo por comer. Están saboreando, experimentando, creando.</p>
                    <p><strong>2. Sé que lo que comen es lo mejor.</strong> No es una frase de marketing. Es una certeza. Porque yo mismo he cuidado esa tierra, he recolectado esa fruta, he supervisado cada frasco.</p>
                </div>
                
                <p><strong>Y eso me reconforta.</strong></p>
                
                <p>Me reconforta saber que tanto Roger como Ares tendrán la energía necesaria, a base de alimentos de los que sé exactamente de dónde vienen, para disfrutar de un día intenso en la escuela.</p>
                
                <p><strong>Y eso es exactamente lo que quiero compartir contigo.</strong></p>
                
                <!-- Sección: Qué recibirás -->
                <h2 class="section-title">¿Qué recibirás en tu bandeja de entrada?</h2>
                
                <p>Esto no es un newsletter típico de ofertas y promociones.</p>
                
                <p><strong>Es un diario de nuestra vida haciendo productos de primera calidad.</strong></p>
                
                <div class="newsletter-content">
                    <p>Cada día (o cada semana, según lo que esté pasando) recibirás:</p>
                    <ul>
                        <li>📖 <strong>Historias reales de nuestra vida</strong> - Cómo es un día en el campo, en el obrador, en nuestra cocina. Sin filtros, sin marketing. Solo la verdad de hacer las cosas bien.</li>
                        <li>🍑 <strong>Qué pasa cada temporada</strong> - La cosecha del paraguayo, la recolección de las aceitunas, los momentos buenos (y los no tan buenos) de trabajar con la naturaleza.</li>
                        <li>👨‍🌾 <strong>Cómo hacemos los productos</strong> - Los tiempos, los procesos, las decisiones que tomamos. Por qué un frasco tarda lo que tarda. Por qué no cortamos caminos.</li>
                        <li>👶 <strong>Anécdotas de Roger y Ares</strong> - Sus experimentos culinarios (como las mezclas de Ares), sus preguntas, sus descubrimientos. Porque los niños ven las cosas de forma diferente.</li>
                        <li>🎁 <strong>Ofertas especiales</strong> - Sí, también. Pero solo cuando realmente tengan sentido. No spam. No descuentos falsos. Solo oportunidades reales.</li>
                        <li>📚 <strong>Recetas y consejos</strong> - Cómo aprovechamos nosotros nuestros productos. Qué hace Araceli en la cocina. Qué combinaciones funcionan mejor.</li>
                    </ul>
                </div>
                
                <p><strong>En resumen: Recibirás las cosas que pasan cuando haces productos de primera calidad.</strong></p>
                
                <p>Las buenas, las difíciles, las divertidas, las emotivas.</p>
                
                <p>Porque creemos que si vas a comprar nuestros productos, mereces saber quiénes somos de verdad.</p>
                
                <!-- Sección: Historia de Mikel's -->
                <h2 class="section-title">Cómo nació Mikel's Earth (la historia real)</h2>
                
                <p>Llevo más de 30 años exportando el mejor aceite del territorio a Asia.</p>
                
                <p>Hace 25 años me casé con Araceli, y nos dieron dos hijos: Roger y Ares.</p>
                
                <p>Mira por dónde, la familia de mi esposa tenía tierras, pero no comercializaban ni la fruta ni las aceitunas. Utilizaban los métodos que te ofrece el sistema: intermediarios y empresas enfocadas al volumen que, no les queda otra, desmejorar el producto.</p>
                
                <p><strong>Entonces hablé con la familia.</strong></p>
                
                <p>Les dije: "Necesitamos que más niños puedan tener en sus mesas lo que comen Roger y Ares."</p>
                
                <p><strong>Y así fue como surgió el Mikel's que vosotros conocéis.</strong></p>
                
                <!-- Imagen de los abuelos -->
                <div class="image-container">
                    <img src="https://files.manuscdn.com/user_upload_by_module/session_file/310519663056520872/qsMsFHjqVJtqBTbT.jpg" alt="Los abuelos de Roger y Ares preparando almíbar artesanalmente" />
                    <p class="image-caption">Los abuelos de Roger y Ares preparando almíbar de forma artesanal, con el mismo cuidado que ponemos hoy</p>
                </div>
                
                <!-- Sección: Productos -->
                <h2 class="section-title">¿Qué vas a encontrar en nuestra tienda?</h2>
                
                <p>No productos de supermercado. No fruta que ha pasado por intermediarios.</p>
                
                <p>Vas a encontrar lo mismo que Roger y Ares comen cada día:</p>
                
                <div class="products">
                    <div class="product-item">
                        <div class="product-title">🍑 Paraguayo en Almíbar</div>
                        <p>El mismo que Ares mezcla con la nectarina para crear sus propias recetas. Fruta real, recolectada por nosotros, con los tiempos necesarios para que la fruta sepa a fruta y conserve su textura. Porque los niños no son tontos.</p>
                    </div>
                    
                    <div class="product-item">
                        <div class="product-title">🍊 Nectarina en Almíbar</div>
                        <p>La compañera perfecta del paraguayo en las creaciones de Ares. Dulce, aromática, con la textura que hace que los niños repitan.</p>
                    </div>
                    
                    <div class="product-item">
                        <div class="product-title">🫒 Aceite de Oliva Virgen Extra Temprano</div>
                        <p>El mismo que Roger unta en su pan cada mañana, como yo he hecho toda mi vida. Intenso, picante, con carácter. El mejor aceite del territorio que llevo 30 años exportando a Asia.</p>
                    </div>
                    
                    <div class="product-item">
                        <div class="product-title">🎁 Pack Degustación</div>
                        <p>Si no sabes por dónde empezar, este pack te lleva directamente a nuestra mesa familiar. Es como si vinieras a desayunar con nosotros.</p>
                    </div>
                </div>
                
                <!-- Código de descuento -->
                <div class="discount-box">
                    <h2>🎁 Tu regalo de bienvenida</h2>
                    <p>Como bienvenida a nuestra familia, quiero regalarte un <strong>10% de descuento</strong> en tu primera compra.</p>
                    <div class="discount-code">BIENVENIDA10</div>
                    <p style="font-size: 14px; margin-top: 15px;">Copia este código y úsalo en tu primera compra</p>
                    <a href="https://www.mikels.es/tienda" class="btn">Quiero que mis hijos disfruten como Roger y Ares</a>
                </div>
                
                <!-- Qué ponemos en cada frasco -->
                <div class="highlight">
                    <p><strong>Sabemos exactamente qué ponemos en cada frasco:</strong></p>
                    <p>✨ Fruta en su punto perfecto de maduración</p>
                    <p>⏰ Los tiempos necesarios para que la fruta sepa a fruta</p>
                    <p>🍑 La textura que hace que los niños repitan (y creen sus propias recetas)</p>
                    <p>❤️ Y sí, también amor. Mucho amor.</p>
                </div>
                
                <p><strong>Porque no hay nada mejor que ver a tus hijos disfrutar de lo que comen, sabiendo que es lo mejor.</strong></p>
                
                <!-- Compromiso social -->
                <div class="social-impact">
                    <h3 style="margin-top: 0; color: #d68910;">Una cosa más que debes saber...</h3>
                    <p>Parte de lo que ganamos va a causas que nos importan de verdad:</p>
                    <p><strong>• Ileris:</strong> Un centro de personas especiales que nos ayudan a empaquetar cada frasco con dedicación y cariño.</p>
                    <p><strong>• Fundación Agonlinhossouyetokandji</strong> en Benín: Ayudando a niños huérfanos y ancianos a tener una vida mejor.</p>
                    <p style="margin-bottom: 0;">Porque creemos que el éxito solo tiene sentido si se comparte.</p>
                </div>
                
                <!-- Contacto -->
                <h2 class="section-title">¿Tienes dudas? Escríbeme.</h2>
                
                <p>De verdad. Estoy aquí para ayudarte a elegir lo que mejor se adapte a ti y a tu familia.</p>
                
                <div class="contact-info">
                    <p>📧 Email: <a href="mailto:info@mikels.es" style="color: #2d5016; text-decoration: none; font-weight: bold;">info@mikels.es</a></p>
                    <p>📱 WhatsApp: <a href="https://wa.me/436789070062172" style="color: #2d5016; text-decoration: none; font-weight: bold;">+43 6789 0700 62172</a></p>
                    <p>🌐 Web: <a href="https://www.mikels.es" style="color: #2d5016; text-decoration: none; font-weight: bold;">www.mikels.es</a></p>
                </div>
                
                <p style="font-size: 14px; color: #666; margin-top: 30px;"><em>El código BIENVENIDA10 es válido solo para tu primera compra y no es acumulable con otras promociones.</em></p>
                
                <p style="margin-top: 40px;">Un abrazo desde Lleida,</p>
                <p style="color: #2d5016; font-weight: bold; font-size: 18px;">Jordi Giró</p>
                <p style="color: #666; font-style: italic;">Fundador de Mikel's Earth<br>Del campo a tu mesa</p>
                
                <div style="background-color: #f0f7e9; padding: 20px; border-radius: 8px; margin-top: 30px;">
                    <p style="margin: 0 0 10px 0;"><strong>P.D.:</strong> Roger me acaba de preguntar si hoy hay paraguayo para merendar. Ares ha dicho que ella quiere mezclar paraguayo con melocotón esta vez. Ya sabes las respuestas. 😊</p>
                    <p style="margin: 0;"><strong>P.D. 2:</strong> Si quieres contestar a estos emails, hazlo. Leo todos los mensajes. De verdad.</p>
                </div>
            </div>
            
            <div class="footer">
                <p><strong>Mikel's Earth</strong> - Del campo a tu mesa desde 1819</p>
                <p>Carrer Cardenal Cisneros, 10 - Lleida, España</p>
                <p style="margin-top: 15px; font-size: 12px;">Has recibido este correo porque te has suscrito a nuestro newsletter en www.mikels.es</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return False
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\\n', '').replace('\\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Jordi - Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": email}],
                "subject": subject,
                "htmlContent": html_content
            }
        )
        
        if response.status_code == 201:
            print(f"✅ Email de bienvenida enviado exitosamente a: {email}")
            return True
        else:
            print(f"❌ Error enviando email: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"Error sending newsletter welcome email: {str(e)}")
        return False

