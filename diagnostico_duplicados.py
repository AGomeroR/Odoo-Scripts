"""
Diagnostic script to identify duplicate products in Odoo.

Checks for products with duplicate x_studio_referencia_del_proveedor values
and analyzes supplier (seller_ids) configuration to understand the scope
of the duplication problem before applying fixes.

Usage:
    python diagnostico_duplicados.py        # Production (asks for confirmation)
    python diagnostico_duplicados.py --stg  # Staging
"""

import os
import openpyxl
from collections import defaultdict
from comun import OdooClient

PROVEEDOR = os.getenv("PROVEEDOR", "Zentral Media S.L")

# Known repeating references from the latest import attempt
EXCEL_PATH = "/home/servidor/Programas-automatizaciones/Importador/Zentralmedia/Logs/2026-02-25/Articulos_a_subir.xlsx"


def load_references_from_excel():
    """Load provider references from Articulos_a_subir.xlsx."""
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    ref_col = headers.index("x_studio_referencia_del_proveedor")
    refs = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        ref = row[ref_col]
        if ref:
            refs.append(str(ref).strip())
    wb.close()
    print(f"Loaded {len(refs)} references from Excel")
    return refs


def search_products_by_refs(odoo, refs):
    """Search Odoo for products matching the given provider references."""
    products = odoo.execute(
        'product.template', 'search_read',
        [[['x_studio_referencia_del_proveedor', 'in', refs]]],
        {'fields': ['id', 'name', 'x_studio_referencia_del_proveedor', 'seller_ids', 'create_date'],
         'limit': 5000}
    )
    return products


def get_supplier_info(odoo, seller_ids_list):
    """Read supplier info records to check partner names."""
    if not seller_ids_list:
        return []
    return odoo.execute(
        'product.supplierinfo', 'read',
        [seller_ids_list],
        {'fields': ['partner_id', 'price']}
    )


def find_all_duplicate_refs(odoo):
    """Find ALL products in Odoo that have duplicate x_studio_referencia_del_proveedor values."""
    # Get all products that have a non-empty provider reference
    all_products = odoo.execute(
        'product.template', 'search_read',
        [[['x_studio_referencia_del_proveedor', '!=', False],
          ['x_studio_referencia_del_proveedor', '!=', '']]],
        {'fields': ['id', 'name', 'x_studio_referencia_del_proveedor', 'seller_ids', 'create_date'],
         'limit': 50000}
    )
    # Group by reference
    by_ref = defaultdict(list)
    for p in all_products:
        ref = str(p['x_studio_referencia_del_proveedor']).strip()
        if ref:
            by_ref[ref].append(p)
    # Filter to only duplicates
    duplicates = {ref: prods for ref, prods in by_ref.items() if len(prods) > 1}
    return duplicates, len(all_products)


