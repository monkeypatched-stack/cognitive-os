"""SOC2Agent — SOC 2 Type II Trust Service Criteria compliance checker.

Covers all five TSC categories:
  CC1  Control Environment
  CC6  Logical and Physical Access
  CC7  System Operations
  CC8  Change Management
  A    Availability
  C    Confidentiality
  PI   Processing Integrity
  P    Privacy

Signals consumed from context["data_signals"] / context["system_attributes"]:
  security_policy_documented    — written information security policy exists
  risk_ownership_defined        — risk owners assigned to all identified risks
  board_oversight_active        — board or executive oversight of security program
  mfa_enforced                  — MFA enforced for all user accounts
  access_provisioning_formal    — formal access provisioning and de-provisioning process
  privileged_access_reviewed    — privileged accounts reviewed quarterly
  network_segmentation          — production network segmented from non-production
  endpoint_protection           — endpoint detection and response active
  monitoring_alerting_enabled   — continuous system monitoring with alerting in place
  incident_response_plan        — documented and tested IR plan
  backup_tested                 — backups taken and restoration tested
  change_management_process     — formal change management process (ticket, approval, test, rollback)
  change_log_maintained         — all changes logged with business justification
  availability_sla_defined      — availability SLA defined and monitored
  recovery_objectives_defined   — RPO and RTO defined and tested
  data_classification_policy    — data classification policy applied to all data
  confidentiality_agreements    — NDAs/confidentiality agreements with staff and vendors
  processing_accuracy_controls  — input/output accuracy controls on all processing
  error_handling_documented     — system error handling and exception management documented
  privacy_notice_current        — current privacy notice published and accessible
  data_subject_rights_enabled   — mechanisms for data subject rights requests
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent


class SOC2Agent(BaseComplianceAgent):
    agent_type = "compliance_soc2"
    description = "SOC 2 Type II compliance agent — validates AICPA Trust Service Criteria"
    standard = "SOC 2"
    opa_policy_path = "compliance/soc2"

    RULES = [
        # CC1 — Control Environment
        (
            "has_operations",
            "security_policy_documented",
            "HIGH",
            "CC1.2",
            "No written information security policy",
            "Document, approve, and communicate an information security policy to all personnel",
        ),
        (
            "has_operations",
            "risk_ownership_defined",
            "HIGH",
            "CC1.4",
            "Risk owners not assigned",
            "Assign named risk owners for all identified risks in the risk register",
        ),
        (
            "has_operations",
            "board_oversight_active",
            "MEDIUM",
            "CC1.5",
            "No board or executive-level security oversight",
            "Establish a security steering committee with executive sponsorship and quarterly reporting",
        ),
        # CC6 — Logical and Physical Access
        (
            "has_user_access",
            "mfa_enforced",
            "CRITICAL",
            "CC6.1",
            "MFA not enforced for user accounts",
            "Enable MFA for all user accounts; enforce hardware tokens for privileged access",
        ),
        (
            "has_user_access",
            "access_provisioning_formal",
            "HIGH",
            "CC6.2",
            "No formal access provisioning/de-provisioning process",
            "Implement an access management workflow with approval, ticketing, and timely de-provisioning on termination",
        ),
        (
            "has_privileged_access",
            "privileged_access_reviewed",
            "HIGH",
            "CC6.3",
            "Privileged accounts not reviewed quarterly",
            "Schedule quarterly privileged access reviews; remove all stale grants immediately",
        ),
        (
            "has_production_systems",
            "network_segmentation",
            "HIGH",
            "CC6.6",
            "Production network not segmented from non-production",
            "Implement VLAN/firewall-based network segmentation; enforce zero-trust between environments",
        ),
        (
            "has_endpoints",
            "endpoint_protection",
            "HIGH",
            "CC6.8",
            "No endpoint detection and response (EDR) deployed",
            "Deploy an EDR solution on all corporate endpoints with centralised alerting",
        ),
        # CC7 — System Operations
        (
            "has_operations",
            "monitoring_alerting_enabled",
            "HIGH",
            "CC7.2",
            "No continuous system monitoring or alerting",
            "Implement SIEM-based continuous monitoring with alerting for anomalous activity",
        ),
        (
            "has_operations",
            "incident_response_plan",
            "HIGH",
            "CC7.3",
            "No documented and tested incident response plan",
            "Create, document, and exercise an incident response plan at least annually",
        ),
        (
            "has_data",
            "backup_tested",
            "HIGH",
            "CC7.5",
            "Backups not taken or restoration not tested",
            "Perform regular backups and document quarterly restoration tests with success metrics",
        ),
        # CC8 — Change Management
        (
            "has_systems",
            "change_management_process",
            "HIGH",
            "CC8.1",
            "No formal change management process",
            "Implement a change management process with ticket, approval, testing, and rollback plan",
        ),
        (
            "has_systems",
            "change_log_maintained",
            "MEDIUM",
            "CC8.1",
            "Change log not maintained",
            "Log all changes with timestamp, author, business justification, and test result",
        ),
        # Availability
        (
            "has_availability_commitment",
            "availability_sla_defined",
            "HIGH",
            "A1.1",
            "No availability SLA defined or monitored",
            "Define availability targets, instrument uptime monitoring, and report against SLA monthly",
        ),
        (
            "has_availability_commitment",
            "recovery_objectives_defined",
            "HIGH",
            "A1.2",
            "RPO/RTO not defined or tested",
            "Define and test recovery point and recovery time objectives at least annually",
        ),
        # Confidentiality
        (
            "has_confidential_data",
            "data_classification_policy",
            "HIGH",
            "C1.1",
            "No data classification policy applied",
            "Define and enforce a data classification scheme (Public, Internal, Confidential, Restricted)",
        ),
        (
            "has_confidential_data",
            "confidentiality_agreements",
            "HIGH",
            "C1.2",
            "No NDAs or confidentiality agreements with staff/vendors",
            "Execute confidentiality agreements with all employees and third parties with data access",
        ),
        # Processing Integrity
        (
            "has_processing",
            "processing_accuracy_controls",
            "HIGH",
            "PI1.1",
            "No input/output accuracy controls on processing",
            "Implement checksums, reconciliation, and error-detection controls on all critical data processing",
        ),
        (
            "has_processing",
            "error_handling_documented",
            "MEDIUM",
            "PI1.4",
            "Error handling and exception management not documented",
            "Document and test error-handling procedures; ensure exceptions are logged and escalated",
        ),
        # Privacy
        (
            "collects_personal_data",
            "privacy_notice_current",
            "HIGH",
            "P2.1",
            "No current privacy notice published",
            "Publish an up-to-date privacy notice describing data collection, use, and retention practices",
        ),
        (
            "collects_personal_data",
            "data_subject_rights_enabled",
            "HIGH",
            "P6.1",
            "No mechanism for data subject rights requests",
            "Implement a rights-request workflow covering access, correction, deletion, and portability",
        ),
    ]
