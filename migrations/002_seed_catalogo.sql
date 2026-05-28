-- Catálogo real de masvidaconsciente (tomado del catálogo PDF del cliente).
-- Todo libre de gluten, lácteos, azúcar, maíz, trigo, avena, cebada y centeno.
-- Precios en USD. NULL = "precio a consultar".

INSERT INTO productos (nombre, categoria, descripcion, precio, presentacion) VALUES
-- ── PANADERÍA ──────────────────────────────────────────────
('Pan de Sándwich', 'panaderia', 'Masa madre enriquecida, harina de yuca o batata desalmidonada, harina de plátano verde', 18.00, '18 rebanadas'),
('Pan de Hamburguesa', 'panaderia', 'Harina de yuca o plátano, almidón de batata, masa madre, psyllium, goma guar, linaza', 18.00, '7 unidades'),
('Pan Keto', 'panaderia', 'Harina de almendra y coco, aceite de aguacate, huevos, goma guar, psyllium, linaza', 25.00, '18 rebanadas'),
('Tortillas de Plátano o Yuca', 'panaderia', 'Masa de plátano o harina de yuca, chía, linaza, sal marina, huevo', 6.00, '6 unidades'),
('Empanadas', 'panaderia', 'Harina de yuca o masa de plátano, trigo sarraceno, almidón de sagú y yuca, relleno de carne mechada', 8.00, '4 unidades'),
('Empanadas Keto', 'panaderia', 'Harina de almendras, psyllium, manteca, queso de cabra o búfala. Relleno de pollo, carne o queso', 8.00, '4 unidades'),

-- ── DULCERÍA ───────────────────────────────────────────────
('Galletas New York', 'dulceria', 'Harina de almendra y coco, leche de almendra. Toppings: chocolate, limón, pistacho, canela, merey', 12.00, '4 unidades'),
('Mini New York', 'dulceria', 'Versión mini de las galletas New York', 12.00, '10 unidades'),
('Quesillo', 'dulceria', 'Azúcar de coco, huevos, leche y leche condensada de coco, vainilla natural', 8.00, '200g'),
('Ponquesitos', 'dulceria', 'Harina de almendras o merey, azúcar de coco. Toppings: limón, almendra, chocolate, pistacho, arequipe', 14.00, '4 unidades'),
('Ponqué / Torta', 'dulceria', 'Harina de almendra y coco, azúcar de coco. Sabores: cambur, limón, almendras, chocolate, piña, pistacho', NULL, '250g / 500g / 1kg'),

-- ── CONGELADOS ─────────────────────────────────────────────
('Tortillas Taco', 'congelados', 'Harina de almendra y coco, psyllium, linaza, sal marina, huevo', 8.00, '6 unidades'),
('Empanadas Horneadas Salteñas', 'congelados', 'Harina de yuca y garbanzo activado, masa madre, psyllium. Con proteína de tu preferencia', 12.00, '4 unidades'),
('Tequeños', 'congelados', 'Harina de yuca y garbanzo activado, masa madre. Con queso de coco, búfala o cabra', 10.00, 'a partir de 10 unidades'),
('Wafles Salados', 'congelados', 'Harina de yuca desalmidonada, levadura nutricional, hígado de res deshidratado, huevos', 8.00, '6 unidades'),
('Wafles Dulces', 'congelados', 'Harina de plátano verde y yuca, plátano maduro, aceite de coco, huevos', 8.00, '6 unidades'),
('Untable de Chocolate', 'congelados', 'Chocolate Dubai y almendras', 8.00, NULL),

-- ── ARTESANAL ──────────────────────────────────────────────
('Miel Pura', 'artesanal', 'Concentración al 100% de origen natural', 20.00, '1kg'),
('Caldo de Huesos', 'artesanal', 'Codo, rodilla, hueso rojo, cola y fémur de res. 18 horas de cocción lenta', 8.00, '500ml'),
('Arepas Andinas', 'artesanal', 'Harina de yuca sin almidón, harina de garbanzo activado y germinado, psyllium, linaza', 8.00, '6 unidades'),
('Kombucha', 'artesanal', 'Fermentado probiótico. Sabores: parchita, limón, naranja', 4.00, '350ml'),
('Kombucha', 'artesanal', 'Fermentado probiótico. Sabores: parchita, limón, naranja', 7.00, '700ml'),
('Chucrut', 'artesanal', 'Repollo orgánico, sal marina, fermentación de 90 días', 6.00, '200g'),
('Kéfir de Leche', 'artesanal', 'Bebida probiótica vegana, libre de lácteos', 4.00, '350ml'),
('Kéfir de Leche', 'artesanal', 'Bebida probiótica vegana, libre de lácteos', 8.00, '1000ml'),
('Yogurt Kéfirado', 'artesanal', 'Superalimento simbiótico, probióticos del kéfir', 7.00, '350ml'),

-- ── HARINAS ────────────────────────────────────────────────
('Premezclas', 'harinas', 'Todouso, garbanzo, pan en casa, harina de cambur, plátano o yuca', NULL, '500gr'),
('Harina de Almendra', 'harinas', 'Activada y sin conservantes', NULL, '1kg'),
('Harina de Merey', 'harinas', 'Activada y sin conservantes', NULL, '1kg');

-- Configuración inicial del negocio
INSERT INTO configuracion (clave, valor) VALUES
('negocio_nombre', 'masvidaconsciente'),
('negocio_ubicacion', 'Cabudare, Venezuela'),
('negocio_pago', 'Pago Móvil'),
('negocio_instagram', '@masvidaconsciente')
ON CONFLICT (clave) DO NOTHING;
