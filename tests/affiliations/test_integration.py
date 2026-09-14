import pytest
from src.monkey_brain.kernel.affiliations.trust import TrustEngine
from src.monkey_brain.kernel.affiliations.affiliation import Affiliation
from src.monkey_brain.kernel.affiliations.family import FamilyAffiliation
from src.monkey_brain.kernel.affiliations.employment import EmploymentAffiliation
from src.monkey_brain.kernel.affiliations.education import EducationAffiliation
from src.monkey_brain.kernel.affiliations.manager import AffiliationManager
from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor


class TestAffiliationIntegration:
    def test_actor_with_full_affiliations(self):
        actor = CognitiveActor(entity_id="alice", objective="cost", goals=["buy_milk"])

        actor.affiliations.add(
            FamilyAffiliation(
                affiliation_id="f1",
                affiliation_type="family",
                target_id="bob",
                target_name="Bob (Spouse)",
                trust_level=1.0,
                permissions=("financial", "travel"),
                policies=(),
                priority=0,
                valid_from="",
                valid_until="",
                metadata={},
                branch="creation",
                relation="spouse",
            )
        )
        actor.affiliations.add(
            EmploymentAffiliation(
                affiliation_id="e1",
                affiliation_type="employment",
                target_id="openai",
                target_name="OpenAI",
                trust_level=0.8,
                permissions=("career_planning",),
                policies=(),
                priority=1,
                valid_from="2026-01-01",
                valid_until="",
                metadata={},
                role="Engineer",
                start_date="2026-01-01",
                end_date="",
                status="active",
            )
        )
        actor.affiliations.add(
            EducationAffiliation(
                affiliation_id="ed1",
                affiliation_type="education",
                target_id="stanford",
                target_name="Stanford",
                trust_level=0.85,
                permissions=("research",),
                policies=(),
                priority=2,
                valid_from="2020-09-01",
                valid_until="2022-06-15",
                metadata={},
                institution="Stanford",
                program="CS",
                degree="MS",
                status="completed",
            )
        )

        assert len(actor.affiliations.all()) == 3
        assert len(actor.affiliations.by_type("family")) == 1
        assert len(actor.affiliations.by_type("employment")) == 1
        assert len(actor.affiliations.by_type("education")) == 1

    def test_trust_evolution(self):
        actor = CognitiveActor(entity_id="alice")
        actor.affiliations.add(
            EmploymentAffiliation(
                affiliation_id="e1",
                affiliation_type="employment",
                target_id="employer",
                target_name="Employer",
                trust_level=0.7,
                metadata={},
                role="Eng",
                start_date="",
                end_date="",
                status="active",
            )
        )
        initial = actor.affiliations.get_trust("employer")
        # AffiliationManager.get_trust() reads Affiliation.trust_level
        # directly whenever a real affiliation exists (manager.py's own
        # docstring: "the same field every live caller reads... rather
        # than a separate trust store that could drift from it") --
        # trust_engine.update_from_outcome() only ever writes to
        # trust_engine's own separate dict, which get_trust() falls back
        # to ONLY when no affiliation is on file. update_trust_from_
        # outcome() is the real, documented "Primary trust evolution
        # mechanism" for the case (this one) where a real affiliation
        # exists -- it applies the same compute_delta() formula but
        # writes the result back to the affiliation's own trust_level.
        actor.affiliations.update_trust_from_outcome(
            "employer",
            goal_achieved=True,
            obligation_met=True,
        )
        after = actor.affiliations.get_trust("employer")
        assert after > initial

    def test_discover_participants(self):
        actor = CognitiveActor(entity_id="alice")
        actor.affiliations.add(
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
        actor.affiliations.add(
            EmploymentAffiliation(
                affiliation_id="e1",
                affiliation_type="employment",
                target_id="employer",
                target_name="Employer",
                trust_level=0.7,
                metadata={},
                role="Eng",
                start_date="",
                end_date="",
                status="active",
            )
        )
        participants = actor.affiliations.discover_participants("accept_job")
        assert len(participants) >= 1

    def test_backward_compatibility(self):
        actor = CognitiveActor(entity_id="old_actor")
        assert len(actor.affiliations.all()) == 0
        assert actor.affiliations.get_trust("anyone") == 0.5
