#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sincronizar categorías TPV con categorías internas
Rehace el árbol de categorías TPV (pos.category) para que sea idéntico al de categorías
internas (product.category), que ya coincide con el de eCommerce.

Funcionalidad:
1. Crea en TPV todas las categorías internas (salvo EXCLUIR) con el mismo nombre y jerarquía.
   Si ya existe una categoría TPV con la misma ruta exacta, se reutiliza
2. Asigna a todos los productos (publicados o no, activos o archivados) la categoría TPV
   equivalente a su categoría interna. Reemplaza las categorías TPV que tuvieran.
   Los productos de categorías excluidas se quedan sin categoría TPV
3. Elimina todas las categorías TPV antiguas que no forman parte del nuevo árbol

Uso:
    python Sincronizar_categorias_tpv.py        # Producción (pide confirmación)
    python Sincronizar_categorias_tpv.py --stg  # Staging

Nota:
- Script independiente, no forma parte del pipeline
- Requiere .env y comun.py
- Odoo puede impedir borrar categorías TPV mientras haya una sesión de TPV abierta
"""

import os
import sys
from datetime import datetime

from comun import OdooClient, configurar_log, log_message

# Log propio
configurar_log("sincronizar_categorias_tpv_log.txt")

WRITE_BATCH_SIZE = 500
CONTEXTO_TODOS = {'context': {'active_test': False}}

# Categorías internas (y sus subcategorías) que no se crean en TPV
EXCLUIR = ["Goods", "Services", "Expenses", "Food", "Deliveries", "Amazon Services", "Archivado", "Generico"]


def excluida(path):
    return path.split(' / ')[0] in EXCLUIR


def crear_arbol_tpv(manager):
    """
    Crea en TPV las categorías internas que falten, respetando la jerarquía.

    Returns:
        tuple: ({categ_id interna: pos_category_id}, creadas, reutilizadas)
    """
    internas = manager.execute('product.category', 'search_read', [[]], {'fields': ['id', 'name', 'parent_id', 'complete_name']})
    tpv = manager.execute('pos.category', 'search_read', [[]], {'fields': ['id', 'display_name']})
    tpv_por_ruta = {c['display_name']: c['id'] for c in tpv}

    mapa = {}
    creadas = 0
    reutilizadas = 0

    # Padres primero
    for cat in sorted(internas, key=lambda c: len(c['complete_name'].split(' / '))):
        if excluida(cat['complete_name']):
            continue

        if cat['complete_name'] in tpv_por_ruta:
            mapa[cat['id']] = tpv_por_ruta[cat['complete_name']]
            reutilizadas += 1
            log_message(f"   ♻️ Reutilizada: '{cat['complete_name']}' (ID {mapa[cat['id']]})")
            continue

        datos = {'name': cat['name']}
        if cat['parent_id']:
            datos['parent_id'] = mapa[cat['parent_id'][0]]
        mapa[cat['id']] = manager.execute('pos.category', 'create', [datos])
        creadas += 1
        log_message(f"   📝 Creada: '{cat['complete_name']}' (ID {mapa[cat['id']]})")

    return mapa, creadas, reutilizadas


def asignar_productos(manager, mapa):
    """Asigna a todos los productos la categoría TPV de su categoría interna"""
    internas = manager.execute('product.category', 'search_read', [[]], {'fields': ['id', 'complete_name']})
    asignados = 0
    vaciados = 0

    for cat in internas:
        product_ids = manager.execute('product.template', 'search', [[['categ_id', '=', cat['id']]]], CONTEXTO_TODOS)
        if not product_ids:
            continue

        pos_id = mapa.get(cat['id'])
        valor = [(6, 0, [pos_id])] if pos_id else [(6, 0, [])]

        for inicio in range(0, len(product_ids), WRITE_BATCH_SIZE):
            lote = product_ids[inicio:inicio + WRITE_BATCH_SIZE]
            manager.execute('product.template', 'write', [lote, {'pos_categ_ids': valor}], CONTEXTO_TODOS)

        if pos_id:
            asignados += len(product_ids)
            log_message(f"   🔄 {len(product_ids)} productos → '{cat['complete_name']}'")
        else:
            vaciados += len(product_ids)
            log_message(f"   ⏭️ {len(product_ids)} productos de '{cat['complete_name']}' (excluida) sin categoría TPV")

    return asignados, vaciados


def eliminar_antiguas(manager, mapa):
    """Elimina las categorías TPV que no forman parte del nuevo árbol"""
    nuevas = set(mapa.values())
    tpv = manager.execute('pos.category', 'search_read', [[]], {'fields': ['id', 'display_name']})
    antiguas = [c for c in tpv if c['id'] not in nuevas]

    # Primero las más profundas
    antiguas.sort(key=lambda c: -len(c['display_name'].split(' / ')))
    eliminadas = 0
    errores = 0

    for cat in antiguas:
        try:
            # Puede haber desaparecido ya al borrar su padre en cascada
            if not manager.execute('pos.category', 'search', [[['id', '=', cat['id']]]]):
                continue
            restantes = manager.execute('product.template', 'search_count', [[['pos_categ_ids', 'in', [cat['id']]]]], CONTEXTO_TODOS)
            if restantes:
                errores += 1
                log_message(f"   ⚠️ No se elimina '{cat['display_name']}': todavía tiene {restantes} productos")
                continue
            manager.execute('pos.category', 'unlink', [[cat['id']]])
            eliminadas += 1
            log_message(f"   🗑️ Eliminada: '{cat['display_name']}' (ID {cat['id']})")
        except Exception as e:
            errores += 1
            log_message(f"   ❌ Error eliminando '{cat['display_name']}': {e}")

    return eliminadas, errores


def run():
    """Función principal que ejecuta todo el proceso"""
    print("=" * 60)
    print("SINCRONIZAR CATEGORÍAS TPV CON CATEGORÍAS INTERNAS")
    print("=" * 60)

    start_time = datetime.now()

    try:
        manager = OdooClient()
        manager.connect_to_odoo()

        # Paso 1: Crear el árbol TPV
        log_message("\n🔍 Creando categorías TPV a partir de las categorías internas...")
        mapa, creadas, reutilizadas = crear_arbol_tpv(manager)

        # Paso 2: Asignar productos
        log_message("\n🔍 Asignando categorías TPV a todos los productos...")
        asignados, vaciados = asignar_productos(manager, mapa)

        # Paso 3: Eliminar categorías antiguas
        log_message("\n🔍 Eliminando categorías TPV antiguas...")
        eliminadas, errores = eliminar_antiguas(manager, mapa)

        # Resumen final
        duration = datetime.now() - start_time
        log_message("\n" + "=" * 50)
        log_message("📋 RESUMEN FINAL")
        log_message("=" * 50)
        log_message(f"Categorías TPV creadas: {creadas}")
        log_message(f"Categorías TPV reutilizadas: {reutilizadas}")
        log_message(f"Productos con categoría TPV asignada: {asignados}")
        log_message(f"Productos de categorías excluidas sin categoría TPV: {vaciados}")
        log_message(f"Categorías TPV antiguas eliminadas: {eliminadas}")
        log_message(f"Categorías TPV antiguas no eliminadas: {errores}")
        log_message(f"Tiempo de ejecución: {duration.total_seconds():.2f} segundos")

        return errores == 0

    except Exception as e:
        log_message(f"\n❌ Error durante el proceso: {e}")
        return False


def main():
    """Función principal"""
    if not run():
        sys.exit(1)


if __name__ == "__main__":
    main()