def main():
    odoo = OdooClient()
    odoo.connect_to_odoo()

    # --- Part 1: Check known references from Excel ---
    print("\n" + "=" * 70)
    print("PART 1: Products matching references from Articulos_a_subir.xlsx")
    print("=" * 70)

    refs = load_references_from_excel()
    products = search_products_by_refs(odoo, refs)

    # Group by reference
    by_ref = defaultdict(list)
    for p in products:
        ref = str(p['x_studio_referencia_del_proveedor']).strip()
        by_ref[ref].append(p)

    refs_with_duplicates = {r: ps for r, ps in by_ref.items() if len(ps) > 1}
    refs_single = {r: ps for r, ps in by_ref.items() if len(ps) == 1}
    refs_not_found = [r for r in refs if r not in by_ref]

    print(f"\nReferences from Excel: {len(refs)}")
    print(f"  Found in Odoo (single): {len(refs_single)}")
    print(f"  Found in Odoo (DUPLICATED): {len(refs_with_duplicates)}")
    print(f"  NOT found in Odoo: {len(refs_not_found)}")

    # Collect all seller_ids to batch-read supplier info
    all_seller_ids = set()
    for p in products:
        all_seller_ids.update(p.get('seller_ids', []))
    seller_info = {}
    if all_seller_ids:
        for info in get_supplier_info(odoo, list(all_seller_ids)):
            seller_info[info['id']] = info

    # Check supplier configuration
    products_with_supplier = 0
    products_without_supplier = 0
    for p in products:
        has_matching_supplier = False
        for sid in p.get('seller_ids', []):
            si = seller_info.get(sid)
            if si and si['partner_id'] and PROVEEDOR.lower() in si['partner_id'][1].lower():
                has_matching_supplier = True
                break
        if has_matching_supplier:
            products_with_supplier += 1
        else:
            products_without_supplier += 1

    print(f"\nSupplier analysis ('{PROVEEDOR}'):")
    print(f"  Products WITH matching supplier: {products_with_supplier}")
    print(f"  Products WITHOUT matching supplier: {products_without_supplier}")

    # Show sample duplicates
    if refs_with_duplicates:
        print(f"\n--- Sample duplicated references (up to 10) ---")
        for ref, prods in list(refs_with_duplicates.items())[:10]:
            print(f"\n  Ref: {ref} ({len(prods)} copies)")
            for p in prods:
                suppliers = []
                for sid in p.get('seller_ids', []):
                    si = seller_info.get(sid)
                    if si and si['partner_id']:
                        suppliers.append(si['partner_id'][1])
                print(f"    ID={p['id']} | Created={p.get('create_date', '?')} | Suppliers={suppliers or 'NONE'}")
                print(f"      Name: {p['name'][:80]}")

    # --- Part 2: All duplicates in Odoo (global) ---
    print("\n" + "=" * 70)
    print("PART 2: ALL duplicate x_studio_referencia_del_proveedor in Odoo")
    print("=" * 70)

    all_duplicates, total_with_ref = find_all_duplicate_refs(odoo)
    total_duplicate_products = sum(len(ps) for ps in all_duplicates.values())

    print(f"\nTotal products with a provider reference: {total_with_ref}")
    print(f"Unique references with duplicates: {len(all_duplicates)}")
    print(f"Total products involved in duplicates: {total_duplicate_products}")
    print(f"Extra copies (to potentially remove): {total_duplicate_products - len(all_duplicates)}")

    # Show top duplicates by count
    if all_duplicates:
        sorted_dups = sorted(all_duplicates.items(), key=lambda x: len(x[1]), reverse=True)
        print(f"\n--- Top 15 most duplicated references ---")
        for ref, prods in sorted_dups[:15]:
            names = set(p['name'][:60] for p in prods)
            print(f"  Ref={ref}: {len(prods)} copies, unique names: {len(names)}")
            for name in list(names)[:3]:
                print(f"    - {name}")

    # --- Part 3: Products with Studio Ref but no supplier ---
    print("\n" + "=" * 70)
    print("PART 3: Products with Studio Ref but missing supplier info")
    print("=" * 70)

    # Collect all seller_ids from all duplicates
    dup_seller_ids = set()
    for prods in all_duplicates.values():
        for p in prods:
            dup_seller_ids.update(p.get('seller_ids', []))
    dup_seller_info = {}
    if dup_seller_ids:
        # Batch read in chunks
        dup_seller_list = list(dup_seller_ids)
        for i in range(0, len(dup_seller_list), 500):
            chunk = dup_seller_list[i:i+500]
            for info in get_supplier_info(odoo, chunk):
                dup_seller_info[info['id']] = info

    ref_set_no_supplier = 0
    ref_empty_has_supplier = 0
    for ref, prods in all_duplicates.items():
        for p in prods:
            has_supplier = False
            for sid in p.get('seller_ids', []):
                si = dup_seller_info.get(sid)
                if si and si['partner_id'] and PROVEEDOR.lower() in si['partner_id'][1].lower():
                    has_supplier = True
                    break
            if not has_supplier:
                ref_set_no_supplier += 1

    print(f"  Duplicate products with ref but NO '{PROVEEDOR}' supplier: {ref_set_no_supplier}")

    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
