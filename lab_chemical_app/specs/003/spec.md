# Product-Production Order Relationship Fix

## Problem
On the `/stages/add` form, the **Product** dropdown shows all products regardless of which **Production Order** is selected. Since each Production Order already has a `product_id` FK, the Product dropdown should auto-filter/auto-select based on the selected order.

## Solution
- Add `data-product-id="{{ order.product_id }}"` to each production order `<option>`
- In the JS change handler, auto-select the matching product in the dropdown

## Status
Implementation ready. Waiting for deployment.
