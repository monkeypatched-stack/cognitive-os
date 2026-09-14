"""GDPRAgent — GDPR (EU 2016/679) compliance checker.

Signals consumed from context["data_signals"] / context["system_attributes"]:
  has_pii                   — data contains personal identifiable information
  lawful_basis              — truthy string (consent/contract/legal_obligation/vital/public/legitimate)
  consent_obtained          — explicit, granular consent recorded
  consent_withdrawable      — mechanism exists for data subjects to withdraw consent
  cross_border_transfer     — data transferred outside EEA
  adequacy_decision         — destination country has EC adequacy decision, OR SCCs in place
  retention_period_defined  — explicit retention schedule exists
  right_to_erasure_enabled  — deletion workflow implemented
  right_to_access_enabled   — subject access request workflow implemented
  dpo_designated            — Data Protection Officer appointed (required for high-risk processing)
  high_volume_processing    — processes data of ≥ 250 employees or regular/systematic monitoring
  dpia_completed            — Data Protection Impact Assessment on file
  breach_notification_proc  — documented 72-hour DPA notification procedure
  children_data             — processes data of subjects under 16
  parental_consent          — verifiable parental/guardian consent obtained
  profiling_enabled         — automated profiling/decision-making in use
  human_review_available    — right to human review of automated decisions
  privacy_by_design         — data minimisation, pseudonymisation applied at design time
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent


class GDPRAgent(BaseComplianceAgent):
    agent_type = "compliance_gdpr"
    description = "GDPR compliance agent — validates EU 2016/679 data protection obligations"
    standard = "GDPR"
    opa_policy_path = "compliance/gdpr"

    RULES = [
        # trigger_key              check_key                 severity    article          description                                                          remediation
        (
            "has_pii",
            "lawful_basis",
            "CRITICAL",
            "Art. 6",
            "PII processed without a documented lawful basis",
            "Establish and document a lawful basis (consent, contract, legal obligation, legitimate interest, etc.)",
        ),
        (
            "has_pii",
            "privacy_by_design",
            "HIGH",
            "Art. 25",
            "Data protection not embedded at design time",
            "Apply data minimisation and pseudonymisation from the design phase",
        ),
        (
            "has_pii",
            "retention_period_defined",
            "HIGH",
            "Art. 5(1)(e)",
            "No retention schedule defined for personal data",
            "Define and enforce a documented data retention and deletion schedule",
        ),
        (
            "has_pii",
            "right_to_erasure_enabled",
            "HIGH",
            "Art. 17",
            "No right-to-erasure workflow implemented",
            "Implement a deletion pipeline that can remove all personal data on request",
        ),
        (
            "has_pii",
            "right_to_access_enabled",
            "HIGH",
            "Art. 15",
            "No subject access request workflow implemented",
            "Implement a SAR process returning all held personal data within 30 days",
        ),
        (
            "cross_border_transfer",
            "adequacy_decision",
            "CRITICAL",
            "Art. 44-49",
            "Personal data transferred outside EEA without adequate safeguards",
            "Obtain EC adequacy decision, implement SCCs, or use BCRs before transferring data",
        ),
        (
            "high_volume_processing",
            "dpa_completed",
            "HIGH",
            "Art. 30",
            "High-volume processor lacks Records of Processing Activities",
            "Maintain an Article 30 RoPA document",
        ),
        (
            "high_volume_processing",
            "dpia_completed",
            "HIGH",
            "Art. 35",
            "High-risk processing without a completed DPIA",
            "Conduct and document a Data Protection Impact Assessment",
        ),
        (
            "has_pii",
            "breach_notification_proc",
            "HIGH",
            "Art. 33-34",
            "No documented 72-hour breach notification procedure",
            "Document a DPA breach notification procedure with 72-hour SLA and affected-subject notification path",
        ),
        (
            "children_data",
            "parental_consent",
            "CRITICAL",
            "Art. 8",
            "Processing children's data without verifiable parental consent",
            "Implement age verification and verifiable parental/guardian consent collection",
        ),
        (
            "profiling_enabled",
            "human_review_available",
            "HIGH",
            "Art. 22",
            "Automated profiling without right to human review",
            "Implement a human review pathway for all automated decisions with legal or significant effect",
        ),
        (
            "has_pii",
            "consent_withdrawable",
            "HIGH",
            "Art. 7(3)",
            "Consent collected but no withdrawal mechanism exists",
            "Provide an equally easy mechanism to withdraw consent as to give it",
        ),
    ]
