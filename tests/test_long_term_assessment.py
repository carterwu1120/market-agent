from src.agents.long_term_assessment import assess_long_term


def test_strong_fundamentals_and_commercial_evidence_are_long_term_eligible():
    result = assess_long_term(
        {
            "revenue_growth": 0.25,
            "roe": 0.22,
            "gross_margin": 0.45,
            "debt_to_equity": 35,
            "pe_ratio": 25,
        },
        {
            "evidence": {
                "technology": [{"title": "new technology"}],
                "commercialization": [{"title": "mass production"}],
                "supply_chain": [{"title": "customer adoption"}],
                "competition_risk": [],
            }
        },
    )

    assert result["score"] == 95
    assert result["classification"] == "verified_advantage"
    assert result["eligible_for_long_term"] is True


def test_search_theme_without_business_evidence_is_not_long_term_eligible():
    result = assess_long_term(
        {"revenue_growth": -0.1, "roe": 0.05, "gross_margin": 0.1, "pe_ratio": 80},
        {"evidence": {"technology": [{"title": "concept"}]}},
    )

    assert result["classification"] == "theme_only"
    assert result["eligible_for_long_term"] is False
    assert "本益比偏高，需額外估值安全邊際" in result["risks"]
