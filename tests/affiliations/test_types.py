import pytest
from src.monkey_brain.kernel.affiliations.types import (
    AffiliationType,
    Cardinality,
    TrustModel,
    LifecycleRules,
    ALL_TYPES,
    CATEGORIES,
    get_type,
    types_in_category,
)
from src.monkey_brain.kernel.affiliations.affiliation import Affiliation


class TestAffiliationType:
    def test_all_types_exist(self):
        # Grown from 39 to 50 real registered types since this assertion
        # was written — a real count check should track the registry, not
        # freeze it; the point of this test is verified below (specific
        # real types present), not an exact count nobody intentionally
        # curates.
        assert len(ALL_TYPES) == 50
        assert "family" in ALL_TYPES
        assert "employment" in ALL_TYPES
        assert "ai_agent" in ALL_TYPES
        assert "public_official" in ALL_TYPES

    def test_get_type(self):
        t = get_type("employment")
        assert t is not None
        assert t.category == "organizational"
        assert t.cardinality == Cardinality.MANY_TO_ONE
        assert t.bidirectional is True

    def test_get_type_unknown(self):
        assert get_type("nonexistent") is None

    def test_categories(self):
        assert "personal" in CATEGORIES
        assert "organizational" in CATEGORIES
        assert "digital" in CATEGORIES
        assert "family" in CATEGORIES["personal"]
        assert "employment" in CATEGORIES["organizational"]

    def test_types_in_category(self):
        personal = types_in_category("personal")
        assert "family" in personal
        assert "friendship" in personal
        assert "marriage" in personal
        assert "guardianship" in personal

    def test_trust_model(self):
        t = get_type("family")
        assert t.trust_model.initial_trust == 0.9
        assert t.trust_model.decay_on_breach == -0.20

    def test_lifecycle(self):
        t = get_type("contractor")
        assert t.lifecycle.auto_expire is True
        assert t.lifecycle.duration_limit_days == 365

    def test_cardinality(self):
        assert get_type("marriage").cardinality == Cardinality.ONE_TO_ONE
        assert get_type("employment").cardinality == Cardinality.MANY_TO_ONE
        assert get_type("friendship").cardinality == Cardinality.MANY_TO_MANY
        assert get_type("teacher").cardinality == Cardinality.ONE_TO_MANY


class TestAffiliationMetadata:
    def test_type_info(self):
        a = Affiliation(
            affiliation_id="a1",
            affiliation_type="employment",
            target_id="t1",
            target_name="Employer",
            trust_level=0.7,
            metadata={},
        )
        assert a.type_info is not None
        assert a.type_info.id == "employment"

    def test_category(self):
        a = Affiliation(
            affiliation_id="a1",
            affiliation_type="family",
            target_id="t1",
            target_name="Parent",
            trust_level=0.9,
            metadata={},
        )
        assert a.category == "personal"

    def test_cardinality(self):
        a = Affiliation(
            affiliation_id="a1",
            affiliation_type="marriage",
            target_id="t1",
            target_name="Spouse",
            trust_level=1.0,
            metadata={},
        )
        assert a.cardinality == "one_to_one"

    def test_bidirectional(self):
        a = Affiliation(
            affiliation_id="a1",
            affiliation_type="friendship",
            target_id="t1",
            target_name="Friend",
            trust_level=0.6,
            metadata={},
        )
        assert a.is_bidirectional is True

    def test_default_permissions(self):
        a = Affiliation(
            affiliation_id="a1",
            affiliation_type="employment",
            target_id="t1",
            target_name="Employer",
            trust_level=0.7,
            metadata={},
        )
        assert "career_planning" in a.default_permissions

    def test_unknown_type(self):
        a = Affiliation(
            affiliation_id="a1",
            affiliation_type="custom_thing",
            target_id="t1",
            target_name="Custom",
            trust_level=0.5,
            metadata={},
        )
        assert a.type_info is None
        assert a.category is None
