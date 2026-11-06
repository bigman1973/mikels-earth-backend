# Bloque D · Referencias de precio por unidad

Preparación local autorizada por Jordi el 6 de noviembre; publicar únicamente con el catálogo y después de revisar la ficha. Fuente comercial: orden de 5 de noviembre de 2026, bloque D. No se reconstruye un historial de DB: el archivo ya existente permanece en `/home/ubuntu/estrategia-05-11/archivo-precios`.

`CampaignPack` conserva el precio completo autorizado, receta e identidad. Añade valores de presentación declarados por Jordi y `protect_campaign_pack_public_properties` los expone como `pricingData` únicamente para los seis SKU exactos. Las cadenas decimales de `unitPrice` y `usualUnitPrice`, el formato de unidad y `discountPercent` son metadatos de presentación, no precio cobrable, descuento de cupón ni precio anterior de pack.

El campo público `price` sigue procediendo del maestro persistido. Ante una discrepancia con el pack autorizado se conserva el maestro y se omite `pricingData`; nunca se aplica el precio de presentación como sustituto. PARA6 conserva su precio completo 61,70 €; 10,28 € por tarro es un redondeo aprobado y no se multiplica para obtener el total.

MIKVET500, Temprano 2026/27, no recibe estos metadatos y conserva el tratamiento existente sin referencia ni comparación. No se añade migración, seed, historial, tarea, activación ni llamada externa. Todas las barreras de catálogo, disponibilidad, cupones, cobro, impuestos y stock permanecen intactas.

`tests/test_bf26_unit_reference.py` valida metadatos, ausencia de mutación, discrepancias, exclusión de cosecha actual y los 404 de packs inactivos. La fixture de respuesta para React proviene de los handlers Flask auténticos con SQLite efímero. Sólo dentro de esa fixture se hace resoluble un maestro oculto y no disponible, nunca un producto real.
