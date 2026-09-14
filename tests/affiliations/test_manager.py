import pytest
from src.monkey_brain.kernel.affiliations.trust import TrustEngine
from src.monkey_brain.kernel.affiliations.affiliation import Affiliation
from src.monkey_brain.kernel.affiliations.family import FamilyAffiliation
from src.monkey_brain.kernel.affiliations.employment import EmploymentAffiliation
from src.monkey_brain.kernel.affiliations.education import EducationAffiliation
from src.monkey_brain.kernel.affiliations.manager import AffiliationManager


class TestCRUD:
    def test_add_and_get(self):
        mgr = AffiliationManager()
        a = Affiliation(
            affiliation_id="a1",
            affiliation_type="community",
            target_id="c1",
            target_name="Tech Club",
            trust_level=0.6,
            metadata={},
        )
        mgr.add(a)
        assert mgr.get("a1") == a
        assert mgr.count() == 1

    def test_add_multiple(self):
        mgr = AffiliationManager()
        for i in range(5):
            mgr.add(
                Affiliation(
                    affiliation_id=f"a{i}",
                    affiliation_type="community",
                    target_id=f"t{i}",
                    target_name=f"Club {i}",
                    trust_level=0.5,
                    metadata={},
                )
            )
        assert mgr.count() == 5
        assert len(mgr.all()) == 5

    def test_remove(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="community",
                target_id="c1",
                target_name="Club",
                trust_level=0.6,
                metadata={},
            )
        )
        assert mgr.remove("a1") is True
        assert mgr.get("a1") is None
        assert mgr.count() == 0

    def test_remove_nonexistent(self):
        mgr = AffiliationManager()
        assert mgr.remove("nope") is False

    def test_update_trust_level(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="employer",
                target_id="o1",
                target_name="OpenAI",
                trust_level=0.7,
                metadata={},
            )
        )
        updated = mgr.update("a1", trust_level=0.9)
        assert updated.trust_level == 0.9
        assert mgr.get_trust("o1") == 0.9

    def test_update_permissions(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="employer",
                target_id="o1",
                target_name="OpenAI",
                trust_level=0.7,
                permissions=("career_planning",),
                metadata={},
            )
        )
        updated = mgr.update("a1", permissions=("career_planning", "financial"))
        assert "financial" in updated.permissions

    def test_update_nonexistent(self):
        mgr = AffiliationManager()
        assert mgr.update("nope", trust_level=0.9) is None


class TestQuery:
    def _make_manager(self):
        mgr = AffiliationManager()
        mgr.add(
            FamilyAffiliation(
                affiliation_id="f1",
                affiliation_type="family",
                target_id="spouse",
                target_name="Spouse",
                trust_level=1.0,
                metadata={},
                branch="creation",
                relation="spouse",
            )
        )
        mgr.add(
            EmploymentAffiliation(
                affiliation_id="e1",
                affiliation_type="employment",
                target_id="openai",
                target_name="OpenAI",
                trust_level=0.8,
                metadata={},
                role="Engineer",
                start_date="",
                end_date="",
                status="active",
                permissions=("career_planning",),
            )
        )
        mgr.add(
            EmploymentAffiliation(
                affiliation_id="e2",
                affiliation_type="employment",
                target_id="prev_job",
                target_name="Old Corp",
                trust_level=0.4,
                metadata={},
                role="SWE",
                start_date="2020",
                end_date="2024",
                status="ended",
            )
        )
        mgr.add(
            EducationAffiliation(
                affiliation_id="ed1",
                affiliation_type="education",
                target_id="stanford",
                target_name="Stanford",
                trust_level=0.85,
                metadata={},
                institution="Stanford",
                program="CS",
                degree="MS",
                status="completed",
            )
        )
        return mgr

    def test_by_type(self):
        mgr = self._make_manager()
        employments = mgr.by_type("employment")
        assert len(employments) == 2

    def test_by_target(self):
        mgr = self._make_manager()
        results = mgr.by_target("openai")
        assert len(results) == 1
        assert results[0].target_name == "OpenAI"

    def test_by_permission(self):
        mgr = self._make_manager()
        results = mgr.by_permission("career_planning")
        assert len(results) == 1
        assert results[0].target_id == "openai"

    def test_has_permission(self):
        mgr = self._make_manager()
        assert mgr.has_permission("openai", "career_planning") is True
        assert mgr.has_permission("openai", "financial") is False
        assert mgr.has_permission("spouse", "career_planning") is False

    def test_active_filters_expired(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="employment",
                target_id="t1",
                target_name="Active",
                trust_level=0.7,
                valid_from="",
                valid_until="",
                metadata={},
            )
        )
        mgr.add(
            Affiliation(
                affiliation_id="a2",
                affiliation_type="employment",
                target_id="t2",
                target_name="Expired",
                trust_level=0.7,
                valid_from="2020-01-01",
                valid_until="2024-12-31",
                metadata={},
            )
        )
        active = mgr.active()
        assert len(active) == 1
        assert active[0].target_name == "Active"


