# Diameter Auto-Select on Production Order Change

## Problem
When selecting a Production Order on `/stages/add`, the Diameter (DN) dropdown is auto-set via JS but doesn't dispatch a `change` event. This means `filterMoldsByDiameter()` never runs, so the Mold Number dropdown doesn't filter by the newly-set diameter.

## Fix
Add `dnSelect.dispatchEvent(new Event('change'))` after setting the diameter value so the mold filter picks it up.

## Status
Ready for implementation.
