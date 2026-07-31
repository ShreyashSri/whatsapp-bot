from cards.render import _build_html, validate_card_design


def test_card_design_schema_allows_text_but_rejects_unsafe_urls():
    assert validate_card_design({"base_template": "custom", "title": "<script>"}) is not None
    assert validate_card_design({"base_template": "custom", "logo_url": "javascript:alert(1)"}) is None
    assert validate_card_design({"base_template": "talk"}) is None
    assert validate_card_design({"base_template": "custom", "accent": "red"}) is None


def test_custom_design_overrides_copy_style_but_keeps_renderer_html_controlled():
    design = validate_card_design({
        "base_template": "hackathon",
        "title": "Congratulations, Zodiak!",
        "accent": "#a855f7",
        "pill": "PBCTF 5.0",
        "highlight_terms": ["PBCTF"],
        "tone": "sarcastic",
        "variation": 4,
    })

    html = _build_html(
        card_type="hackathon",
        name="Zodiak",
        text="For PBCTF",
        photo_data_url="data:image/png;base64,AA==",
        logo_data_url=None,
        design=design,
    )

    assert "Congratulations, Zodiak!" in html
    assert "#A855F7" in html
    assert 'class="highlight">PBCTF</span>' in html
    assert 'class="poster style-hackathon tone-sarcastic variation-4"' in html
    assert "text-transform: uppercase" in html
    assert "poster-wash" in html
    assert "poster-orbit" in html
    assert "<script" not in html
