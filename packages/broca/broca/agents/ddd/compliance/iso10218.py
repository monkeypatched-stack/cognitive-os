"""ISO10218Agent — ISO 10218-1/-2:2011 Industrial Robot Safety compliance checker.

ISO 10218-1 — Safety requirements for industrial robots: Robots
ISO 10218-2 — Safety requirements for industrial robots: Robot systems and integration

Also references ISO/TS 15066:2016 for collaborative robot (cobot) operations.

Signals consumed from context["data_signals"] / context["system_attributes"]:
  has_industrial_robot        — system includes an industrial robot
  risk_assessment_completed   — robot risk assessment per ISO 12100 documented
  collaborative_operation     — robot operates in collaborative mode (ISO/TS 15066)
  speed_power_force_limited   — speed ≤ 250mm/s, force ≤ quasi-static limits (15066 Table 1)
  safety_rated_monitored_stop — SRMS (Safety-Rated Monitored Stop) implemented
  hand_guiding_control        — hand-guiding device with enabling device and speed limit
  speed_separation_monitoring — SSM (Speed and Separation Monitoring) with safeguarded zone
  power_force_limiting        — PFL (Power and Force Limiting) system validated
  emergency_stop_cat0_or_1    — emergency stop category 0 or 1 per IEC 60204-1
  safeguarding_devices        — perimeter guards, interlocks, or light curtains in place
  workspace_defined           — robot workspace (restricted + operating) defined and marked
  operator_protection         — operator protection devices verified and tested
  control_system_safety_rated — robot control system safety-rated (SIL2 / PLd or higher)
  teach_pendant_enabling      — teach pendant has hold-to-run + three-position enabling device
  speed_limit_teaching        — speed limited to ≤ 250mm/s during teach mode
  software_safety_validated   — robot safety software validated per IEC 61508-3
  installation_commissioning  — installation verified against 10218-2 §5 before hand-over
  safety_assessment_completed — pre-deployment safety assessment documented
  operator_training_documented — operators trained on safe operation, hazards, and E-stop
  maintenance_procedure       — documented maintenance and inspection schedule with safety checks
  end_effector_risk_assessed  — end-of-arm tooling (EOAT) included in risk assessment
"""

from broca.agents.ddd.compliance._base_compliance import BaseComplianceAgent


