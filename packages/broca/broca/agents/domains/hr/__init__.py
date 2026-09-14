"""Human Resources agents — Employee, Recruiting, Interview, Payroll, Leave, Benefits, Performance, Onboarding, Offboarding, Organization."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.hr")


class EmployeeAgent(BaseDDDAgent):
    agent_type = "employee"
    description = "Manages employee records, profiles, and lifecycle"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "employee_id": context.get("employee_id", ""),
            "query": context.get("query", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"employee.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"employee.{decision['operation']}", "success": True}


class RecruitingAgent(BaseDDDAgent):
    agent_type = "recruiting"
    description = "Manages job postings, candidate pipelines, and hiring workflow"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "post"),
            "position": context.get("position", {}),
            "pipeline": context.get("pipeline", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"recruiting.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"recruiting.{decision['operation']}", "success": True}


class InterviewAgent(BaseDDDAgent):
    agent_type = "interview"
    description = "Schedules interviews, generates questions, and evaluates candidates"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_id": context.get("candidate_id", ""),
            "position": context.get("position", ""),
            "interview_type": context.get("interview_type", "technical"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "interview.schedule",
            "candidate_id": perception.get("candidate_id", ""),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "interview.schedule",
            "success": True,
            "interview_id": f"iv-{decision.get('candidate_id', '')[:8]}",
        }


class PayrollAgent(BaseDDDAgent):
    agent_type = "payroll"
    description = "Processes payroll, deductions, and tax calculations"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "process"),
            "pay_period": context.get("pay_period", ""),
            "employee_ids": context.get("employee_ids", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"payroll.{perception['operation']}",
            "employee_count": len(perception.get("employee_ids", [])),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"payroll.{decision['operation']}",
            "success": True,
            "processed": decision.get("employee_count", 0),
        }


class LeaveAgent(BaseDDDAgent):
    agent_type = "leave"
    description = "Manages leave requests, balances, and accrual policies"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "request"),
            "employee_id": context.get("employee_id", ""),
            "leave_type": context.get("leave_type", "vacation"),
            "days": context.get("days", 1),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"leave.{perception['operation']}",
            "approved": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"leave.{decision['operation']}",
            "success": decision.get("approved", True),
        }


class BenefitsAgent(BaseDDDAgent):
    agent_type = "benefits"
    description = "Manages employee benefits enrollment and plans"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "enroll"),
            "employee_id": context.get("employee_id", ""),
            "plan": context.get("plan", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"benefits.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"benefits.{decision['operation']}", "success": True}


class PerformanceAgent(BaseDDDAgent):
    agent_type = "performance"
    description = "Manages performance reviews, goals, and feedback"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "employee_id": context.get("employee_id", ""),
            "operation": context.get("operation", "review"),
            "period": context.get("period", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"performance.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"performance.{decision['operation']}", "success": True}


class OnboardingAgent(BaseDDDAgent):
    agent_type = "onboarding"
    description = "Manages new employee onboarding workflows and checklists"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "employee_id": context.get("employee_id", ""),
            "department": context.get("department", ""),
            "checklist": context.get("checklist", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "onboarding.progress",
            "completed_items": 0,
            "total_items": len(perception.get("checklist", [])),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": "onboarding.progress", "success": True, "progress": 0}


class OffboardingAgent(BaseDDDAgent):
    agent_type = "offboarding"
    description = "Manages employee offboarding, asset recovery, and access revocation"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "employee_id": context.get("employee_id", ""),
            "last_day": context.get("last_day", ""),
            "assets": context.get("assets", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "offboarding.process",
            "employee_id": perception.get("employee_id", ""),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": "offboarding.process", "success": True}


class OrganizationAgent(BaseDDDAgent):
    agent_type = "organization"
    description = "Manages org structure, departments, and reporting lines"
    ddd_layer = "domain"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "department": context.get("department", ""),
            "query": context.get("query", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"organization.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"organization.{decision['operation']}", "success": True}
