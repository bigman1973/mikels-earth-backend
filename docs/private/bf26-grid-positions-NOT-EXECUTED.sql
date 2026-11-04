-- BORRADOR PRIVADO / NO EJECUTADO. Requiere aprobación separada para cargar datos.
-- NO es migración de arranque; NO importar desde main.py ni añadir al deploy.
-- Usa columna YA existente y nullable: web_products.display_order.
-- Único campo escrito: display_order. No toca active, visible_in_store,
-- in_promotion, price, stock, impuestos, transporte ni timestamps.
-- Incluye MIKVET500 a 7 SOLO cuando se autorice ejecutar este script.
-- Operador: exportar primero id,sku,display_order para rollback y verificar
-- inactividad/ocultación de los seis ANTES y DESPUÉS (fuera de este borrador).

BEGIN;
DO $$
BEGIN
  IF (SELECT count(*) FROM web_products WHERE sku IN (
      'BF26-TEMP6','BF26-ECO6','BF26-PARA3','BF26-TEMP12',
      'BF26-PARA6','BF26-5L3','MIKVET500')) <> 7 THEN
    RAISE EXCEPTION 'Faltan filas aprobadas; no cargar ninguna posición';
  END IF;
END $$;

-- Desplaza solo posiciones positivas del resto a 100+ manteniendo orden
-- relativo anterior (posición,id). NULL/0/negativas quedan intactas y detrás.
-- Necesario: filas existentes con posición 1..7 no deben intercalarse.
WITH rest AS (
  SELECT id, 99 + row_number() OVER (ORDER BY display_order, id) AS new_position
  FROM web_products
  WHERE display_order > 0 AND sku IS DISTINCT FROM 'BF26-TEMP6'
    AND sku IS DISTINCT FROM 'BF26-ECO6' AND sku IS DISTINCT FROM 'BF26-PARA3'
    AND sku IS DISTINCT FROM 'BF26-TEMP12' AND sku IS DISTINCT FROM 'BF26-PARA6'
    AND sku IS DISTINCT FROM 'BF26-5L3' AND sku IS DISTINCT FROM 'MIKVET500'
)
UPDATE web_products p SET display_order = rest.new_position::integer
FROM rest WHERE p.id = rest.id;

UPDATE web_products p SET display_order = desired.position
FROM (VALUES ('BF26-TEMP6',1),('BF26-ECO6',2),('BF26-PARA3',3),
             ('BF26-TEMP12',4),('BF26-PARA6',5),('BF26-5L3',6),
             ('MIKVET500',7)) AS desired(sku,position)
WHERE p.sku = desired.sku;
COMMIT;

-- Rollback: restaurar SOLO display_order desde exportación previa. NO ejecutar
-- asignaciones manuales estimadas ni restaurar precios/stock/estados.