class ISO10218Agent(BaseComplianceAgent):
    agent_type = "compliance_iso10218"
    description = "ISO 10218-1/2:2011 compliance agent — industrial robot and collaborative robot safety"
    standard = "ISO 10218 / ISO/TS 15066"
    opa_policy_path = "compliance/iso10218"

    RULES = [
        # Risk Assessment
        (
            "has_industrial_robot",
            "risk_assessment_completed",
            "CRITICAL",
            "ISO 10218-1 §4.1 / ISO 12100",
            "No risk assessment completed for the robot cell",
            "Conduct a documented risk assessment per ISO 12100 covering robot, EOAT, workpiece, environment, and operators; repeat after any significant change",
        ),
        (
            "has_industrial_robot",
            "end_effector_risk_assessed",
            "HIGH",
            "ISO 10218-2 §5.4.2",
            "End-of-arm tooling (EOAT) not included in risk assessment",
            "Extend the risk assessment to explicitly cover EOAT hazards: dropped workpieces, sharp edges, pressure, temperature",
        ),
        # Collaborative Operation (ISO/TS 15066)
        (
            "collaborative_operation",
            "speed_power_force_limited",
            "CRITICAL",
            "ISO/TS 15066 §5.4",
            "Collaborative robot does not limit speed (≤250mm/s) and contact force to quasi-static limits",
            "Validate speed, force, and power limits against ISO/TS 15066 Table 1 body-region limits; integrate certified force/torque sensors or PFL controller",
        ),
        (
            "collaborative_operation",
            "safety_rated_monitored_stop",
            "HIGH",
            "ISO/TS 15066 §5.2",
            "No Safety-Rated Monitored Stop (SRMS) for collaborative operation",
            "Implement SRMS so the robot halts when an operator enters the collaborative workspace; verify using SIL2/PLd-rated monitoring",
        ),
        (
            "collaborative_operation",
            "speed_separation_monitoring",
            "HIGH",
            "ISO/TS 15066 §5.3",
            "Speed and Separation Monitoring (SSM) not implemented for collaborative zones",
            "Implement a validated SSM system (area scanner, vision) that dynamically reduces robot speed as operator proximity decreases",
        ),
        (
            "collaborative_operation",
            "power_force_limiting",
            "HIGH",
            "ISO/TS 15066 §5.5",
            "Power and Force Limiting (PFL) system not validated",
            "Validate PFL system against ISO/TS 15066 Table 1; document test protocol, body region, and measured forces at maximum robot speed",
        ),
        # Safety Functions and Stopping
        (
            "has_industrial_robot",
            "emergency_stop_cat0_or_1",
            "CRITICAL",
            "ISO 10218-1 §5.5.2 / IEC 60204-1",
            "Emergency stop does not meet category 0 or 1 per IEC 60204-1",
            "Install and validate a Category 0 (immediate de-energisation) or Category 1 (controlled stop then de-energise) emergency stop circuit",
        ),
        (
            "has_industrial_robot",
            "control_system_safety_rated",
            "CRITICAL",
            "ISO 10218-1 §5.4",
            "Robot control system not safety-rated to at least SIL2 / PLd",
            "Use a safety-rated robot controller certified to SIL2 (IEC 62061) or PLd Cat. 3 (ISO 13849-1); obtain manufacturer certificate",
        ),
        # Workspace and Safeguarding
        (
            "has_industrial_robot",
            "workspace_defined",
            "HIGH",
            "ISO 10218-2 §5.4.4",
            "Robot restricted and operating workspace not defined or marked",
            "Define and physically mark maximum space, restricted space, and operating space; use floor marking, barriers, and interlock-protected gates",
        ),
        (
            "has_industrial_robot",
            "safeguarding_devices",
            "CRITICAL",
            "ISO 10218-2 §5.4",
            "No safeguarding devices (guards, interlocks, or light curtains) in place",
            "Install perimeter guards with interlocked gates and/or light curtains at all access points; validate to PLd / SIL2 minimum",
        ),
        (
            "has_industrial_robot",
            "operator_protection",
            "HIGH",
            "ISO 10218-2 §5.4.3",
            "Operator protection devices not verified and tested",
            "Functionally test all safety devices (E-stop, interlocks, light curtains) before handover; document test results and pass/fail criteria",
        ),
        # Teach Mode
        (
            "has_industrial_robot",
            "teach_pendant_enabling",
            "HIGH",
            "ISO 10218-1 §5.8.3",
            "Teach pendant lacks hold-to-run control or three-position enabling device",
            "Fit all teach pendants with a three-position enabling device (deadman switch) and hold-to-run control for all motion in teach mode",
        ),
        (
            "has_industrial_robot",
            "speed_limit_teaching",
            "HIGH",
            "ISO 10218-1 §5.8.2",
            "Robot speed in teach mode not limited to ≤ 250mm/s",
            "Configure teach mode to enforce a maximum speed of 250mm/s; make this limit non-defeatable by ordinary operators",
        ),
        # Software and Validation
        (
            "has_industrial_robot",
            "software_safety_validated",
            "HIGH",
            "ISO 10218-1 §5.4 / IEC 61508-3",
            "Robot safety software not validated",
            "Validate safety-relevant robot software per IEC 61508-3; maintain validation documentation including test cases and results",
        ),
        (
            "has_industrial_robot",
            "installation_commissioning",
            "HIGH",
            "ISO 10218-2 §7",
            "Installation not verified against ISO 10218-2 requirements before hand-over",
            "Complete the handover verification checklist per ISO 10218-2 §7; include safeguarding, E-stop, teach mode, and collaborative function tests",
        ),
        (
            "has_industrial_robot",
            "safety_assessment_completed",
            "CRITICAL",
            "ISO 10218-2 §7.2",
            "Pre-deployment safety assessment not documented",
            "Complete a pre-deployment safety assessment report signed by a competent person; retain as part of the technical file",
        ),
        # Human Factors
        (
            "has_industrial_robot",
            "operator_training_documented",
            "HIGH",
            "ISO 10218-2 §6.7",
            "Operator training on safe operation, hazards, and E-stop not documented",
            "Deliver and record role-specific training covering hazards, safe operating procedures, and emergency stop location/operation",
        ),
        (
            "has_industrial_robot",
            "maintenance_procedure",
            "HIGH",
            "ISO 10218-2 §6.6",
            "No documented maintenance and inspection schedule with safety checks",
            "Establish a maintenance schedule covering all safety-critical components; include lock-out/tag-out procedures and proof test of safety functions",
        ),
    ]
