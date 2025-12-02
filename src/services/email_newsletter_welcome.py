"""
Email de bienvenida para suscriptores del newsletter
"""
import os
import requests
from datetime import datetime


def send_newsletter_welcome_email(email, coupon_code="BIENVENIDA10"):
    """
    Envía email de bienvenida con la historia de Jordi y código de descuento único
    Args:
        email: Email del suscriptor
        coupon_code: Código de cupón único generado (default: BIENVENIDA10)
    """
    subject = "🌿 Bienvenido a la familia Mikel's Earth + Tu regalo (10% descuento)"
    
    # HTML optimizado para compatibilidad con todos los clientes de email
    html_content = f"""
    <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
    <html xmlns="http://www.w3.org/1999/xhtml">
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
        <title>Bienvenido a Mikel's Earth</title>
    </head>
    <body style="margin: 0; padding: 0; font-family: Georgia, 'Times New Roman', serif; background-color: #f5f5f5;">
        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #f5f5f5;">
            <tr>
                <td align="center" style="padding: 20px 0;">
                    <table border="0" cellpadding="0" cellspacing="0" width="650" style="background-color: #ffffff; max-width: 650px;">
                        
                        <!-- Header -->
                        <tr>
                            <td align="center" style="background-color: #2d5016; padding: 40px 30px;">
                                <h1 style="color: #ffffff; font-size: 28px; font-weight: normal; margin: 0;">🌿 Bienvenido a la familia Mikel's Earth</h1>
                            </td>
                        </tr>
                        
                        <!-- Content -->
                        <tr>
                            <td style="padding: 40px 30px; color: #333333; font-size: 16px; line-height: 1.8;">
                                
                                <p style="margin: 0 0 20px 0;">Hola,</p>
                                
                                <p style="margin: 0 0 20px 0;">Soy Jordi.</p>
                                
                                <p style="margin: 0 0 20px 0;">Cada mañana, cuando veo a mis hijos Roger y Ares desayunar en nuestra cocina, siento algo especial.</p>
                                
                                <p style="margin: 0 0 20px 0;">Roger, que es más como yo, se coge el pan que sabe a pan de la panadería de Alcarràs (que también tiene más de tres generaciones) y le pone nuestro aceite Mikel's, untándolo con cuidado, como he hecho yo toda mi vida.</p>
                                
                                <p style="margin: 0 0 20px 0;">Ares abre dos frascos: el de paraguayo y el de nectarina. Y empieza a mezclarlos, creando sus propias recetas.</p>
                                
                                <p style="margin: 0 0 20px 0;"><strong>Sonrío.</strong></p>
                                
                                <!-- Highlight Box -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0;">
                                    <tr>
                                        <td style="background-color: #f0f7e9; border-left: 4px solid #2d5016; padding: 25px;">
                                            <p style="margin: 5px 0;">Porque en ese momento siento dos cosas:</p>
                                            <p style="margin: 5px 0;"><strong>1. Están disfrutando.</strong> De verdad. No están comiendo por comer. Están saboreando, experimentando, creando.</p>
                                            <p style="margin: 5px 0;"><strong>2. Sé que lo que comen es lo mejor.</strong> No es una frase de marketing. Es una certeza. Porque yo mismo he cuidado esa tierra, he recolectado esa fruta, he supervisado cada frasco.</p>
                                        </td>
                                    </tr>
                                </table>
                                
                                <p style="margin: 0 0 20px 0;"><strong>Y eso me reconforta.</strong></p>
                                
                                <p style="margin: 0 0 20px 0;">Me reconforta saber que tanto Roger como Ares tendrán la energía necesaria, a base de alimentos de los que sé exactamente de dónde vienen, para disfrutar de un día intenso en la escuela.</p>
                                
                                <p style="margin: 0 0 20px 0;"><strong>Y eso es exactamente lo que quiero compartir contigo.</strong></p>
                                
                                <!-- Section Title -->
                                <h2 style="font-size: 22px; color: #2d5016; margin: 40px 0 20px 0; font-weight: bold;">¿Qué recibirás en tu bandeja de entrada?</h2>
                                
                                <p style="margin: 0 0 20px 0;">Esto no es un newsletter típico de ofertas y promociones.</p>
                                
                                <p style="margin: 0 0 20px 0;"><strong>Es un diario de nuestra vida haciendo productos de primera calidad.</strong></p>
                                
                                <!-- Newsletter Content Box -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0;">
                                    <tr>
                                        <td style="background-color: #fafafa; padding: 25px;">
                                            <p style="margin: 0 0 15px 0;">Cada día (o cada semana, según lo que esté pasando) recibirás:</p>
                                            <p style="margin: 10px 0; padding-bottom: 10px; border-bottom: 1px solid #e0e0e0;">📖 <strong>Historias reales de nuestra vida</strong> - Cómo es un día en el campo, en el obrador, en nuestra cocina. Sin filtros, sin marketing. Solo la verdad de hacer las cosas bien.</p>
                                            <p style="margin: 10px 0; padding-bottom: 10px; border-bottom: 1px solid #e0e0e0;">🍑 <strong>Qué pasa cada temporada</strong> - La cosecha del paraguayo, la recolección de las aceitunas, los momentos buenos (y los no tan buenos) de trabajar con la naturaleza.</p>
                                            <p style="margin: 10px 0; padding-bottom: 10px; border-bottom: 1px solid #e0e0e0;">👨‍🌾 <strong>Cómo hacemos los productos</strong> - Los tiempos, los procesos, las decisiones que tomamos. Por qué un frasco tarda lo que tarda. Por qué no cortamos caminos.</p>
                                            <p style="margin: 10px 0; padding-bottom: 10px; border-bottom: 1px solid #e0e0e0;">👶 <strong>Anécdotas de Roger y Ares</strong> - Sus experimentos culinarios (como las mezclas de Ares), sus preguntas, sus descubrimientos. Porque los niños ven las cosas de forma diferente.</p>
                                            <p style="margin: 10px 0; padding-bottom: 10px; border-bottom: 1px solid #e0e0e0;">🎁 <strong>Ofertas especiales</strong> - Sí, también. Pero solo cuando realmente tengan sentido. No spam. No descuentos falsos. Solo oportunidades reales.</p>
                                            <p style="margin: 10px 0;">📚 <strong>Recetas y consejos</strong> - Cómo aprovechamos nosotros nuestros productos. Qué hace Araceli en la cocina. Qué combinaciones funcionan mejor.</p>
                                        </td>
                                    </tr>
                                </table>
                                
                                <p style="margin: 0 0 20px 0;"><strong>En resumen: Recibirás las cosas que pasan cuando haces productos de primera calidad.</strong></p>
                                
                                <p style="margin: 0 0 20px 0;">Las buenas, las difíciles, las divertidas, las emotivas.</p>
                                
                                <p style="margin: 0 0 20px 0;">Porque creemos que si vas a comprar nuestros productos, mereces saber quiénes somos de verdad.</p>
                                
                                <!-- Section Title -->
                                <h2 style="font-size: 22px; color: #2d5016; margin: 40px 0 20px 0; font-weight: bold;">Cómo nació Mikel's Earth (la historia real)</h2>
                                
                                <p style="margin: 0 0 20px 0;">Llevo más de 30 años exportando el mejor aceite del territorio a Asia.</p>
                                
                                <p style="margin: 0 0 20px 0;">Hace 25 años me casé con Araceli, y nos dieron dos hijos: Roger y Ares.</p>
                                
                                <p style="margin: 0 0 20px 0;">Mira por dónde, la familia de mi esposa tenía tierras, pero no comercializaban ni la fruta ni las aceitunas. Utilizaban los métodos que te ofrece el sistema: intermediarios y empresas enfocadas al volumen que, no les queda otra, desmejorar el producto.</p>
                                
                                <p style="margin: 0 0 20px 0;"><strong>Entonces hablé con la familia.</strong></p>
                                
                                <p style="margin: 0 0 20px 0;">Les dije: "Necesitamos que más niños puedan tener en sus mesas lo que comen Roger y Ares."</p>
                                
                                <p style="margin: 0 0 20px 0;"><strong>Y así fue como surgió el Mikel's que vosotros conocéis.</strong></p>
                                
                                <!-- Image -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0;">
                                    <tr>
                                        <td align="center">
                                            <img src="https://files.manuscdn.com/user_upload_by_module/session_file/310519663056520872/qsMsFHjqVJtqBTbT.jpg" alt="Los abuelos de Roger y Ares preparando almíbar artesanalmente" width="590" style="display: block; max-width: 100%; height: auto;" />
                                            <p style="font-size: 14px; color: #666; font-style: italic; margin-top: 10px;">Los abuelos de Roger y Ares preparando almíbar de forma artesanal, con el mismo cuidado que ponemos hoy</p>
                                        </td>
                                    </tr>
                                </table>
                                
                                <!-- Section Title -->
                                <h2 style="font-size: 22px; color: #2d5016; margin: 40px 0 20px 0; font-weight: bold;">¿Qué vas a encontrar en nuestra tienda?</h2>
                                
                                <p style="margin: 0 0 20px 0;">No productos de supermercado. No fruta que ha pasado por intermediarios.</p>
                                
                                <p style="margin: 0 0 20px 0;">Vas a encontrar lo mismo que Roger y Ares comen cada día:</p>
                                
                                <!-- Products -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0;">
                                    <tr>
                                        <td style="padding: 15px 0; border-bottom: 1px solid #e0e0e0;">
                                            <p style="font-size: 18px; color: #2d5016; font-weight: bold; margin: 0 0 8px 0;">🍑 Paraguayo en Almíbar</p>
                                            <p style="margin: 0;">El mismo que Ares mezcla con la nectarina para crear sus propias recetas. Fruta real, recolectada por nosotros, con los tiempos necesarios para que la fruta sepa a fruta y conserve su textura. Porque los niños no son tontos.</p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 15px 0; border-bottom: 1px solid #e0e0e0;">
                                            <p style="font-size: 18px; color: #2d5016; font-weight: bold; margin: 0 0 8px 0;">🍊 Nectarina en Almíbar</p>
                                            <p style="margin: 0;">La compañera perfecta del paraguayo en las creaciones de Ares. Dulce, aromática, con la textura que hace que los niños repitan.</p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 15px 0; border-bottom: 1px solid #e0e0e0;">
                                            <p style="font-size: 18px; color: #2d5016; font-weight: bold; margin: 0 0 8px 0;">🫒 Aceite de Oliva Virgen Extra Temprano</p>
                                            <p style="margin: 0;">El mismo que Roger unta en su pan cada mañana, como yo he hecho toda mi vida. Intenso, picante, con carácter. El mejor aceite del territorio que llevo 30 años exportando a Asia.</p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 15px 0;">
                                            <p style="font-size: 18px; color: #2d5016; font-weight: bold; margin: 0 0 8px 0;">🎁 Pack Degustación</p>
                                            <p style="margin: 0;">Si no sabes por dónde empezar, este pack te lleva directamente a nuestra mesa familiar. Es como si vinieras a desayunar con nosotros.</p>
                                        </td>
                                    </tr>
                                </table>
                                
                                <!-- Discount Box -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 40px 0;">
                                    <tr>
                                        <td align="center" style="background-color: #2d5016; padding: 30px;">
                                            <h2 style="color: #ffffff; font-size: 24px; margin: 0 0 15px 0;">🎁 Tu regalo de bienvenida</h2>
                                            <p style="color: #ffffff; margin: 0 0 15px 0;">Como bienvenida a nuestra familia, quiero regalarte un <strong>10% de descuento</strong> en tu primera compra.</p>
                                            <table border="0" cellpadding="0" cellspacing="0" style="margin: 15px 0;">
                                                <tr>
                                                    <td align="center" style="background-color: #ffffff; padding: 15px 30px;">
                                                        <span style="color: #2d5016; font-size: 24px; font-weight: bold; letter-spacing: 2px;">{coupon_code}</span>
                                                    </td>
                                                </tr>
                                            </table>
                                            <p style="color: #ffffff; font-size: 14px; margin: 15px 0;">Copia este código y úsalo en tu primera compra</p>
                                            <table border="0" cellpadding="0" cellspacing="0" style="margin: 20px 0;">
                                                <tr>
                                                    <td align="center" style="background-color: #4a7c28; padding: 15px 40px;">
                                                        <a href="https://www.mikels.es/tienda" style="color: #ffffff; text-decoration: none; font-size: 16px; font-weight: bold; display: block;">Quiero que mis hijos disfruten como Roger y Ares</a>
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                    </tr>
                                </table>
                                
                                <!-- Highlight Box -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0;">
                                    <tr>
                                        <td style="background-color: #f0f7e9; border-left: 4px solid #2d5016; padding: 25px;">
                                            <p style="margin: 5px 0;"><strong>Sabemos exactamente qué ponemos en cada frasco:</strong></p>
                                            <p style="margin: 5px 0;">✨ Fruta en su punto perfecto de maduración</p>
                                            <p style="margin: 5px 0;">⏰ Los tiempos necesarios para que la fruta sepa a fruta</p>
                                            <p style="margin: 5px 0;">🍑 La textura que hace que los niños repitan (y creen sus propias recetas)</p>
                                            <p style="margin: 5px 0;">❤️ Y sí, también amor. Mucho amor.</p>
                                        </td>
                                    </tr>
                                </table>
                                
                                <p style="margin: 0 0 20px 0;"><strong>Porque no hay nada mejor que ver a tus hijos disfrutar de lo que comen, sabiendo que es lo mejor.</strong></p>
                                
                                <!-- Social Impact Box -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0;">
                                    <tr>
                                        <td style="background-color: #fff9e6; border-left: 4px solid #f4a261; padding: 25px;">
                                            <h3 style="margin: 0 0 15px 0; color: #d68910;">Una cosa más que debes saber...</h3>
                                            <p style="margin: 0 0 10px 0;">Parte de lo que ganamos va a causas que nos importan de verdad:</p>
                                            <p style="margin: 0 0 10px 0;"><strong>• Ileris:</strong> Un centro de personas especiales que nos ayudan a empaquetar cada frasco con dedicación y cariño.</p>
                                            <p style="margin: 0;"><strong>• Fundación Agonlinhossouyetokandji</strong> en Benín: Ayudando a niños huérfanos y ancianos a tener una vida mejor.</p>
                                            <p style="margin: 10px 0 0 0;">Porque creemos que el éxito solo tiene sentido si se comparte.</p>
                                        </td>
                                    </tr>
                                </table>
                                
                                <!-- Section Title -->
                                <h2 style="font-size: 22px; color: #2d5016; margin: 40px 0 20px 0; font-weight: bold;">¿Tienes dudas? Escríbeme.</h2>
                                
                                <p style="margin: 0 0 20px 0;">De verdad. Estoy aquí para ayudarte a elegir lo que mejor se adapte a ti y a tu familia.</p>
                                
                                <!-- Contact Info Box -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0;">
                                    <tr>
                                        <td style="background-color: #f0f7e9; padding: 20px;">
                                            <p style="margin: 8px 0;">📧 Email: <a href="mailto:info@mikels.es" style="color: #2d5016; text-decoration: none; font-weight: bold;">info@mikels.es</a></p>
                                            <p style="margin: 8px 0;">📱 WhatsApp: <a href="https://wa.me/436789070062172" style="color: #2d5016; text-decoration: none; font-weight: bold;">+43 6789 0700 62172</a></p>
                                            <p style="margin: 8px 0;">🌐 Web: <a href="https://www.mikels.es" style="color: #2d5016; text-decoration: none; font-weight: bold;">www.mikels.es</a></p>
                                        </td>
                                    </tr>
                                </table>
                                
                                <p style="font-size: 14px; color: #666; margin: 30px 0 0 0;"><em>El código {coupon_code} es válido solo para tu primera compra y no es acumulable con otras promociones.</em></p>
                                
                                <p style="margin: 40px 0 0 0;">Un abrazo desde Lleida,</p>
                                <p style="color: #2d5016; font-weight: bold; font-size: 18px; margin: 10px 0;">Jordi Giró</p>
                                <p style="color: #666; font-style: italic; margin: 0;">Fundador de Mikel's Earth<br/>Del campo a tu mesa</p>
                                
                                <!-- P.D. Box -->
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 30px 0 0 0;">
                                    <tr>
                                        <td style="background-color: #f0f7e9; padding: 20px;">
                                            <p style="margin: 0 0 10px 0;"><strong>P.D.:</strong> Roger me acaba de preguntar si hoy hay paraguayo para merendar. Ares ha dicho que ella quiere mezclar paraguayo con melocotón esta vez. Ya sabes las respuestas. 😊</p>
                                            <p style="margin: 0;"><strong>P.D. 2:</strong> Si quieres contestar a estos emails, hazlo. Leo todos los mensajes. De verdad.</p>
                                        </td>
                                    </tr>
                                </table>
                                
                            </td>
                        </tr>
                        
                        <!-- Footer -->
                        <tr>
                            <td align="center" style="background-color: #f9f9f9; padding: 30px; border-top: 1px solid #e0e0e0;">
                                <p style="margin: 5px 0; font-size: 14px; color: #666;"><strong>Mikel's Earth</strong> - Del campo a tu mesa desde 1819</p>
                                <p style="margin: 5px 0; font-size: 14px; color: #666;">Carrer Cardenal Cisneros, 10 - Lleida, España</p>
                                <p style="margin: 15px 0 0 0; font-size: 12px; color: #666;">Has recibido este correo porque te has suscrito a nuestro newsletter en www.mikels.es</p>
                            </td>
                        </tr>
                        
                    </table>
                </td>
            </tr>
        </table>
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
