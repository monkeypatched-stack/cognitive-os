"""IEC61508Agent — IEC 61508:2010 Functional Safety compliance checker.

Covers E/E/PE (Electrical/Electronic/Programmable Electronic) safety-related systems.

Safety Integrity Levels (SIL):
  SIL 1 — PFD 10⁻² to 10⁻¹  (lowest)
  SIL 2 — PFD 10⁻³ to 10⁻²
  SIL 3 — PFD 10⁻⁴ to 10⁻³
  SIL 4 — PFD 10⁻⁵ to 10⁻⁴  (highest)

Signals consumed from context["data_signals"] / context["system_attributes"]:
  has_safety_function         — system implements one or more safety functions
  sil_assessment_performed    — SIL assessment documented (LOPA, ETA, or FMEDA)
  sil_target_defined          — required SIL level explicitly stated
  sil_achieved_verified       — achieved SIL verified against target through analysis
  hazard_analysis_done        — Hazard and Risk Analysis (HARA) completed
  functional_safety_plan      — Functional Safety Management Plan documented (Part 1 §6)
  hardware_fault_tolerance    — HFT (Hardware Fault Tolerance) verified for target SIL
  diagnostic_coverage         — diagnostic coverage ≥ 60% SIL1, 90% SIL2, 99% SIL3/4
  systematic_capability       — systematic capability (SC) ≥ target SIL
  proof_test_interval         — proof test interval defined and scheduled
  common_cause_failure_analysis — CCF analysis (beta factor or MooN analysis) completed
  safety_function_response_time — SFF response time verified ≤ process safety time
  independence_of_verification  — safety functions verified by an independent party
  software_safety_lifecycle   — software developed under IEC 61508-3 software safety lifecycle
  formal_methods_sil3_sil4    — formal methods used for SIL 3/4 software development
  safety_manual_available     — safety manual for the component/subsystem available
  proven_in_use_evidence      — prior-use evidence documented (if claiming prior use route)
  modification_procedure      — formal modification and change management procedure for safety systems
  functional_safety_audit     — pre-deployment functional safety assessment completed
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent


class IEC61508Agent(BaseComplianceAgent):
    agent_type = "compliance_iec61508"
    description = "IEC 61508:2010 compliance agent — functional safety of E/E/PE safety-related systems"
    standard = "IEC 61508:2010"
    opa_policy_path = "compliance/iec61508"

    RULES = [
        # Hazard and Risk Assessment
        (
            "has_safety_function",
            "hazard_analysis_done",
            "CRITICAL",
            "IEC 61508-1 §7.4",
            "No Hazard and Risk Analysis completed for the safety-related system",
            "Conduct and document a HARA using ETA, FTA, or equivalent; establish tolerable risk criteria",
        ),
        (
            "has_safety_function",
            "functional_safety_plan",
            "HIGH",
            "IEC 61508-1 §6",
            "No Functional Safety Management Plan documented",
            "Prepare a Functional Safety Management Plan covering the full safety lifecycle, roles, and activities",
        ),
        # SIL Assessment and Verification
        (
            "has_safety_function",
            "sil_assessment_performed",
            "CRITICAL",
            "IEC 61508-1 §7.6",
            "SIL assessment not performed or documented",
            "Perform and document SIL assessment (LOPA, FMEDA, or equivalent) prior to design",
        ),
        (
            "has_safety_function",
            "sil_target_defined",
            "CRITICAL",
            "IEC 61508-1 §7.6",
            "Required SIL not explicitly specified",
            "State the required SIL for each safety function in the Safety Requirements Specification",
        ),
        (
            "has_safety_function",
            "sil_achieved_verified",
            "CRITICAL",
            "IEC 61508-1 §7.6",
            "Achieved SIL not verified against target through quantitative analysis",
            "Verify achieved SIL through FMEDA or equivalent; demonstrate PFD/PFH within SIL band",
        ),
        # Hardware
        (
            "has_safety_function",
            "hardware_fault_tolerance",
            "HIGH",
            "IEC 61508-2 §7.4.4",
            "Hardware Fault Tolerance (HFT) not verified for target SIL",
            "Verify HFT meets Part 2 Table 3 requirements for the target SIL; use 1oo2 or TMR architectures for SIL 3/4",
        ),
        (
            "has_safety_function",
            "diagnostic_coverage",
            "HIGH",
            "IEC 61508-2 §7.4.4",
            "Diagnostic coverage insufficient for target SIL",
            "Achieve ≥60% DC for SIL1, ≥90% for SIL2, ≥99% for SIL3/4; document DC calculation in FMEDA",
        ),
        (
            "has_safety_function",
            "common_cause_failure_analysis",
            "HIGH",
            "IEC 61508-2 §7.4.8",
            "Common Cause Failure analysis not completed",
            "Conduct CCF analysis (IEC 61508-6 Annex D beta factor or MooN architecture analysis)",
        ),
        # Systematic Capability
        (
            "has_safety_function",
            "systematic_capability",
            "CRITICAL",
            "IEC 61508-2 §7.4.2",
            "Systematic Capability of components not verified to match target SIL",
            "Only use components/subsystems with certified SC ≥ target SIL; obtain functional safety certificates",
        ),
        # Proof Test and Maintenance
        (
            "has_safety_function",
            "proof_test_interval",
            "HIGH",
            "IEC 61508-1 §7.4",
            "Proof test interval not defined or not aligned with PFD calculation",
            "Define a proof test interval consistent with the PFD target; schedule tests before interval expiry",
        ),
        (
            "has_safety_function",
            "safety_function_response_time",
            "HIGH",
            "IEC 61508-1 §7.6",
            "Safety function response time not verified to be ≤ process safety time",
            "Document the process safety time and verify the safety function total response time (including diagnostics) is within it",
        ),
        # Software (Part 3)
        (
            "has_safety_function",
            "software_safety_lifecycle",
            "CRITICAL",
            "IEC 61508-3 §7",
            "Software not developed under IEC 61508-3 software safety lifecycle",
            "Apply the IEC 61508-3 software safety lifecycle including requirements, design, implementation, testing, and validation phases",
        ),
        (
            "has_sil3_sil4_software",
            "formal_methods_sil3_sil4",
            "HIGH",
            "IEC 61508-3 §7.4.3",
            "Formal methods not applied to SIL 3/4 software",
            "Apply formal specification and verification methods (e.g., Z notation, B-method) for all SIL 3/4 software",
        ),
        # Documentation and Independence
        (
            "has_safety_function",
            "safety_manual_available",
            "HIGH",
            "IEC 61508-2 §7.4",
            "No safety manual available for the safety-related subsystem",
            "Produce a safety manual documenting constraints, assumptions, installation requirements, and proof test procedures",
        ),
        (
            "has_safety_function",
            "independence_of_verification",
            "HIGH",
            "IEC 61508-1 §8.3",
            "Safety function not verified by an independent party",
            "Engage an independent functional safety assessor (FSA) with competence ≥ target SIL",
        ),
        (
            "has_safety_function",
            "functional_safety_audit",
            "CRITICAL",
            "IEC 61508-1 §8",
            "Pre-deployment functional safety assessment not completed",
            "Complete a Functional Safety Assessment (FSA) before commissioning any SIL-rated safety function",
        ),
        (
            "has_safety_function",
            "modification_procedure",
            "HIGH",
            "IEC 61508-1 §6.2.6",
            "No formal modification procedure for safety-related systems",
            "Establish a modification management procedure; repeat applicable lifecycle phases and re-assess SIL on change",
        ),
    ]
