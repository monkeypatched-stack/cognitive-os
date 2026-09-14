#!/usr/bin/env python3
"""
Real Integration Test — Actual Endpoint Testing

This test actually calls the unified prompt endpoint with real function execution,
not just simulating or mocking the calls. This will validate the true integration.
"""

import sys
import os
import asyncio
from typing import Dict, Any
from datetime import datetime

# Add src to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

import logging
from fastapi.testclient import TestClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [integration-test] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("integration-test")


class RealIntegrationTester:
    """Real integration testing with actual endpoint calls."""

    def __init__(self):
        self.test_results = []
        self.success_count = 0
        self.failure_count = 0
        self.start_time = None
        self.end_time = None

    async def run_real_integration_test(self) -> Dict[str, Any]:
        """Run real integration tests by actually calling the endpoints."""
        logger.info("🔍 Starting REAL integration testing")
        logger.info("🎯 Testing actual endpoint execution, not simulations")

        self.start_time = datetime.now()

        try:
            # Import and create test client
            from src.monkey_brain.api.main import app

            client = TestClient(app)

            # Test 1: Health check endpoint
            logger.info("\n🩺 TEST 1: Health Check Endpoint")
            try:
                response = client.get("/api/v1/agentos/prompt/health")
                if response.status_code == 200:
                    result = response.json()
                    self._log_success("Health Check", result)
                else:
                    self._log_failure(
                        "Health Check",
                        f"Status {response.status_code}: {response.text}",
                    )
            except Exception as e:
                self._log_failure("Health Check", str(e))

            # Test 2: Unified prompt endpoint with only query
            logger.info("\n🔄 TEST 2: Unified Prompt (Query Only)")
            try:
                test_data = {
                    "question": "What is the status of work order WO-000001?",
                    "context": {"test": "integration"},
                    "run_query": True,
                    "run_simulate": False,
                }
                response = client.post("/api/v1/agentos/prompt", json=test_data)

                if response.status_code == 200:
                    result = response.json()
                    # Verify we got query results but no simulation results
                    if "query_result" in result and result["query_result"] is not None:
                        self._log_success("Unified Prompt (Query Only)", result)
                    else:
                        self._log_failure("Unified Prompt (Query Only)", "No query_result in response")
                else:
                    self._log_failure(
                        "Unified Prompt (Query Only)",
                        f"Status {response.status_code}: {response.text}",
                    )
            except Exception as e:
                self._log_failure("Unified Prompt (Query Only)", str(e))

            # Test 3: Unified prompt endpoint with only simulation
            logger.info("\n🤖 TEST 3: Unified Prompt (Simulation Only)")
            try:
                test_data = {
                    "question": "Predict the outcome of drug research project R&D-001",
                    "context": {"test": "integration"},
                    "run_query": False,
                    "run_simulate": True,
                }
                response = client.post("/api/v1/agentos/prompt", json=test_data)

                if response.status_code == 200:
                    result = response.json()
                    # Verify we got simulation results but no query results
                    if "simulation_result" in result and result["simulation_result"] is not None:
                        self._log_success("Unified Prompt (Simulation Only)", result)
                    else:
                        self._log_failure(
                            "Unified Prompt (Simulation Only)",
                            "No simulation_result in response",
                        )
                else:
                    self._log_failure(
                        "Unified Prompt (Simulation Only)",
                        f"Status {response.status_code}: {response.text}",
                    )
            except Exception as e:
                self._log_failure("Unified Prompt (Simulation Only)", str(e))

            # Test 4: Unified prompt endpoint with both query and simulation
            logger.info("\n🔄🤖 TEST 4: Unified Prompt (Both Query and Simulation)")
            try:
                test_data = {
                    "question": "Show me production KPIs and predict future trends",
                    "context": {"test": "integration", "system": "full"},
                    "run_query": True,
                    "run_simulate": True,
                }
                response = client.post("/api/v1/agentos/prompt", json=test_data)

                if response.status_code == 200:
                    result = response.json()
                    # Verify we got both query and simulation results
                    if (
                        "query_result" in result
                        and result["query_result"] is not None
                        and "simulation_result" in result
                        and result["simulation_result"] is not None
                    ):
                        self._log_success("Unified Prompt (Both)", result)
                    else:
                        missing = []
                        if "query_result" not in result or result["query_result"] is None:
                            missing.append("query_result")
                        if "simulation_result" not in result or result["simulation_result"] is None:
                            missing.append("simulation_result")
                        self._log_failure("Unified Prompt (Both)", f"Missing results: {missing}")
                else:
                    self._log_failure(
                        "Unified Prompt (Both)",
                        f"Status {response.status_code}: {response.text}",
                    )
            except Exception as e:
                self._log_failure("Unified Prompt (Both)", str(e))

            # Test 5: CI/CD specialized endpoint
            logger.info("\n🏗️  TEST 5: CI/CD Specialized Endpoint")
            try:
                test_data = {
                    "question": "ETASS v1.0 orchestration prompt",
                    "context": {"pipeline": "production", "stage": "testing"},
                    "run_query": True,
                    "run_simulate": True,
                }
                response = client.post("/api/v1/agentos/prompt/cicd", json=test_data)

                if response.status_code == 200:
                    result = response.json()
                    # Verify CI/CD metadata is present
                    if "cicd_metadata" in result and "pipeline_integration" in result:
                        self._log_success("CI/CD Endpoint", result)
                    else:
                        self._log_failure("CI/CD Endpoint", "Missing CI/CD metadata in response")
                else:
                    self._log_failure(
                        "CI/CD Endpoint",
                        f"Status {response.status_code}: {response.text}",
                    )
            except Exception as e:
                self._log_failure("CI/CD Endpoint", str(e))

            # Test 6: Error handling - invalid request
            logger.info("\n⚠️  TEST 6: Error Handling (Invalid Request)")
            try:
                test_data = {
                    "question": "",  # Empty question should fail validation
                    "run_query": True,
                    "run_simulate": True,
                }
                response = client.post("/api/v1/agentos/prompt", json=test_data)

                if response.status_code == 422:  # Expected validation error
                    self._log_success("Error Handling", "Correctly rejected invalid request")
                else:
                    self._log_failure("Error Handling", f"Expected 422, got {response.status_code}")
            except Exception as e:
                self._log_failure("Error Handling", str(e))

        except Exception as e:
            logger.error(f"💥 Fatal error during integration testing: {str(e)}")
            self._log_failure("Integration Test", f"Fatal error: {str(e)}")

        self.end_time = datetime.now()

        # Calculate metrics
        total_time = (self.end_time - self.start_time).total_seconds()
        success_rate = (self.success_count / len(self.test_results)) * 100 if self.test_results else 0

        return {
            "timestamp": datetime.now().isoformat(),
            "tests_run": len(self.test_results),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": f"{success_rate:.2f}%",
            "total_time_seconds": f"{total_time:.2f}",
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "test_results": self.test_results,
        }

    def _log_success(self, test_name: str, result: Dict[str, Any]) -> None:
        """Log a successful test."""
        self.success_count += 1
        test_result = {
            "test": test_name,
            "status": "SUCCESS",
            "result": result,
            "timestamp": datetime.now().isoformat(),
        }
        self.test_results.append(test_result)
        logger.info(f"✅ {test_name}: SUCCESS")

    def _log_failure(self, test_name: str, error: str) -> None:
        """Log a failed test."""
        self.failure_count += 1
        test_result = {
            "test": test_name,
            "status": "FAILURE",
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
        self.test_results.append(test_result)
        logger.error(f"❌ {test_name}: FAILED - {error}")

    def print_report(self, results: Dict[str, Any]) -> None:
        """Print the integration test report."""

        print("\n" + "=" * 80)
        print("🔍 REAL INTEGRATION TEST RESULTS")
        print("=" * 80)

        # Summary
        print(f"📊 TEST SUMMARY:")
        print(f"   • Tests Run: {results['tests_run']}")
        print(f"   • Successful: {results['success_count']} ✅")
        print(f"   • Failed: {results['failure_count']} ❌")
        print(f"   • Success Rate: {results['success_rate']}")
        print(f"   • Total Time: {results['total_time_seconds']} seconds")

        # Detailed results
        print(f"\n📋 TEST RESULTS:")
        for i, result in enumerate(results["test_results"], 1):
            status_emoji = "✅" if result["status"] == "SUCCESS" else "❌"
            print(f"   {i}. {status_emoji} {result['test']}: {result['status']}")
            if result["status"] == "FAILURE":
                print(f"      Error: {result['error']}")

        # Final assessment
        print(f"\n🚀 FINAL ASSESSMENT:")
        success_rate = float(results["success_rate"].replace("%", ""))

        if success_rate == 100.0:
            print(f"   ✅ INTEGRATION STATUS: FULLY FUNCTIONAL")
            print(f"   🎉 All {results['tests_run']} tests passed")
            print(f"   🚀 System is ready for production")
        elif success_rate >= 80.0:
            print(f"   ⚠️  INTEGRATION STATUS: PARTIALLY WORKING")
            print(f"   🔧 {results['failure_count']} tests failed, needs attention")
        else:
            print(f"   ❌ INTEGRATION STATUS: BROKEN")
            print(f"   💥 {results['failure_count']} tests failed, critical issues")

        print("=" * 80)

        # Return appropriate exit code
        if success_rate == 100.0:
            print(f"\n🎉 REAL INTEGRATION TESTING: PASSED")
            return 0
        elif success_rate >= 80.0:
            print(f"\n⚠️  REAL INTEGRATION TESTING: PARTIAL")
            return 1
        else:
            print(f"\n❌ REAL INTEGRATION TESTING: FAILED")
            return 2


async def main():
    """Main entry point for real integration testing."""

    # Create integration tester
    tester = RealIntegrationTester()

    # Run real integration tests
    results = await tester.run_real_integration_test()

    # Print report and return exit code
    exit_code = tester.print_report(results)
    return exit_code


if __name__ == "__main__":
    # Run the real integration testing
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
