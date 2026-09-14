"""GxPAgent — Good Practice (GxP) compliance checker.

Covers GMP (Good Manufacturing Practice), GCP (Good Clinical Practice),
and GLP (Good Laboratory Practice) with a focus on data integrity using
the ALCOA+ framework.

ALCOA+: Attributable, Legible, Contemporaneous, Original, Accurate
      + Complete, Consistent, Enduring, Available

Signals consumed from context["data_signals"] / context["system_attributes"]:
  has_gxp_data                — system processes or stores GxP-regulated data
  records_attributable        — every record has an identified, authenticated author
  records_legible             — records are readable immediately and long-term
  records_contemporaneous     — data recorded at the time of the activity, not retrospectively
  records_original            — original records or certified true copies retained
  records_accurate            — records reflect actual observed values
  records_complete            — no missing data; all required fields populated
  records_consistent          — internal consistency (chronological, logical)
  records_enduring            — durable media; no pencil, erasable ink, or volatile storage
  records_available           — accessible to inspectors and authorised personnel
  audit_trail_gxp             — computerised system audit trail capturing all record changes
  change_justification        — all changes to GxP records include reason and author
  no_backdating               — system prevents or detects backdated entries
  validation_protocol         — prospective validation protocol on file (PQ/OQ/IQ)
  validation_report           — validation summary report signed by QA
  change_control_gxp          — formal change control for GxP-regulated systems and processes
  sop_for_all_processes       — SOPs exist for all GxP-critical processes
  sop_training_documented     — training on applicable SOPs documented per person
  deviation_handling          — deviation/OOS/OOT investigation process active
  review_by_second_person     — critical records reviewed by a second qualified person
  quality_unit_oversight      — Quality Unit has authority to approve/reject all GxP activities
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent


class GxPAgent(BaseComplianceAgent):
    agent_type = "compliance_gxp"
    description = "GxP compliance agent — validates ALCOA+ data integrity and GMP/GCP/GLP requirements"
    standard = "GxP / ALCOA+"
    opa_policy_path = "compliance/gxp"

    RULES = [
        # ALCOA+ Data Integrity
        (
            "has_gxp_data",
            "records_attributable",
            "CRITICAL",
            "ALCOA+ / FDA DI Guidance",
            "Records cannot be attributed to the person who created them",
            "Implement individual login accounts; prohibit shared credentials; link every record to the authenticated user",
        ),
        (
            "has_gxp_data",
            "records_legible",
            "HIGH",
            "ALCOA+",
            "Records are not permanently legible",
            "Use indelible ink or electronic records; scan and verify legibility; enforce field-length and character validations",
        ),
        (
            "has_gxp_data",
            "records_contemporaneous",
            "CRITICAL",
            "ALCOA+",
            "Data not recorded at the time of the activity (backdating risk)",
            "Timestamp all entries at creation; configure systems to reject or flag entries with past timestamps",
        ),
        (
            "has_gxp_data",
            "records_original",
            "HIGH",
            "ALCOA+",
            "Original records not retained (copies without certification)",
            "Retain originals or certified true copies; document the certification process",
        ),
        (
            "has_gxp_data",
            "records_accurate",
            "CRITICAL",
            "ALCOA+",
            "Records do not accurately reflect actual observations",
            "Implement range checks, calibration controls, and instrument-to-record traceability",
        ),
        (
            "has_gxp_data",
            "records_complete",
            "HIGH",
            "ALCOA+",
            "Records have missing or blank fields without documented justification",
            "Validate required fields; require justification (N/A, not applicable) for blank entries",
        ),
        (
            "has_gxp_data",
            "records_consistent",
            "HIGH",
            "ALCOA+",
            "Records have internal inconsistencies (e.g., timestamps out of sequence)",
            "Implement sequence and chronology checks; review audit trails for logical inconsistencies",
        ),
        (
            "has_gxp_data",
            "records_enduring",
            "HIGH",
            "ALCOA+",
            "Records stored on volatile or non-durable media",
            "Store GxP records on validated, durable media with defined retention and backup strategy",
        ),
        (
            "has_gxp_data",
            "records_available",
            "HIGH",
            "ALCOA+",
            "Records not accessible for inspection without undue delay",
            "Index and retain records in a system that supports retrieval within the regulatory timeframe (typically 15+ years)",
        ),
        # Computerised Systems
        (
            "has_gxp_data",
            "audit_trail_gxp",
            "CRITICAL",
            "21 CFR 11 / EU Annex 11 §9",
            "GxP computerised system lacks a compliant audit trail",
            "Configure system audit trail to capture original value, new value, user, date/time, and reason for change",
        ),
        (
            "has_gxp_data",
            "change_justification",
            "HIGH",
            "EU Annex 11 §10",
            "Changes to GxP records not accompanied by reason and author",
            "Require a reason-for-change field and authenticated author on every data modification",
        ),
        (
            "has_gxp_data",
            "no_backdating",
            "CRITICAL",
            "FDA Data Integrity Guidance",
            "System allows or cannot detect backdated entries",
            "Configure system controls to prevent or alarm on timestamp manipulation; include in periodic audit trail review",
        ),
        # Validation
        (
            "has_gxp_data",
            "validation_protocol",
            "CRITICAL",
            "EU Annex 11 §4 / GAMP 5",
            "No prospective validation protocol for GxP computerised system",
            "Create and approve IQ/OQ/PQ protocols before system use; validate all intended uses",
        ),
        (
            "has_gxp_data",
            "validation_report",
            "HIGH",
            "EU Annex 11 §4",
            "Validation summary report not signed by Quality Assurance",
            "Complete and QA-sign a validation summary report before system release to production",
        ),
        (
            "has_gxp_data",
            "change_control_gxp",
            "HIGH",
            "EU Annex 11 §10 / GAMP 5",
            "No change control process for validated GxP systems",
            "Implement a formal change control process requiring impact assessment and re-validation as appropriate",
        ),
        # Procedural Controls
        (
            "has_gxp_data",
            "sop_for_all_processes",
            "HIGH",
            "GMP / GCP / GLP",
            "SOPs not in place for all GxP-critical processes",
            "Write, review, approve, and version-control SOPs for every GxP-critical activity",
        ),
        (
            "has_gxp_data",
            "sop_training_documented",
            "HIGH",
            "GMP Annex 15",
            "SOP training not documented per person",
            "Record training completion for every applicable SOP per individual; include effectiveness checks",
        ),
        (
            "has_gxp_data",
            "deviation_handling",
            "HIGH",
            "ICH Q10 / 21 CFR 211.192",
            "No deviation or OOS/OOT investigation process",
            "Establish a deviation management system with root-cause analysis and CAPA for all GxP deviations",
        ),
        (
            "has_gxp_data",
            "review_by_second_person",
            "HIGH",
            "GMP / GLP",
            "Critical records not reviewed by a second qualified person",
            "Implement mandatory second-person review for all critical GxP records and batch release decisions",
        ),
        (
            "has_gxp_data",
            "quality_unit_oversight",
            "CRITICAL",
            "21 CFR 211.22 / EU GMP Pt I",
            "Quality Unit does not have authority over GxP activities",
            "Establish and resource a Quality Unit with independent authority to approve/reject all GxP activities and outputs",
        ),
    ]
