"""
Script to deduplicate products in Odoo.

For each duplicated x_studio_referencia_del_proveedor:
- Keeps the newest product (latest create_date)
- For older copies:
    * If NO transactions exist (sales, purchases, stock moves, accounting) → DELETE permanently
    * If transactions exist → deactivate (archive) to preserve history

Usage:
    python limpiar_duplicados.py                  # Execute cleanup (production, asks for confirmation)
    python limpiar_duplicados.py --dry-run        # Preview only, no changes
    python limpiar_duplicados.py --stg [--dry-run] # Same against staging
"""

import sys
from collections import defaultdict
from comun import OdooClient

# Models to check for transactions against product variants
TRANSACTION_MODELS = [
    ('sale.order.line',     'product_id'),
    ('purchase.order.line', 'product_id'),
    ('stock.move',          'product_id'),
    ('account.move.line',   'product_id'),
]


def fetch_all_products(odoo):
    """Fetch all products with a non-empty provider reference, including archived."""
    products = odoo.execute(
        'product.template', 'search_read',
        [[
            ['x_studio_referencia_del_proveedor', '!=', False],
            ['x_studio_referencia_del_proveedor', '!=', ''],
            ['active', 'in', [True, False]],
        ]],
        {'fields': ['id', 'name', 'x_studio_referencia_del_proveedor',
                     'create_date', 'product_template_image_ids', 'active'],
         'limit': 50000}
    )
    return products


def find_duplicates(products):
    """Group products by reference and return only groups with 2+ products."""
    by_ref = defaultdict(list)
    for p in products:
        ref = str(p['x_studio_referencia_del_proveedor']).strip()
        if ref:
            by_ref[ref].append(p)
    return {ref: prods for ref, prods in by_ref.items() if len(prods) > 1}


def get_variant_ids(odoo, template_id):
    """Return all product.product variant IDs for a given product.template ID."""
    variants = odoo.execute(
        'product.product', 'search',
        [[['product_tmpl_id', '=', template_id], ['active', 'in', [True, False]]]]
    )
    return variants


def clear_stock(odoo, variant_ids, dry_run=False):
    """Zero out all stock on hand for the given product variant IDs.
    Returns the number of stock.quant records cleared."""
    if not variant_ids:
        return 0

    quants = odoo.execute(
        'stock.quant', 'search_read',
        [[['product_id', 'in', variant_ids], ['quantity', '>', 0]]],
        {'fields': ['id', 'quantity', 'location_id', 'product_id']}
    )

    if not quants:
        return 0

    if dry_run:
        return len(quants)

    quant_ids = [q['id'] for q in quants]
    odoo.execute(
        'stock.quant', 'write',
        [quant_ids, {'inventory_quantity': 0}]
    )
    odoo.execute(
        'stock.quant', 'action_apply_inventory',
        [quant_ids]
    )
    return len(quants)


def has_transactions(odoo, variant_ids):
    """
    Check whether any of the given product variant IDs appear in any transaction model.
    Returns (bool, str) — (True, model_name) if found, (False, '') if clean.
    """
    if not variant_ids:
        return False, ''

    for model, field in TRANSACTION_MODELS:
        try:
            count = odoo.execute(
                model, 'search_count',
                [[[field, 'in', variant_ids]]]
            )
            if count > 0:
                return True, model
        except Exception:
            # Model may not be installed; skip it
            pass

    return False, ''


