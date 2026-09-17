# Spec 006: Product Code Serial Auto-Increment

## Problem
When creating a product via `/admin/products/add`, the product code is generated from selected parameters (e.g., `P10C40Z2060ECB`). There's no serial number appended. If the same combination of parameters is used twice, the product code would be identical — a duplicate.

## Solution
Append a 2-digit serial number to the product code. Query the DB for the highest existing serial for the same base code, then increment.

**Example flow:**
1. User selects parameters → base code = `P10C40Z2060ECB`
2. Check DB: `P10C40Z2060ECB00` exists → use `P10C40Z2060ECB01`
3. Next: `P10C40Z2060ECB02`, etc.

## Implementation
- Modify `Product.generate_product_code()` or add logic in the route
- After generating the base code, query for existing codes with the same base
- Append the next available 2-digit serial (zero-padded)
- The serial slot maps to `PROJECT_TYPE` (last in param order)

## Files
- `/app/models/product.py` — `generate_product_code()` method
- Potentially route in `/app/routes/admin.py`