class TestTrust:
    def test_get_set_trust(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="employer",
                target_id="o1",
                target_name="OpenAI",
                trust_level=0.7,
                metadata={},
            )
        )
        assert mgr.get_trust("o1") == 0.7
        mgr.set_trust("o1", 0.9)
        assert mgr.get_trust("o1") == 0.9

    def test_trusted_participants(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="employer",
                target_id="t1",
                target_name="Trusted",
                trust_level=0.8,
                metadata={},
            )
        )
        mgr.add(
            Affiliation(
                affiliation_id="a2",
                affiliation_type="employer",
                target_id="t2",
                target_name="Untrusted",
                trust_level=0.3,
                metadata={},
            )
        )
        trusted = mgr.trusted_participants(min_trust=0.5)
        assert len(trusted) == 1
        assert trusted[0].target_name == "Trusted"

    def test_update_trust_from_outcome(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="employer",
                target_id="o1",
                target_name="Employer",
                trust_level=0.7,
                metadata={},
            )
        )
        initial = mgr.get_trust("o1")
        mgr.update_trust_from_outcome("o1", goal_achieved=True, obligation_met=True)
        assert mgr.get_trust("o1") > initial

    def test_update_trust_bad_recommendation(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="community",
                target_id="friend",
                target_name="Friend",
                trust_level=0.6,
                metadata={},
            )
        )
        initial = mgr.get_trust("friend")
        mgr.update_trust_from_outcome(
            "friend",
            goal_achieved=False,
            recommendation_valid=False,
        )
        assert mgr.get_trust("friend") < initial


class TestDiscovery:
    def _make_manager(self):
        mgr = AffiliationManager()
        mgr.add(
            FamilyAffiliation(
                affiliation_id="f1",
                affiliation_type="family",
                target_id="spouse",
                target_name="Spouse",
                trust_level=1.0,
                permissions=("financial", "travel"),
                metadata={},
                branch="creation",
                relation="spouse",
            )
        )
        mgr.add(
            EmploymentAffiliation(
                affiliation_id="e1",
                affiliation_type="employment",
                target_id="openai",
                target_name="OpenAI",
                trust_level=0.8,
                permissions=("career_planning",),
                metadata={},
                role="Engineer",
                start_date="",
                end_date="",
                status="active",
            )
        )
        mgr.add(
            EducationAffiliation(
                affiliation_id="ed1",
                affiliation_type="education",
                target_id="stanford",
                target_name="Stanford",
                trust_level=0.85,
                metadata={},
                institution="Stanford",
                program="CS",
                degree="MS",
                status="completed",
            )
        )
        mgr.add(
            Affiliation(
                affiliation_id="h1",
                affiliation_type="patient",
                target_id="hospital",
                target_name="City Hospital",
                trust_level=0.9,
                permissions=("medical",),
                metadata={},
            )
        )
        return mgr

    def test_discover_job(self):
        mgr = self._make_manager()
        participants = mgr.discover_participants("accept new job")
        types = [a.affiliation_type for a in participants]
        assert "employment" in types
        assert "family" not in types

    def test_discover_medical(self):
        mgr = self._make_manager()
        participants = mgr.discover_participants("find a hospital")
        types = [a.affiliation_type for a in participants]
        assert "patient" in types

    def test_discover_unknown_falls_back(self):
        mgr = self._make_manager()
        participants = mgr.discover_participants("xyzzy_unknown")
        assert len(participants) >= 1

    def test_discover_sorted_by_trust(self):
        mgr = self._make_manager()
        participants = mgr.discover_participants("accept job")
        trusts = [mgr.get_trust(a.target_id) for a in participants]
        assert trusts == sorted(trusts, reverse=True)

    def test_discover_respects_trust_floor(self):
        mgr = AffiliationManager()
        mgr.add(
            EmploymentAffiliation(
                affiliation_id="e1",
                affiliation_type="employment",
                target_id="shady",
                target_name="Shady Corp",
                trust_level=0.1,
                metadata={},
                role="Intern",
                start_date="",
                end_date="",
                status="active",
            )
        )
        participants = mgr.discover_participants("apply for job")
        assert len(participants) == 0

    def test_participants_alias(self):
        mgr = self._make_manager()
        p1 = mgr.participants("accept job")
        p2 = mgr.discover_participants("accept job")
        assert p1 == p2


class TestSerialization:
    def test_roundtrip(self):
        mgr = AffiliationManager()
        mgr.add(
            FamilyAffiliation(
                affiliation_id="f1",
                affiliation_type="family",
                target_id="spouse",
                target_name="Spouse",
                trust_level=1.0,
                permissions=("financial",),
                metadata={},
                branch="creation",
                relation="spouse",
            )
        )
        mgr.add(
            EmploymentAffiliation(
                affiliation_id="e1",
                affiliation_type="employment",
                target_id="openai",
                target_name="OpenAI",
                trust_level=0.8,
                permissions=("career_planning",),
                metadata={},
                role="Engineer",
                start_date="2026-01-01",
                end_date="",
                status="active",
            )
        )

        data = mgr.to_dict()
        mgr2 = AffiliationManager.from_dict(data)

        assert mgr2.count() == 2
        assert mgr2.get_trust("spouse") == 1.0
        assert mgr2.get_trust("openai") == 0.8
        f1 = mgr2.get("f1")
        assert isinstance(f1, FamilyAffiliation)
        assert f1.branch == "creation"
        e1 = mgr2.get("e1")
        assert isinstance(e1, EmploymentAffiliation)
        assert e1.role == "Engineer"

    def test_roundtrip_preserves_trust_evolution(self):
        mgr = AffiliationManager()
        mgr.add(
            Affiliation(
                affiliation_id="a1",
                affiliation_type="employer",
                target_id="o1",
                target_name="OpenAI",
                trust_level=0.7,
                metadata={},
            )
        )
        mgr.update_trust_from_outcome("o1", goal_achieved=True, obligation_met=True)
        evolved_trust = mgr.get_trust("o1")

        data = mgr.to_dict()
        mgr2 = AffiliationManager.from_dict(data)
        assert mgr2.get_trust("o1") == evolved_trust

    def test_empty_roundtrip(self):
        mgr = AffiliationManager()
        data = mgr.to_dict()
        mgr2 = AffiliationManager.from_dict(data)
        assert mgr2.count() == 0
