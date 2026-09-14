"""ISO27001Agent — ISO/IEC 27001:2022 Information Security Management System checker.

Signals consumed from context["data_signals"] / context["system_attributes"]:
  asset_inventory_current    — information asset register exists and is up to date
  risk_assessment_current    — risk assessment performed within the last 12 months
  access_control_policy      — formal access control policy documented
  mfa_enabled                — multi-factor authentication enforced for privileged access
  least_privilege_enforced   — principle of least privilege applied to all roles
  access_review_performed    — periodic access review on record (at least annually)
  encryption_at_rest         — sensitive data encrypted at rest
  encryption_in_transit      — data encrypted in transit (TLS 1.2+)
  key_management_policy      — formal cryptographic key management policy exists
  incident_management_proc   — documented information security incident management procedure
  incident_log_exists        — incident register maintained
  supplier_assessment_done   — supplier/third-party security assessments on file
  supplier_contracts_isms    — contracts include ISMS clauses
  audit_log_enabled          — audit logging active on all critical systems
  audit_log_integrity        — audit logs protected from tampering
  business_continuity_plan   — BCP documented and tested
  vuln_management_process    — formal vulnerability management process exists
  patch_sla_defined          — patch application SLA defined (critical ≤ 30 days)
  security_training_current  — staff security awareness training completed this year
  physical_access_controlled — physical access to data centres controlled and logged
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent


class ISO27001Agent(BaseComplianceAgent):
    agent_type = "compliance_iso27001"
    description = "ISO 27001:2022 ISMS compliance agent — validates information security management controls"
    standard = "ISO/IEC 27001:2022"
    opa_policy_path = "compliance/iso27001"

    RULES = [
        # trigger_key                    check_key                     severity    clause        description                                                              remediation
        (
            "has_sensitive_data",
            "asset_inventory_current",
            "HIGH",
            "A.5.9",
            "No current information asset inventory",
            "Create and maintain an asset register including owner, classification, and location",
        ),
        (
            "has_sensitive_data",
            "risk_assessment_current",
            "HIGH",
            "Cl. 6.1.2",
            "Risk assessment not performed or older than 12 months",
            "Perform and document an ISO 27005-aligned risk assessment annually or on significant change",
        ),
        (
            "has_privileged_access",
            "mfa_enabled",
            "CRITICAL",
            "A.8.5",
            "Privileged access without multi-factor authentication",
            "Enforce MFA for all privileged and remote access accounts",
        ),
        (
            "has_user_access",
            "least_privilege_enforced",
            "HIGH",
            "A.8.2",
            "Least-privilege principle not applied",
            "Review and reduce all role permissions to the minimum required; remove stale accounts",
        ),
        (
            "has_user_access",
            "access_review_performed",
            "HIGH",
            "A.8.2",
            "No periodic access review on record",
            "Schedule and document access reviews at least annually; revoke stale grants immediately",
        ),
        (
            "has_sensitive_data",
            "encryption_at_rest",
            "HIGH",
            "A.8.24",
            "Sensitive data not encrypted at rest",
            "Apply AES-256 (or equivalent) encryption to all data stores containing classified information",
        ),
        (
            "has_data_in_transit",
            "encryption_in_transit",
            "HIGH",
            "A.8.24",
            "Data transmitted without encryption",
            "Enforce TLS 1.2+ for all network communications; disable legacy ciphers",
        ),
        (
            "uses_cryptography",
            "key_management_policy",
            "HIGH",
            "A.8.24",
            "No formal cryptographic key management policy",
            "Document key generation, storage, rotation, and destruction procedures",
        ),
        (
            "has_operations",
            "incident_management_proc",
            "HIGH",
            "A.5.26",
            "No documented security incident management procedure",
            "Create a tiered incident classification and response procedure with defined escalation paths",
        ),
        (
            "has_operations",
            "incident_log_exists",
            "MEDIUM",
            "A.5.28",
            "No security incident register maintained",
            "Maintain a chronological incident log with root-cause and lessons-learned entries",
        ),
        (
            "uses_suppliers",
            "supplier_assessment_done",
            "HIGH",
            "A.5.19",
            "Third-party supplier security not assessed",
            "Perform security due-diligence assessments on all suppliers handling sensitive information",
        ),
        (
            "uses_suppliers",
            "supplier_contracts_isms",
            "HIGH",
            "A.5.20",
            "Supplier contracts lack ISMS security clauses",
            "Include information security obligations in all supplier agreements (confidentiality, audit rights, breach notification)",
        ),
        (
            "has_operations",
            "audit_log_enabled",
            "HIGH",
            "A.8.15",
            "Audit logging not enabled on critical systems",
            "Enable tamper-evident audit logging across all systems storing or processing classified data",
        ),
        (
            "has_operations",
            "audit_log_integrity",
            "HIGH",
            "A.8.15",
            "Audit logs not protected from tampering or deletion",
            "Store audit logs in a write-once or SIEM-protected location; alert on deletion attempts",
        ),
        (
            "has_operations",
            "business_continuity_plan",
            "HIGH",
            "A.5.29",
            "No tested business continuity plan for information security",
            "Develop, document, and test a BCP that includes information security incident scenarios",
        ),
        (
            "has_systems",
            "vuln_management_process",
            "HIGH",
            "A.8.8",
            "No formal vulnerability management process",
            "Implement continuous vulnerability scanning and a formal remediation SLA process",
        ),
        (
            "has_systems",
            "security_training_current",
            "MEDIUM",
            "A.6.3",
            "Staff security awareness training not current",
            "Deliver and record annual security awareness training for all personnel with system access",
        ),
    ]
