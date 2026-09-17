# Spec 005: Production Order → Product Cascade

## Problem
When a Production Order is selected, the JS handler directly sets diameter (from `data-diameter`) and pipe class (from `data-type`), THEN also sets the product (from `data-product-id`). This creates a conflict — the product handler also tries to set diameter/class from its own data, overwriting what the PO handler set.

## Solution
Remove the direct diameter/class setting from the PO change handler. The PO should only set the Product dropdown. The Product change handler already cascades to diameter (with correct DN mapping), pipe class, and weight.

## Cascade Flow
1. User selects Production Order → PO handler sets Product dropdown
2. Product `change` event fires → Product handler sets diameter (with correct DN from `product_dn_map`), class, weight
3. Diameter `change` event fires → Mold filter updates

## Change
- Remove `data-diameter`/`data-type` reading and setting from PO change handler
- Keep `data-product-id` → Product dropdown selection