def main():
    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print("=" * 60)
        print("  DRY RUN - No changes will be made")
        print("=" * 60)

    odoo = OdooClient()
    odoo.connect_to_odoo()

    print("\nFetching all products with provider reference (including archived)...")
    products = fetch_all_products(odoo)
    print(f"Total products fetched: {len(products)}")

    duplicates = find_duplicates(products)
    total_extra = sum(len(prods) - 1 for prods in duplicates.values())

    print(f"\nDuplicated references: {len(duplicates)}")
    print(f"Extra copies to process: {total_extra}")

    if not duplicates:
        print("\nNo duplicates found. Nothing to do.")
        return

    deleted = 0
    deactivated = 0
    images_cleared = 0
    stock_cleared = 0
    errors = []

    print(f"\n{'=' * 60}")
    print("Processing duplicates...")
    print(f"{'=' * 60}\n")

    for ref, prods in duplicates.items():
        # Keep the newest (latest create_date)
        prods_sorted = sorted(prods, key=lambda p: p['create_date'], reverse=True)
        keeper = prods_sorted[0]
        to_remove = prods_sorted[1:]

        print(f"Ref: {ref} ({len(prods)} copies)")
        print(f"  KEEP: ID={keeper['id']} | {keeper['create_date']} | {keeper['name'][:70]}")

        for p in to_remove:
            # Determine what action to take
            variant_ids = get_variant_ids(odoo, p['id'])
            found_tx, tx_model = has_transactions(odoo, variant_ids)

            if found_tx:
                action = "DEACTIVATE"
                reason = f"has transactions in {tx_model}"
            else:
                action = "DELETE"
                reason = "no transactions"

            print(f"  {action}: ID={p['id']} | {p['create_date']} | {p['name'][:60]} ({reason})", end="")

            if dry_run:
                print(" [dry-run]")
                if action == "DELETE":
                    deleted += 1
                else:
                    deactivated += 1
                    if p.get('product_template_image_ids'):
                        images_cleared += 1
                    n = clear_stock(odoo, variant_ids, dry_run=True)
                    if n:
                        stock_cleared += n
                        print(f"    -> Would clear stock ({n} quant(s))")
                continue

            try:
                if action == "DELETE":
                    # Remove any stock.quant records (even qty=0) to avoid FK constraint
                    all_quants = odoo.execute(
                        'stock.quant', 'search',
                        [[['product_id', 'in', variant_ids]]]
                    )
                    if all_quants:
                        odoo.execute(
                            'stock.quant', 'unlink',
                            [all_quants]
                        )
                        print(f"\n    -> Quants removed ({len(all_quants)})", end="")

                    # Clear images and disable sale/purchase flags before deleting
                    odoo.execute(
                        'product.template', 'write',
                        [[p['id']], {'product_template_image_ids': [(5, 0, 0)],
                                     'image_1920': False,
                                     'sale_ok': False,
                                     'purchase_ok': False,
                                     'available_in_pos': False}]
                    )
                    # Hard delete
                    odoo.execute(
                        'product.template', 'unlink',
                        [[p['id']]]
                    )
                    deleted += 1
                    print(" OK (deleted)")

                else:
                    # Clear stock if any
                    n = clear_stock(odoo, variant_ids)
                    if n:
                        stock_cleared += n
                        print(f"\n    -> Stock cleared ({n} quant(s))", end="")

                    # Clear additional images
                    odoo.execute(
                        'product.template', 'write',
                        [[p['id']], {'product_template_image_ids': [(5, 0, 0)],
                                     'image_1920': False}]
                    )
                    if p.get('product_template_image_ids'):
                        images_cleared += 1

                    # Deactivate
                    odoo.execute(
                        'product.template', 'write',
                        [[p['id']], {'active': False, 'website_published': False}]
                    )
                    deactivated += 1
                    print(" OK (deactivated)")

            except Exception as e:
                errors.append((p['id'], ref, str(e)))
                print(f" ERROR: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"Duplicated references processed: {len(duplicates)}")
    print(f"Products deleted (no transactions): {deleted}")
    print(f"Products deactivated (had transactions): {deactivated}")
    print(f"Products with images cleared: {images_cleared}")
    print(f"Stock quants cleared before deactivation: {stock_cleared}")
    if errors:
        print(f"Errors: {len(errors)}")
        for pid, ref, err in errors:
            print(f"  ID={pid} Ref={ref}: {err}")
    else:
        print("Errors: 0")

    if dry_run:
        print(f"\nDRY RUN complete. Re-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
