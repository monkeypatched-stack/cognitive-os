import pytest
from src.monkey_brain.kernel.affiliations.family import FamilyAffiliation
from src.monkey_brain.kernel.affiliations.employment import EmploymentAffiliation
from src.monkey_brain.kernel.affiliations.education import EducationAffiliation


class TestFamilyAffiliation:
    def test_origin(self):
        f = FamilyAffiliation(
            affiliation_id="f1",
            affiliation_type="family",
            target_id="bob",
            target_name="Bob",
            trust_level=1.0,
            metadata={},
            branch="origin",
            relation="father",
        )
        assert f.branch == "origin"
        assert f.relation == "father"
        assert f.trust_level == 1.0

    def test_creation(self):
        f = FamilyAffiliation(
            affiliation_id="f2",
            affiliation_type="family",
            target_id="child1",
            target_name="Child",
            trust_level=0.9,
            metadata={},
            branch="creation",
            relation="child",
        )
        assert f.branch == "creation"


class TestEmploymentAffiliation:
    def test_active(self):
        e = EmploymentAffiliation(
            affiliation_id="e1",
            affiliation_type="employment",
            target_id="openai",
            target_name="OpenAI",
            trust_level=0.8,
            metadata={},
            role="Engineer",
            start_date="2026-01-01",
            end_date="",
            status="active",
        )
        assert e.status == "active"
        assert e.end_date == ""

    def test_ended(self):
        e = EmploymentAffiliation(
            affiliation_id="e2",
            affiliation_type="employment",
            target_id="google",
            target_name="Google",
            trust_level=0.6,
            metadata={},
            role="SWE",
            start_date="2020-01-01",
            end_date="2024-12-31",
            status="ended",
        )
        assert e.status == "ended"


class TestEducationAffiliation:
    def test_enrolled(self):
        ed = EducationAffiliation(
            affiliation_id="ed1",
            affiliation_type="education",
            target_id="stanford",
            target_name="Stanford",
            trust_level=0.85,
            metadata={},
            institution="Stanford",
            program="CS",
            degree="MS",
            status="enrolled",
        )
        assert ed.degree == "MS"

    def test_completed(self):
        ed = EducationAffiliation(
            affiliation_id="ed2",
            affiliation_type="education",
            target_id="mit",
            target_name="MIT",
            trust_level=0.8,
            metadata={},
            institution="MIT",
            program="AI",
            degree="PhD",
            status="completed",
        )
        assert ed.status == "completed"
