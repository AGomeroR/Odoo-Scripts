#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sincronizar categorías internas con categorías eCommerce
Hace que las categorías de producto (product.category) se llamen exactamente igual que
las categorías eCommerce (product.public.category) y asigna a cada producto publicado
la categoría eCommerce equivalente a su categoría interna.

Funcionalidad:
1. Empareja categorías internas y eCommerce cuya ruta completa coincide sin tener en cuenta
   mayúsculas ni tildes, y renombra la interna con el nombre exacto de la eCommerce
2. Renombra las categorías internas con nombre distinto definidas en RENOMBRAR
3. Crea en eCommerce las categorías definidas en CREAR_EN_ECOMMERCE
4. A cada producto publicado le añade la categoría eCommerce equivalente a su categoría interna
   y le quita solo las categorías eCommerce que son padres de esa (p. ej. 'Guitarras y Bajos').
   El resto de categorías eCommerce del producto (Outlet, Ofertas...) se mantienen
5. Muestra las categorías que quedan solo en uno de los dos árboles

Uso:
    python Sincronizar_categorias.py        # Producción (pide confirmación)
    python Sincronizar_categorias.py --stg  # Staging

Nota:
- Script independiente, no forma parte del pipeline
- Requiere .env y comun.py
"""

import os
import sys
from datetime import datetime

from comun import OdooClient, configurar_log, log_message, normalizar

# Log propio
configurar_log("sincronizar_categorias_log.txt")

BATCH_SIZE = 200

# Categorías internas con distinto nombre que su equivalente eCommerce: {ruta interna: ruta eCommerce}
RENOMBRAR = {
    "Cuerda Frotada / Cuerdas para instrumentos de cuerda frotada": "Cuerda Frotada / Cuerdas",
    "Guitarras y Bajos / Cuerdas": "Guitarras y Bajos / Cuerdas de Guitarra y Bajo",
    "Pianos y Teclados / Accesorios & banquetas": "Pianos y Teclados / Accesorios para Piano y Teclado",
}

# Categorías internas de primer nivel que se crean también en eCommerce
CREAR_EN_ECOMMERCE = ["Iluminación"]


def leer_categorias(manager):
    """
    Lee los dos árboles de categorías.

    Returns:
        tuple: (internas, publicas) como {id: {'name', 'parent_id', 'path'}}
    """
    internas = {
        c['id']: {'name': c['name'], 'parent_id': c['parent_id'][0] if c['parent_id'] else None, 'path': c['complete_name']}
        for c in manager.execute('product.category', 'search_read', [[]], {'fields': ['id', 'name', 'parent_id', 'complete_name']})
    }
    publicas = {
        c['id']: {'name': c['name'], 'parent_id': c['parent_id'][0] if c['parent_id'] else None, 'path': c['display_name']}
        for c in manager.execute('product.public.category', 'search_read', [[]], {'fields': ['id', 'name', 'parent_id', 'display_name']})
    }
    return internas, publicas


def profundidad(path):
    return len(path.split(' / '))


def renombrar_categorias(manager, internas, publicas):
    """Renombra las categorías internas con el nombre exacto de su equivalente eCommerce"""
    publicas_por_ruta = {normalizar(c['path']): c for c in publicas.values()}
    renombrar = {normalizar(k): normalizar(v) for k, v in RENOMBRAR.items()}
    renombradas = 0

    for cat_id, cat in sorted(internas.items(), key=lambda x: profundidad(x[1]['path'])):
        ruta = normalizar(cat['path'])
        publica = publicas_por_ruta.get(renombrar.get(ruta, ruta))
        if not publica or publica['name'] == cat['name']:
            continue

        manager.execute('product.category', 'write', [[cat_id], {'name': publica['name']}])
        renombradas += 1
        log_message(f"   ✏️ '{cat['path']}' → nombre '{publica['name']}' (ID {cat_id})")

    return renombradas


def crear_categorias_ecommerce(manager, publicas):
    """Crea en eCommerce las categorías de CREAR_EN_ECOMMERCE que no existan"""
    rutas = {normalizar(c['path']) for c in publicas.values()}
    creadas = 0
    for nombre in CREAR_EN_ECOMMERCE:
        if normalizar(nombre) in rutas:
            continue
        nuevo_id = manager.execute('product.public.category', 'create', [{'name': nombre}])
        creadas += 1
        log_message(f"   📝 Categoría eCommerce creada: '{nombre}' (ID {nuevo_id})")
    return creadas


def ancestros(cat_id, publicas):
    """Devuelve los IDs de los padres de una categoría eCommerce"""
    resultado = set()
    padre = publicas[cat_id]['parent_id']
    while padre and padre not in resultado:
        resultado.add(padre)
        padre = publicas.get(padre, {}).get('parent_id')
    return resultado


def asignar_categorias_productos(manager, internas, publicas):
    """Añade a cada producto publicado la categoría eCommerce equivalente a su categoría interna"""
    publicas_por_ruta = {normalizar(c['path']): cid for cid, c in publicas.items()}

    product_ids = manager.execute('product.template', 'search', [[['is_published', '=', True]]], {'order': 'id'})
    log_message(f"\n📖 {len(product_ids)} productos publicados en Odoo")

    actualizados = 0
    ya_correctos = 0
    sin_equivalente = {}

    for inicio in range(0, len(product_ids), BATCH_SIZE):
        productos = manager.execute(
            'product.template', 'read',
            [product_ids[inicio:inicio + BATCH_SIZE]], {'fields': ['id', 'name', 'categ_id', 'public_categ_ids']}
        )

        for p in productos:
            interna = internas.get(p['categ_id'][0]) if p.get('categ_id') else None
            destino = publicas_por_ruta.get(normalizar(interna['path'])) if interna else None
            if not destino:
                clave = interna['path'] if interna else '(sin categoría)'
                sin_equivalente[clave] = sin_equivalente.get(clave, 0) + 1
                continue

            actuales = set(p.get('public_categ_ids', []))
            padres = ancestros(destino, publicas)
            comandos = []
            if destino not in actuales:
                comandos.append((4, destino))
            comandos += [(3, cid) for cid in actuales & padres]

            if not comandos:
                ya_correctos += 1
                continue

            try:
                manager.execute('product.template', 'write', [[p['id']], {'public_categ_ids': comandos}])
                actualizados += 1
                quitadas = [publicas[cid]['path'] for cid in actuales & padres]
                log_message(f"   🔄 (ID {p['id']}) {p['name']}: + '{publicas[destino]['path']}'"
                            + (f" / - {', '.join(quitadas)}" if quitadas else ""))
            except Exception as e:
                log_message(f"   ❌ Error actualizando producto ID {p['id']}: {e}")

    return actualizados, ya_correctos, sin_equivalente


def run():
    """Función principal que ejecuta todo el proceso"""
    print("=" * 60)
    print("SINCRONIZAR CATEGORÍAS INTERNAS Y ECOMMERCE")
    print("=" * 60)

    start_time = datetime.now()

    try:
        manager = OdooClient()
        manager.connect_to_odoo()

        # Paso 1: Renombrar categorías internas
        log_message("\n🔍 Renombrando categorías internas con el nombre eCommerce...")
        internas, publicas = leer_categorias(manager)
        log_message(f"   {len(internas)} categorías internas, {len(publicas)} categorías eCommerce")
        renombradas = renombrar_categorias(manager, internas, publicas)

        # Paso 2: Crear categorías eCommerce que faltan
        log_message("\n🔍 Creando categorías eCommerce que faltan...")
        creadas = crear_categorias_ecommerce(manager, publicas)

        # Releer los árboles con los cambios (complete_name se recalcula en Odoo)
        internas, publicas = leer_categorias(manager)

        # Paso 3: Asignar categorías eCommerce a los productos
        log_message("\n🔍 Asignando categorías eCommerce a los productos publicados...")
        actualizados, ya_correctos, sin_equivalente = asignar_categorias_productos(manager, internas, publicas)

        # Categorías que quedan solo en un árbol
        rutas_internas = {normalizar(c['path']): c['path'] for c in internas.values()}
        rutas_publicas = {normalizar(c['path']): c['path'] for c in publicas.values()}
        solo_internas = sorted(rutas_internas[r] for r in set(rutas_internas) - set(rutas_publicas))
        solo_publicas = sorted(rutas_publicas[r] for r in set(rutas_publicas) - set(rutas_internas))
        distintas = sorted(f"{rutas_internas[r]} ≠ {rutas_publicas[r]}"
                           for r in set(rutas_internas) & set(rutas_publicas) if rutas_internas[r] != rutas_publicas[r])

        # Resumen final
        duration = datetime.now() - start_time
        log_message("\n" + "=" * 50)
        log_message("📋 RESUMEN FINAL")
        log_message("=" * 50)
        log_message(f"Categorías internas renombradas: {renombradas}")
        log_message(f"Categorías eCommerce creadas: {creadas}")
        log_message(f"Productos actualizados: {actualizados}")
        log_message(f"Productos ya correctos: {ya_correctos}")
        log_message(f"Productos sin categoría eCommerce equivalente: {sum(sin_equivalente.values())}")
        for cat, num in sorted(sin_equivalente.items(), key=lambda x: -x[1]):
            log_message(f"   - {cat}: {num} productos")
        if distintas:
            log_message("⚠️ Categorías que siguen con nombre distinto:")
            for d in distintas:
                log_message(f"   - {d}")
        log_message("Categorías solo internas:")
        for c in solo_internas:
            log_message(f"   - {c}")
        log_message("Categorías solo eCommerce:")
        for c in solo_publicas:
            log_message(f"   - {c}")
        log_message(f"Tiempo de ejecución: {duration.total_seconds():.2f} segundos")

        return True

    except Exception as e:
        log_message(f"\n❌ Error durante el proceso: {e}")
        return False


def main():
    """Función principal"""
    if not run():
        sys.exit(1)


if __name__ == "__main__":
    main()
