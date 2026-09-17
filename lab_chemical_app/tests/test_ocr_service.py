from app.services import ocr_service


def test_extraction_prompt_supports_avg_and_green_highlighted_rows():
    prompt = " ".join(ocr_service.EXTRACTION_PROMPT.lower().split())

    assert "avg" in prompt
    assert "a row highlighted in green (or light-green / pistachio)" in prompt
    assert "that green row wins over everything else, including a row labelled avg" in prompt
    assert "a row labelled avg, ave, average, mean, or المتوسط" in prompt
    assert prompt.index("a row highlighted in green (or light-green / pistachio)") < prompt.index(
        "a row labelled avg, ave, average, mean, or المتوسط"
    )


def test_extract_elements_preserves_existing_json_mapping(monkeypatch):
    monkeypatch.setattr(
        ocr_service,
        "call_vision_api",
        lambda image_bytes, prompt, mime_type: (
            '{"carbon": 3.9569, "silicon": 2.2606, "magnesium": 0.0516, '
            '"copper": 0.0284, "chromium": 0.0311, "sulfur": 0.0147, '
            '"manganese": 0.1966, "phosphorus": 0.0161, "lead": 0.001, '
            '"aluminum": 0.0102}'
        ),
    )

    result = ocr_service.extract_elements_from_image(b"image-bytes", "report.png")

    assert result == {
        "carbon": 3.9569,
        "silicon": 2.2606,
        "magnesium": 0.0516,
        "copper": 0.0284,
        "chromium": 0.0311,
        "sulfur": 0.0147,
        "manganese": 0.1966,
        "phosphorus": 0.0161,
        "lead": 0.001,
        "aluminum": 0.0102,
    }
