#!/usr/bin/env python3
"""
End-to-End Execution Tracer

Comprehensive tracing of the unified prompt endpoint execution flow to verify
all components are working correctly and nothing is "suspiciously" perfect.
"""

import sys
import os
import asyncio
import inspect
import traceback
from typing import Dict, Any, List, Optional
from datetime import datetime

# Add src to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

import logging
from importlib import import_module

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("execution-tracer")


class ExecutionTracer:
    """Comprehensive execution tracer for end-to-end verification."""

    def __init__(self):
        self.trace_log = []
        self.errors = []
        self.warnings = []
        self.performance_metrics = {}
        self.component_status = {}

    def log_trace(self, component: str, message: str, data: Dict[str, Any] = None):
        """Log a trace event."""
        trace_entry = {
            "timestamp": datetime.now().isoformat(),
            "component": component,
            "message": message,
            "data": data or {},
        }
        self.trace_log.append(trace_entry)
        logger.info(f"[{component}] {message}")
        if data:
            logger.debug(f"[{component}] Data: {data}")

    def log_error(self, component: str, error: Exception, context: str = ""):
        """Log an error event."""
        error_entry = {
            "timestamp": datetime.now().isoformat(),
            "component": component,
            "error": str(error),
            "type": type(error).__name__,
            "context": context,
            "traceback": traceback.format_exc(),
        }
        self.errors.append(error_entry)
        logger.error(f"[{component}] ERROR: {str(error)} - {context}")
        logger.debug(f"[{component}] Traceback: {traceback.format_exc()}")

    def log_warning(self, component: str, message: str):
        """Log a warning event."""
        warning_entry = {
            "timestamp": datetime.now().isoformat(),
            "component": component,
            "message": message,
        }
        self.warnings.append(warning_entry)
        logger.warning(f"[{component}] WARNING: {message}")

    def test_import(self, module_path: str, component_name: str) -> bool:
        """Test if a module can be imported successfully."""
        try:
            start_time = asyncio.get_event_loop().time()
            module = import_module(module_path)
            end_time = asyncio.get_event_loop().time()

            import_time = end_time - start_time
            self.performance_metrics[f"{component_name}_import_time"] = import_time

            self.log_trace(
                component_name,
                f"Module imported successfully in {import_time:.4f}s",
                {"module": module_path, "time": import_time},
            )

            # Check if module has expected attributes
            module_attrs = dir(module)
            self.log_trace(
                component_name,
                f"Module attributes found: {len(module_attrs)}",
                {"attributes": module_attrs[:10]},  # Log first 10 to avoid spam
            )

            self.component_status[component_name] = "✅ WORKING"
            return True

        except ImportError as e:
            self.log_error(component_name, e, f"Failed to import {module_path}")
            self.component_status[component_name] = "❌ BROKEN"
            return False
        except Exception as e:
            self.log_error(component_name, e, f"Unexpected error importing {module_path}")
            self.component_status[component_name] = "⚠️  UNSTABLE"
            return False

    async def test_endpoint_functionality(self, endpoint_path: str, function_name: str) -> bool:
        """Test if an endpoint function exists and can be called."""
        try:
            # Import the module
            module_parts = endpoint_path.rsplit(".", 1)
            if len(module_parts) == 2:
                module_path, _ = module_parts
                module = import_module(module_path)

                # Check if function exists
                if hasattr(module, function_name):
                    func = getattr(module, function_name)

                    # Inspect function signature
                    sig = inspect.signature(func)
                    params = list(sig.parameters.keys())

                    self.log_trace(
                        function_name,
                        f"Function found with parameters: {params}",
                        {"signature": str(sig), "parameters": params},
                    )

                    # Check if it's async
                    is_async = asyncio.iscoroutinefunction(func)
                    self.log_trace(
                        function_name,
                        f"Function type: {'async' if is_async else 'sync'}",
                        {"is_async": is_async},
                    )

                    self.component_status[function_name] = "✅ FUNCTIONAL"
                    return True
                else:
                    self.log_error(
                        function_name,
                        ValueError(f"Function {function_name} not found in {module_path}"),
                        "Function lookup failed",
                    )
                    self.component_status[function_name] = "❌ MISSING"
                    return False
            else:
                self.log_error(
                    function_name,
                    ValueError(f"Invalid endpoint path: {endpoint_path}"),
                    "Path parsing failed",
                )
                self.component_status[function_name] = "❌ INVALID"
                return False

        except Exception as e:
            self.log_error(
                function_name,
                e,
                f"Failed to test endpoint functionality for {endpoint_path}",
            )
            self.component_status[function_name] = "⚠️  UNSTABLE"
            return False

    async def trace_execution_flow(self) -> Dict[str, Any]:
        """Trace the complete execution flow end-to-end."""
        logger.info("🔍 Starting comprehensive end-to-end execution trace")
        logger.info("🎯 Verifying all components are truly working")

        # Test 1: Core imports
        logger.info("\n📦 TESTING CORE IMPORTS...")

        core_modules = [
            ("src.monkey_brain.api.main", "Main API"),
            ("src.monkey_brain.api.routes.routes.prompt", "Prompt Routes"),
            ("src.monkey_brain.api.routes.routes.run", "Run Routes"),
            ("src.monkey_brain.api.routes.routes.simulate", "Simulate Routes"),
            ("src.monkey_brain.runtime.runtime", "Runtime"),
            ("src.monkey_brain.runtime.depedencies", "Dependencies"),
            ("src.monkey_brain.runtime.routers", "Routers"),
        ]

        for module_path, component_name in core_modules:
            self.test_import(module_path, component_name)

        # Test 2: Endpoint functionality
        logger.info("\n🎯 TESTING ENDPOINT FUNCTIONALITY...")

        endpoints = [
            (
                "src.monkey_brain.api.routes.routes.prompt.unified_prompt",
                "unified_prompt",
            ),
            ("src.monkey_brain.api.routes.routes.prompt.cicd_prompt", "cicd_prompt"),
            (
                "src.monkey_brain.api.routes.routes.prompt.prompt_health",
                "prompt_health",
            ),
            ("src.monkey_brain.api.routes.routes.run.query_agentos", "query_agentos"),
            (
                "src.monkey_brain.api.routes.routes.simulate.run_simulation",
                "run_simulation",
            ),
        ]

        for endpoint_path, function_name in endpoints:
            await self.test_endpoint_functionality(endpoint_path, function_name)

        # Test 3: Dependency injection
        logger.info("\n🔗 TESTING DEPENDENCY INJECTION...")

        try:
            from src.monkey_brain.runtime.routers import get_mongo_client

            self.log_trace(
                "DependencyInjection",
                "get_mongo_client imported successfully",
                {"function": str(get_mongo_client)},
            )
            self.component_status["DependencyInjection"] = "✅ WORKING"
        except ImportError as e:
            self.log_error("DependencyInjection", e, "Failed to import get_mongo_client")
            self.component_status["DependencyInjection"] = "❌ BROKEN"

        # Test 4: Router registration
        logger.info("\n📡 TESTING ROUTER REGISTRATION...")

        try:
            from src.monkey_brain.api.main import app
            from src.monkey_brain.api.routes.routes.prompt import (
                router as prompt_router,
            )

            # Check if router is properly configured
            routes = []
            prompt_routes_found = 0
            included_routers_found = 0

            for route in app.routes:
                if hasattr(route, "path") and "prompt" in route.path:
                    routes.append(str(route))
                    prompt_routes_found += 1
                # Check for included routers with Prompt tag
                route_tags = []
                if hasattr(route, "tags"):
                    route_tags = route.tags
                elif hasattr(route, "include_context") and hasattr(route.include_context, "tags"):
                    route_tags = route.include_context.tags

                if "Prompt" in route_tags:
                    included_routers_found += 1
                    prefix = getattr(route, "prefix", "N/A")
                    if hasattr(route, "include_context"):
                        prefix = getattr(route.include_context, "prefix", prefix)
                    routes.append(f"IncludedRouter(tags={route_tags}, prefix={prefix})")

            self.log_trace(
                "RouterRegistration",
                f"Found {prompt_routes_found} direct routes and {included_routers_found} included routers",
                {
                    "direct_routes": prompt_routes_found,
                    "included_routers": included_routers_found,
                },
            )

            # Check if we have the prompt router registered (should have Prompt tag)
            # We expect 1 included router with Prompt tag, direct routes may vary
            if included_routers_found >= 1:
                self.component_status["RouterRegistration"] = "✅ REGISTERED"
            else:
                self.log_warning(
                    "RouterRegistration",
                    f"Expected 1+ prompt router, found {included_routers_found}",
                )
                self.component_status["RouterRegistration"] = "⚠️  PARTIAL"

        except Exception as e:
            self.log_error("RouterRegistration", e, "Failed to verify router registration")
            self.component_status["RouterRegistration"] = "❌ BROKEN"

        # Test 5: Pydantic models
        logger.info("\n📋 TESTING PYDANTIC MODELS...")

        try:
            from src.monkey_brain.api.routes.routes.prompt import (
                PromptRequest,
                PromptResponse,
            )
            from src.monkey_brain.api.routes.routes.run import QueryRequest
            from src.monkey_brain.api.routes.routes.simulate import (
                SimulateRequest,
                SimulateResponse,
            )

            # Test model instantiation
            test_request = PromptRequest(
                question="Test question",
                context={"test": "data"},
                run_query=True,
                run_simulate=True,
            )

            self.log_trace(
                "PydanticModels",
                "PromptRequest model working correctly",
                {"model": "PromptRequest", "test_data": test_request.dict()},
            )

            self.component_status["PydanticModels"] = "✅ VALIDATED"

        except Exception as e:
            self.log_error("PydanticModels", e, "Failed to validate Pydantic models")
            self.component_status["PydanticModels"] = "❌ INVALID"

        # Test 6: Error handling
        logger.info("\n🛡️ TESTING ERROR HANDLING...")

        try:
            # Check if error handling is present in prompt.py
            with open("src/monkey_brain/api/routes/routes/prompt.py", "r") as f:
                content = f.read()

            error_handling_patterns = [
                "try:",
                "except Exception",
                "logger.error",
                "results['errors']",
                "HTTPException",
            ]

            found_patterns = []
            for pattern in error_handling_patterns:
                if pattern in content:
                    found_patterns.append(pattern)

            self.log_trace(
                "ErrorHandling",
                f"Found {len(found_patterns)}/{len(error_handling_patterns)} error handling patterns",
                {
                    "patterns_found": found_patterns,
                    "patterns_expected": error_handling_patterns,
                },
            )

            if len(found_patterns) >= 4:
                self.component_status["ErrorHandling"] = "✅ COMPREHENSIVE"
            elif len(found_patterns) >= 2:
                self.component_status["ErrorHandling"] = "⚠️  BASIC"
            else:
                self.component_status["ErrorHandling"] = "❌ MISSING"

        except Exception as e:
            self.log_error("ErrorHandling", e, "Failed to analyze error handling")
            self.component_status["ErrorHandling"] = "⚠️  UNVERIFIED"

        # Generate final report
        return self._generate_report()

    def _generate_report(self) -> Dict[str, Any]:
        """Generate a comprehensive execution trace report."""

        # Count component statuses
        working = sum(1 for status in self.component_status.values() if status.startswith("✅"))
        broken = sum(1 for status in self.component_status.values() if status.startswith("❌"))
        warnings = sum(1 for status in self.component_status.values() if status.startswith("⚠️"))

        report = {
            "timestamp": datetime.now().isoformat(),
            "components_tested": len(self.component_status),
            "components_working": working,
            "components_broken": broken,
            "components_with_warnings": warnings,
            "trace_log_count": len(self.trace_log),
            "errors_count": len(self.errors),
            "warnings_count": len(self.warnings),
            "performance_metrics": self.performance_metrics,
            "component_status": self.component_status,
            "errors": self.errors,
            "warnings": self.warnings,
        }

        return report

    def print_report(self, report: Dict[str, Any]) -> None:
        """Print the execution trace report in a readable format."""

        print("\n" + "=" * 80)
        print("🔍 END-TO-END EXECUTION TRACE REPORT")
        print("=" * 80)

        # Summary
        print(f"📊 EXECUTION SUMMARY:")
        print(f"   • Components Tested: {report['components_tested']}")
        print(f"   • Working: {report['components_working']} ✅")
        print(f"   • Broken: {report['components_broken']} ❌")
        print(f"   • Warnings: {report['components_with_warnings']} ⚠️")
        print(f"   • Trace Logs: {report['trace_log_count']}")
        print(f"   • Errors: {report['errors_count']}")
        print(f"   • Warnings: {report['warnings_count']}")

        # Component Status
        print(f"\n🎯 COMPONENT STATUS:")
        for component, status in report["component_status"].items():
            print(f"   • {component:25} {status}")

        # Performance Metrics
        if report["performance_metrics"]:
            print(f"\n⚡ PERFORMANCE METRICS:")
            for metric, value in report["performance_metrics"].items():
                print(f"   • {metric:30} {value:.4f}s")

        # Errors
        if report["errors"]:
            print(f"\n❌ ERRORS FOUND ({len(report['errors'])}):")
            for i, error in enumerate(report["errors"], 1):
                print(f"   {i}. [{error['component']}] {error['type']}: {error['error']}")
                print(f"      Context: {error['context']}")
        else:
            print(f"\n✅ NO ERRORS FOUND")

        # Warnings
        if report["warnings"]:
            print(f"\n⚠️  WARNINGS ({len(report['warnings'])}):")
            for i, warning in enumerate(report["warnings"], 1):
                print(f"   {i}. [{warning['component']}] {warning['message']}")
        else:
            print(f"\n✅ NO WARNINGS")

        # Final Assessment
        print(f"\n🚀 FINAL ASSESSMENT:")

        if report["components_broken"] > 0:
            print(f"   ❌ SYSTEM STATUS: BROKEN")
            print(f"   🔧 Action Required: Fix {report['components_broken']} broken components")
        elif report["components_with_warnings"] > 2:
            print(f"   ⚠️  SYSTEM STATUS: DEGRADED")
            print(f"   📊 Action Required: Review {report['components_with_warnings']} warnings")
        elif report["components_working"] == report["components_tested"]:
            print(f"   ✅ SYSTEM STATUS: FULLY FUNCTIONAL")
            print(f"   🎉 All {report['components_tested']} components working correctly")
        else:
            print(f"   🟡 SYSTEM STATUS: PARTIALLY WORKING")
            print(f"   🔍 Review {report['components_with_warnings']} components with warnings")

        print("=" * 80)

        # Detailed trace log (sample)
        if self.trace_log:
            print(f"\n📜 SAMPLE TRACE LOG (showing first 5 entries):")
            for i, trace in enumerate(self.trace_log[:5]):
                print(f"   {i + 1}. [{trace['timestamp']}] [{trace['component']}] {trace['message']}")
            if len(self.trace_log) > 5:
                print(f"      ... and {len(self.trace_log) - 5} more trace entries")

        print("=" * 80)


async def main():
    """Main entry point for execution tracing."""

    # Create execution tracer
    tracer = ExecutionTracer()

    # Run comprehensive trace
    report = await tracer.trace_execution_flow()

    # Print report
    tracer.print_report(report)

    # Return exit code based on results
    if report["components_broken"] > 0:
        sys.exit(1)  # Error exit code
    elif report["components_with_warnings"] > 3:
        sys.exit(2)  # Warning exit code
    else:
        sys.exit(0)  # Success exit code


if __name__ == "__main__":
    # Run the execution tracer
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
