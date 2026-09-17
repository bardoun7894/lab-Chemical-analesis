# Green Highlighted Row OCR Design

## Goal

Improve the chemical lab report upload OCR so it extracts values from reports
where the authoritative result row is highlighted green/pistachio, while
preserving the existing behavior for reports that label the row `AVG`.

## Scope

- Update the shared chemical extraction prompt in `app/services/ocr_service.py`.
- Keep the existing upload endpoint, provider dispatch, JSON schema, key
  mapping, range validation, and form auto-fill behavior unchanged.
- Add regression tests for the prompt contract and existing JSON parsing.

## Data Flow

1. The chemical form uploads an image to `POST /chemical/api/ocr-extract`.
2. The route passes image bytes to `extract_elements_from_image`.
3. The OCR service sends the image and `EXTRACTION_PROMPT` to the configured
   Gemini or OpenRouter vision provider.
4. The provider returns the existing JSON object containing the ten supported
   elements.
5. The OCR service parses and range-checks those values; the form fills the
   matching inputs for user verification before saving.

## Prompt Behavior

The prompt will explicitly define two mutually compatible selection paths:

- For ordinary reports, use the row labeled `AVG` or average.
- For colored spreadsheet-like reports, if a green, light-green, or pistachio
  highlighted row is present, use that highlighted row as the authoritative
  result/average row.

The prompt will instruct the model to read values from the cells aligned under
the requested element headers and to ignore other measurement rows, SD rows,
limits, and surrounding non-result rows. If the highlighted row is not visible
or a value is not reliable, the model returns `null` for that value.

## Error Handling

No new error path is required. Existing provider errors, JSON parsing errors,
and out-of-range value handling remain unchanged.

## Testing

- Verify the prompt mentions both `AVG` selection and green/pistachio row
  selection.
- Verify the existing parser still accepts the expected JSON response and
  maps values to the same model field names and precision.
- Run the focused OCR tests and the full test suite.

## Out Of Scope

- Image preprocessing, color segmentation, or row cropping.
- New database fields or changes to chemical form behavior.
- Extraction of additional elements not currently supported by
  `ELEMENT_MAP`.
