"""
Script de migración para añadir columnas de traducción (inglés) a web_products
y poblarlas con las traducciones iniciales.
Ejecutar una sola vez después del deploy.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import create_app
from src.models.user import db
from sqlalchemy import text

app = create_app()

# Traducciones de los productos
TRANSLATIONS = {
    'paraguayo-almibar': {
        'name_en': 'Flat Peach in Syrup',
        'description_en': 'Artisanal flat peach in light syrup, handpicked from our orchards in Lleida. A unique flavour from the heart of Catalonia.',
        'long_description_en': 'Our flat peaches are carefully selected at their optimal ripeness point and preserved in a light syrup that enhances their natural sweetness. A family recipe passed down through seven generations since 1819.'
    },
    'nectarina-almibar': {
        'name_en': 'Nectarine in Syrup',
        'description_en': 'Artisanal nectarine in light syrup, handpicked from our orchards in Lleida. Sweet, juicy and full of flavour.',
        'long_description_en': 'Our nectarines are harvested at their peak ripeness and preserved in a delicate light syrup. Each jar captures the essence of Mediterranean summer fruit, crafted with the same care our family has used for over 200 years.'
    },
    'aceite-temprano-sin-filtrar': {
        'name_en': 'Unfiltered Early Harvest Extra Virgin Olive Oil 500ml',
        'description_en': 'Premium unfiltered early harvest EVOO with intense green colour and robust flavour. Cold-pressed from green olives in Córdoba.',
        'long_description_en': 'Our early harvest olive oil is pressed from green olives collected in October-November, before full ripeness. This produces an oil with higher polyphenol content, more intense flavour and a beautiful green hue. Unfiltered to preserve all natural sediments and maximum nutritional value.'
    },
    'aceite-oliva-ecologico': {
        'name_en': 'Award-Winning Organic Extra Virgin Olive Oil',
        'description_en': 'Certified organic EVOO, internationally awarded. Cold-pressed from organically grown olives in Córdoba.',
        'long_description_en': 'Our organic extra virgin olive oil comes from certified organic olive groves in Córdoba. Produced using sustainable farming practices with zero pesticides or chemicals. Internationally awarded for its exceptional quality and flavour profile.'
    },
    'aceite-5l-caja-3': {
        'name_en': 'Extra Virgin Olive Oil 5L',
        'description_en': 'Family-size 5L container of premium EVOO. Perfect for daily cooking and generous use. Direct from producer.',
        'long_description_en': 'Our 5-litre format is designed for families and professionals who appreciate quality olive oil for everyday use. The same premium extra virgin olive oil in a convenient large format, ideal for cooking, dressing and all culinary applications.'
    },
    'pack-mermelada-aceites': {
        'name_en': 'Premium Tasting Pack',
        'description_en': 'A curated selection of our finest products. The perfect introduction to Mikel\'s Earth or an ideal gift.',
        'long_description_en': 'Our Premium Tasting Pack brings together a carefully curated selection of our best products, allowing you to experience the full range of flavours from our family workshop. An ideal gift for food lovers or the perfect way to discover our artisanal products.'
    },
    'pack-navidad-completo': {
        'name_en': 'Mikel\'s Earth Complete Pack',
        'description_en': 'The complete Mikel\'s Earth experience. All our signature products in one exclusive pack.',
        'long_description_en': 'Our Complete Pack includes every signature product from our range. From our award-winning olive oils to our artisanal fruit preserves, this pack offers the full Mikel\'s Earth experience. Perfect as a premium gift or for those who want to enjoy our entire collection.'
    },
    'pack-fruta-premium': {
        'name_en': 'Premium Fruit Pack',
        'description_en': 'A selection of our finest fruit preserves. Artisanal flat peach and nectarine in syrup.',
        'long_description_en': 'Our Premium Fruit Pack combines our signature fruit preserves: flat peach in syrup and nectarine in syrup. Both made with fruit from our own orchards in Lleida, following traditional family recipes. A taste of Mediterranean summer in every jar.'
    },
    'pack-temprano-premium': {
        'name_en': 'Premium Early Harvest Pack',
        'description_en': 'Our finest early harvest olive oils together. The ultimate olive oil experience for connoisseurs.',
        'long_description_en': 'The Premium Early Harvest Pack features our most exclusive olive oils, pressed from the first green olives of the season. These oils have the highest polyphenol content and the most intense, complex flavour profiles. For true olive oil enthusiasts.'
    },
    'pack-aceite-ecologico-premium-estuche-regalo': {
        'name_en': 'Premium Organic Olive Oil Gift Box',
        'description_en': 'Award-winning organic EVOO in an elegant gift box. The perfect present for olive oil lovers.',
        'long_description_en': 'Our Premium Organic Olive Oil comes beautifully presented in an elegant gift box, making it the perfect present for any occasion. Inside you\'ll find our internationally awarded organic extra virgin olive oil, produced from certified organic olive groves in Córdoba.'
    }
}

with app.app_context():
    # 1. Añadir columnas si no existen
    try:
        db.session.execute(text("ALTER TABLE web_products ADD COLUMN IF NOT EXISTS name_en VARCHAR(200)"))
        db.session.execute(text("ALTER TABLE web_products ADD COLUMN IF NOT EXISTS description_en TEXT"))
        db.session.execute(text("ALTER TABLE web_products ADD COLUMN IF NOT EXISTS long_description_en TEXT"))
        db.session.commit()
        print("✅ Columnas de traducción añadidas correctamente")
    except Exception as e:
        db.session.rollback()
        print(f"⚠️ Columnas ya existían o error: {e}")
    
    # 2. Poblar traducciones
    updated = 0
    for slug, translations in TRANSLATIONS.items():
        try:
            result = db.session.execute(
                text("UPDATE web_products SET name_en = :name_en, description_en = :desc_en, long_description_en = :long_desc_en WHERE slug = :slug"),
                {
                    'name_en': translations['name_en'],
                    'desc_en': translations['description_en'],
                    'long_desc_en': translations['long_description_en'],
                    'slug': slug
                }
            )
            if result.rowcount > 0:
                updated += 1
                print(f"  ✅ {slug}: traducción añadida")
            else:
                print(f"  ⚠️ {slug}: no encontrado en la DB")
        except Exception as e:
            print(f"  ❌ {slug}: error - {e}")
    
    db.session.commit()
    print(f"\n✅ Migración completada: {updated} productos traducidos")
