"""FDAAgent — FDA 21 CFR Part 11 and 21 CFR Part 820 compliance checker.

Covers electronic records (Part 11) and Quality System Regulation (Part 820).

Signals consumed from context["data_signals"] / context["system_attributes"]:
  uses_electronic_records     — system stores or transmits FDA-regulated electronic records
  audit_trail_enabled         — system-generated audit trail (who/what/when) active and unalterable
  audit_trail_reviewed        — audit trails reviewed regularly for anomalies
  electronic_signatures_21cfr — e-signatures compliant with 21 CFR 11.50 (meaning, date, binding)
  access_control_validated    — access controls validated to prevent unauthorised record alteration
  system_validated            — computer system validation (CSV) completed per GAMP 5 or equivalent
  validation_documented       — IQ/OQ/PQ documentation on file
  change_control_process      — formal change control process for validated systems
  backup_and_recovery         — backup and disaster recovery procedures validated
  quality_management_system   — QMS established per 21 CFR 820 Subpart B
  design_controls_documented  — design history file (DHF) maintained (820.30)
  capa_process_active         — Corrective and Preventive Action system operational
  complaint_handling_process  — customer complaint handling procedure per 820.198
  labelling_controlled        — device labelling controlled under document management
  supplier_approval_process   — supplier/contractor qualification and approval procedure
  production_controls         — production and process controls documented per 820.70
  nonconforming_product_proc  — nonconforming product handling procedure per 820.90
  records_retention_defined   — records retention periods defined (≥ 2 years for devices)
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent


class FDAAgent(BaseComplianceAgent):
    agent_type = "compliance_fda"
    description = "FDA 21 CFR Part 11 / Part 820 compliance agent — electronic records and quality system regulation"
    standard = "FDA 21 CFR"
    opa_policy_path = "compliance/fda"

    RULES = [
        # 21 CFR Part 11 — Electronic Records and Signatures
        (
            "uses_electronic_records",
            "audit_trail_enabled",
            "CRITICAL",
            "21 CFR 11.10(e)",
            "Electronic records lack a compliant audit trail",
            "Enable a system-generated, computer-generated audit trail capturing who, what, and when; make it unalterable by ordinary users",
        ),
        (
            "uses_electronic_records",
            "audit_trail_reviewed",
            "HIGH",
            "21 CFR 11.10(e)",
            "Audit trails not reviewed regularly",
            "Establish a documented procedure for periodic audit trail review and exception investigation",
        ),
        (
            "uses_electronic_records",
            "electronic_signatures_21cfr",
            "HIGH",
            "21 CFR 11.50",
            "Electronic signatures non-compliant (missing meaning, date, or binding)",
            "Ensure e-signatures include the printed name, date/time, and meaning (e.g., reviewed, approved, executed)",
        ),
        (
            "uses_electronic_records",
            "access_control_validated",
            "HIGH",
            "21 CFR 11.10(d)",
            "Access controls to electronic records not validated",
            "Validate that only authorised users can create, modify, or delete regulated electronic records",
        ),
        (
            "uses_electronic_records",
            "system_validated",
            "CRITICAL",
            "21 CFR 11.10(a)",
            "Computer system not validated for intended use",
            "Complete and document computer system validation (CSV) per GAMP 5; include IQ, OQ, PQ protocols",
        ),
        (
            "uses_electronic_records",
            "validation_documented",
            "HIGH",
            "21 CFR 11.10(a)",
            "Validation documentation (IQ/OQ/PQ) not on file",
            "Maintain complete validation documentation including test protocols, results, and discrepancy resolutions",
        ),
        (
            "system_validated",
            "change_control_process",
            "HIGH",
            "21 CFR 11.10(k)",
            "No change control process for validated systems",
            "All changes to validated systems must go through a formal change control process with re-validation assessment",
        ),
        (
            "uses_electronic_records",
            "backup_and_recovery",
            "HIGH",
            "21 CFR 11.10(c)",
            "Backup and recovery procedures not in place or not validated",
            "Validate backup and disaster recovery; test restoration of electronic records at defined intervals",
        ),
        # 21 CFR Part 820 — Quality System Regulation
        (
            "is_medical_device",
            "quality_management_system",
            "CRITICAL",
            "21 CFR 820.5",
            "No Quality Management System established",
            "Establish a QMS meeting 21 CFR 820 Subpart B requirements; consider ISO 13485 alignment",
        ),
        (
            "is_medical_device",
            "design_controls_documented",
            "CRITICAL",
            "21 CFR 820.30",
            "No Design History File or design controls process",
            "Establish design controls including design input, output, review, verification, validation, and transfer records",
        ),
        (
            "is_medical_device",
            "capa_process_active",
            "HIGH",
            "21 CFR 820.100",
            "No Corrective and Preventive Action system",
            "Implement a CAPA system that identifies root cause, defines corrective/preventive actions, and verifies effectiveness",
        ),
        (
            "is_medical_device",
            "complaint_handling_process",
            "HIGH",
            "21 CFR 820.198",
            "No complaint handling procedure",
            "Establish a procedure for receiving, reviewing, and evaluating complaints; MDR reporting where required",
        ),
        (
            "is_medical_device",
            "supplier_approval_process",
            "HIGH",
            "21 CFR 820.50",
            "No supplier/contractor qualification and approval procedure",
            "Establish supplier evaluation, approval, and monitoring criteria; maintain an approved supplier list",
        ),
        (
            "is_medical_device",
            "production_controls",
            "HIGH",
            "21 CFR 820.70",
            "Production and process controls not documented",
            "Document and implement controls to ensure product conforms to specifications throughout production",
        ),
        (
            "is_medical_device",
            "nonconforming_product_proc",
            "HIGH",
            "21 CFR 820.90",
            "No nonconforming product handling procedure",
            "Establish procedures to identify, document, evaluate, segregate, and disposition nonconforming product",
        ),
        (
            "is_medical_device",
            "records_retention_defined",
            "HIGH",
            "21 CFR 820.180",
            "Records retention periods not defined (minimum 2 years for devices)",
            "Define record retention schedules meeting the 2-year minimum requirement; implement controls to prevent premature destruction",
        ),
        (
            "is_medical_device",
            "labelling_controlled",
            "HIGH",
            "21 CFR 820.120",
            "Device labelling not under document control",
            "Control labelling under the QMS document management system; prevent use of obsolete labels",
        ),
    ]
