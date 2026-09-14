#!/usr/bin/env python3
"""
Interactive Example 3: World Model Builder

Demonstrates creating and managing custom world models through the SDK.
Users can build, search, and explore world models interactively.
"""

import asyncio
import json
from monkeypatched_sdk import CapabilityAdapter, capability_adapter, AdapterResponse


# World Model Registry
class WorldModelRegistry:
    """Registry for custom world models"""

    def __init__(self):
        self.models = {}
        self.model_count = 0

    def register_model(self, model_id, model_data):
        """Register a new world model"""
        self.models[model_id] = model_data
        self.model_count += 1
        return f"Model {model_id} registered successfully"

    def get_model(self, model_id):
        """Retrieve a registered world model"""
        return self.models.get(model_id)

    def search_models(self, query):
        """Search models by name or description"""
        query_lower = query.lower()
        return [
            mid
            for mid, mdata in self.models.items()
            if query_lower in mid.lower() or query_lower in mdata.get("description", "").lower()
        ]

    def list_models(self):
        """List all registered models"""
        return list(self.models.keys())

    def get_stats(self):
        """Get registry statistics"""
        return {"total_models": self.model_count, "model_ids": list(self.models.keys())}


@capability_adapter(
    capability_id="world_model_builder",
    name="World Model Builder",
    description="Create and manage custom world models for simulation",
    version="1.0.0",
)
class WorldModelBuilderAdapter(CapabilityAdapter):
    """Adapter for building and managing world models"""

    def __init__(self, adapter_id, capability_id):
        super().__init__(adapter_id, capability_id)
        self.registry = WorldModelRegistry()

    async def initialize(self):
        """Initialize the adapter"""
        print("🌍 World Model Builder initialized!")
        print("   Registry: Ready")
        print("   Status: Accepting model definitions")
        return True

    async def execute(self, context, inputs):
        """Execute world model operations"""
        action = inputs.get("action", "list")
        print(f"📥 Processing action: {action}")

        try:
            if action == "create":
                return await self._create_model(inputs)
            elif action == "get":
                return await self._get_model(inputs)
            elif action == "search":
                return await self._search_models(inputs)
            elif action == "list":
                return await self._list_models()
            elif action == "simulate":
                return await self._run_simulation(inputs)
            else:
                return AdapterResponse.fail(f"Unknown action: {action}", "INVALID_ACTION")
        except Exception as e:
            return AdapterResponse.fail(f"Error: {str(e)}", "PROCESSING_ERROR")

    async def _create_model(self, inputs):
        """Create a new world model"""
        model_id = inputs.get("model_id")
        model_data = inputs.get("model_data", {})

        if not model_id:
            return AdapterResponse.fail("model_id is required", "VALIDATION_ERROR")

        # Validate model data
        required_fields = ["name", "description", "entities", "rules"]
        for field in required_fields:
            if field not in model_data:
                return AdapterResponse.fail(f"Missing required field: {field}", "VALIDATION_ERROR")

        # Register the model
        result = self.registry.register_model(model_id, model_data)

        print(f"✅ Created world model: {model_id}")

        return AdapterResponse.ok(
            {
                "status": "success",
                "message": result,
                "model_id": model_id,
                "entities": list(model_data["entities"].keys()),
                "rules_count": len(model_data["rules"]),
                "simulation_capabilities": model_data.get("simulation_capabilities", []),
            }
        )

    async def _get_model(self, inputs):
        """Retrieve a world model"""
        model_id = inputs.get("model_id")

        if not model_id:
            return AdapterResponse.fail("model_id is required", "VALIDATION_ERROR")

        model = self.registry.get_model(model_id)

        if not model:
            return AdapterResponse.fail(f"Model {model_id} not found", "NOT_FOUND")

        print(f"📄 Retrieved world model: {model_id}")

        return AdapterResponse.ok({"status": "success", "model": model})

    async def _search_models(self, inputs):
        """Search for world models"""
        query = inputs.get("query", "")
        results = self.registry.search_models(query)

        print(f"🔍 Found {len(results)} models matching '{query}'")

        return AdapterResponse.ok({"status": "success", "results": results, "count": len(results)})

    async def _list_models(self):
        """List all available world models"""
        models = self.registry.list_models()

        print(f"📚 Listing {len(models)} world models")

        return AdapterResponse.ok({"status": "success", "models": models, "count": len(models)})

    async def _run_simulation(self, inputs):
        """Run a simulation on a world model"""
        model_id = inputs.get("model_id")
        scenario = inputs.get("scenario", {})

        if not model_id:
            return AdapterResponse.fail("model_id is required", "VALIDATION_ERROR")

        # Get the model
        model = self.registry.get_model(model_id)

        if not model:
            return AdapterResponse.fail(f"Model {model_id} not found", "NOT_FOUND")

        # Simulate based on model type
        if "pharmaceutical" in model.get("domain", "").lower():
            simulation_result = self._simulate_pharmaceutical(model, scenario)
        elif "quality" in model.get("domain", "").lower():
            simulation_result = self._simulate_quality_control(model, scenario)
        else:
            simulation_result = self._simulate_generic(model, scenario)

        print(f"🧪 Ran simulation on {model_id}: {scenario.get('type', 'default')}")

        return AdapterResponse.ok(
            {
                "status": "success",
                "model_id": model_id,
                "scenario": scenario,
                "results": simulation_result,
            }
        )

    def _simulate_pharmaceutical(self, model, scenario):
        """Simulate pharmaceutical manufacturing scenario"""
        scenario_type = scenario.get("type", "process_variation")

        if scenario_type == "process_variation":
            # Simulate process variation impact
            variation = scenario.get("variation", "blend_speed")
            impact = {
                "blend_speed": {
                    "impact": "Content uniformity may decrease by 5-10%",
                    "recommendation": "Monitor RSD and adjust speed gradually",
                    "confidence": 0.85,
                },
                "compression_force": {
                    "impact": "Tablet hardness increases by 15%, friability decreases",
                    "recommendation": "Maintain force within 10-15 kN range",
                    "confidence": 0.90,
                },
                "temperature": {
                    "impact": "No significant impact if within 18-25°C range",
                    "recommendation": "Monitor environmental conditions",
                    "confidence": 0.75,
                },
            }.get(
                variation,
                {
                    "impact": "Unknown variation",
                    "recommendation": "Consult process engineer",
                    "confidence": 0.50,
                },
            )

            return {
                "type": "process_variation_analysis",
                "variation": variation,
                "impact": impact["impact"],
                "recommendation": impact["recommendation"],
                "confidence": impact["confidence"],
                "entities_affected": (
                    ["blending", "compression"] if variation in ["blend_speed", "compression_force"] else []
                ),
            }

        elif scenario_type == "quality_impact":
            # Simulate quality impact
            return {
                "type": "quality_impact_prediction",
                "impact": "Content uniformity RSD may increase to 2.5% (current: 1.8%)",
                "recommendation": "Increase blend time by 10% to maintain RSD < 2.0%",
                "confidence": 0.88,
                "quality_tests_affected": ["Content Uniformity", "Blend Homogeneity"],
            }

        else:
            return self._simulate_generic(model, scenario)

    def _simulate_quality_control(self, model, scenario):
        """Simulate quality control scenario"""
        scenario_type = scenario.get("type", "oos_analysis")

        if scenario_type == "oos_analysis":
            test_type = scenario.get("test_type", "assay")

            oos_analysis = {
                "assay": {
                    "root_cause": "Potential calibration drift in HPLC",
                    "recommendation": "Recalibrate HPLC and rerun sample",
                    "confidence": 0.92,
                    "corrective_action": "Instrument calibration and verification",
                },
                "dissolution": {
                    "root_cause": "Tablet hardness variation affecting dissolution rate",
                    "recommendation": "Check compression force and tablet hardness",
                    "confidence": 0.87,
                    "corrective_action": "Adjust compression parameters",
                },
                "microbiological": {
                    "root_cause": "Environmental contamination during sampling",
                    "recommendation": "Review sampling procedure and environment",
                    "confidence": 0.85,
                    "corrective_action": "Environmental monitoring and procedure review",
                },
            }.get(
                test_type,
                {
                    "root_cause": "Unknown cause",
                    "recommendation": "Investigate test procedure and equipment",
                    "confidence": 0.60,
                    "corrective_action": "Full investigation required",
                },
            )

            return {
                "type": "oos_root_cause_analysis",
                "test_type": test_type,
                "root_cause": oos_analysis["root_cause"],
                "recommendation": oos_analysis["recommendation"],
                "confidence": oos_analysis["confidence"],
                "corrective_action": oos_analysis["corrective_action"],
            }

        elif scenario_type == "specification_compliance":
            return {
                "type": "specification_compliance_prediction",
                "compliance": "98.5% probability of passing all specifications",
                "risk_areas": ["Dissolution Q value at lower specification limit"],
                "recommendation": "Monitor dissolution profiles closely",
                "confidence": 0.91,
            }

        else:
            return self._simulate_generic(model, scenario)

    def _simulate_generic(self, model, scenario):
        """Generic simulation for any model type"""
        return {
            "type": scenario.get("type", "default_simulation"),
            "message": "Simulation completed successfully",
            "model_id": model["name"],
            "scenario": scenario,
            "confidence": 0.75,
            "recommendations": ["Review simulation parameters for model-specific results"],
        }

    async def health_check(self):
        """Check adapter health"""
        stats = self.registry.get_stats()

        return {
            "status": "healthy",
            "models_registered": stats["total_models"],
            "adapter": self.adapter_id,
            "capabilities": ["create", "get", "search", "list", "simulate"],
        }

    async def shutdown(self):
        """Cleanup resources"""
        print("🌍 World Model Builder shutting down...")
        print(f"   Models registered: {self.registry.model_count}")
        return True


async def show_main_menu():
    """Display main menu"""
    print("\n" + "=" * 60)
    print("🌍 World Model Builder - Main Menu")
    print("=" * 60)
    print("1. Create new world model")
    print("2. View world model details")
    print("3. Search world models")
    print("4. List all world models")
    print("5. Run simulation")
    print("6. Exit")
    print("-" * 60)


async def get_menu_choice():
    """Get user menu choice"""
    while True:
        try:
            choice = input("Enter your choice (1-6): ").strip()
            if choice in ["1", "2", "3", "4", "5", "6"]:
                return choice
            print("❌ Invalid choice. Please enter 1-6.")
        except Exception:
            print("❌ Invalid input. Please try again.")


def get_model_type():
    """Get model type from user"""
    print("\n📚 Select model type:")
    print("1. Pharmaceutical Manufacturing")
    print("2. Quality Control")
    print("3. Custom Model")

    while True:
        choice = input("Enter choice (1-3): ").strip()
        if choice in ["1", "2", "3"]:
            return choice
        print("❌ Please enter 1, 2, or 3.")


def build_pharmaceutical_model():
    """Interactively build a pharmaceutical manufacturing model"""
    print("\n💊 Building Pharmaceutical Manufacturing Model")

    name = input("Model name: ").strip() or "Tablet Manufacturing Workflow"
    description = input("Description: ").strip() or "Complete OSD tablet manufacturing process"
    version = input("Version: ").strip() or "1.0.0"

    print("\n📋 Define entities (press enter to skip):")
    entities = {}

    # Plants
    plants = input("Plants (comma-separated): ").strip()
    if plants:
        entities["plants"] = [p.strip() for p in plants.split(",")]

    # Lines
    lines = input("Production lines (comma-separated): ").strip()
    if lines:
        entities["lines"] = [l.strip() for l in lines.split(",")]

    # Stages
    stages = input("Stages (comma-separated): ").strip() or "Dispensing,Blending,Granulation,Compression,Coating"
    entities["stages"] = [s.strip() for s in stages.split(",")]

    # Workstations
    workstations = input("Workstations (comma-separated): ").strip()
    if workstations:
        entities["workstations"] = [w.strip() for w in workstations.split(",")]

    # Machines
    machines = input("Machines (comma-separated): ").strip()
    if machines:
        entities["machines"] = [m.strip() for m in machines.split(",")]

    # Quality tests
    quality_tests = input("Quality tests (comma-separated): ").strip()
    if quality_tests:
        entities["quality_tests"] = [q.strip() for q in quality_tests.split(",")]

    print("\n📜 Define rules (one per line, empty line to finish):")
    rules = []
    while True:
        rule = input(f"Rule {len(rules) + 1}: ").strip()
        if not rule:
            break
        rules.append(rule)

    if not rules:
        rules = [
            "Blending must achieve RSD < 2.0% before proceeding",
            "Compression force must be 10-15 kN",
            "Tablet hardness must be 8-12 kP",
        ]

    print("\n🔧 Define constraints:")
    constraints = {}

    temp = input("Temperature range (e.g., 18-25°C): ").strip()
    if temp:
        constraints["temperature"] = temp

    humidity = input("Humidity range (e.g., 30-50% RH): ").strip()
    if humidity:
        constraints["humidity"] = humidity

    pressure = input("Pressure (e.g., Atmospheric): ").strip()
    if pressure:
        constraints["pressure"] = pressure

    print("\n🧪 Define simulation capabilities (comma-separated):")
    sim_caps = input("Capabilities: ").strip() or "process_variation_analysis,quality_impact_prediction"
    simulation_capabilities = [s.strip() for s in sim_caps.split(",")]

    return {
        "name": name,
        "description": description,
        "version": version,
        "domain": "pharmaceutical_manufacturing",
        "entities": entities,
        "rules": rules,
        "constraints": constraints,
        "simulation_capabilities": simulation_capabilities,
    }


def build_quality_control_model():
    """Interactively build a quality control model"""
    print("\n🔬 Building Quality Control Model")

    name = input("Model name: ").strip() or "Pharmaceutical Quality Control"
    description = input("Description: ").strip() or "Quality control processes and acceptance criteria"
    version = input("Version: ").strip() or "1.0.0"

    print("\n📋 Define entities:")
    entities = {}

    # Test types
    test_types = (
        input("Test types (comma-separated): ").strip() or "Assay,Related Substances,Dissolution,Microbiological"
    )
    entities["test_types"] = [t.strip() for t in test_types.split(",")]

    # Instruments
    instruments = input("Instruments (comma-separated): ").strip() or "HPLC,UV-Spectrophotometer,Dissolution Apparatus"
    entities["instruments"] = [i.strip() for i in instruments.split(",")]

    # Specifications
    specs = input("Specifications (comma-separated): ").strip() or "USP,EP,JP,ICH"
    entities["specifications"] = [s.strip() for s in specs.split(",")]

    # Test parameters
    params = input("Test parameters (comma-separated): ").strip() or "Potency,Purity,Uniformity,Sterility"
    entities["test_parameters"] = [p.strip() for p in params.split(",")]

    print("\n📜 Define rules:")
    rules = []
    while True:
        rule = input(f"Rule {len(rules) + 1}: ").strip()
        if not rule:
            break
        rules.append(rule)

    if not rules:
        rules = [
            "Assay must be 95-105% of label claim",
            "Related substances < 0.5% each, < 1.0% total",
            "Dissolution Q=80% in 30 minutes",
        ]

    print("\n🔧 Define constraints:")
    constraints = {}

    storage = input("Sample storage (e.g., 2-8°C): ").strip()
    if storage:
        constraints["storage"] = storage

    testing_time = input("Testing time window (e.g., Within 24 hours): ").strip()
    if testing_time:
        constraints["testing_time"] = testing_time

    calibration = input("Calibration frequency (e.g., Daily): ").strip()
    if calibration:
        constraints["calibration"] = calibration

    print("\n🧪 Define simulation capabilities:")
    sim_caps = input("Capabilities: ").strip() or "OOS_root_cause_analysis,specification_compliance_prediction"
    simulation_capabilities = [s.strip() for s in sim_caps.split(",")]

    return {
        "name": name,
        "description": description,
        "version": version,
        "domain": "quality_assurance",
        "entities": entities,
        "rules": rules,
        "constraints": constraints,
        "simulation_capabilities": simulation_capabilities,
    }


def build_custom_model():
    """Interactively build a custom model"""
    print("\n🎨 Building Custom World Model")

    name = input("Model name: ").strip() or "Custom Workflow"
    description = input("Description: ").strip() or "Custom world model for specific use case"
    version = input("Version: ").strip() or "1.0.0"
    domain = input("Domain (e.g., manufacturing, quality, logistics): ").strip() or "general"

    print("\n📋 Define entities (JSON format):")
    print("Example: {'items': ['item1', 'item2'], 'locations': ['loc1', 'loc2']}")

    try:
        entities_input = input("Entities: ").strip() or "{}"
        entities = json.loads(entities_input) if entities_input else {}
    except json.JSONDecodeError:
        print("❌ Invalid JSON. Using empty entities.")
        entities = {}

    print("\n📜 Define rules (one per line, empty to finish):")
    rules = []
    while True:
        rule = input(f"Rule {len(rules) + 1}: ").strip()
        if not rule:
            break
        rules.append(rule)

    print("\n🔧 Define constraints (JSON format):")
    print("Example: {'temperature': '15-30°C', 'humidity': '20-60% RH'}")

    try:
        constraints_input = input("Constraints: ").strip() or "{}"
        constraints = json.loads(constraints_input) if constraints_input else {}
    except json.JSONDecodeError:
        print("❌ Invalid JSON. Using empty constraints.")
        constraints = {}

    print("\n🧪 Define simulation capabilities (comma-separated):")
    sim_caps = input("Capabilities: ").strip()
    simulation_capabilities = [s.strip() for s in sim_caps.split(",")] if sim_caps else []

    return {
        "name": name,
        "description": description,
        "version": version,
        "domain": domain,
        "entities": entities,
        "rules": rules,
        "constraints": constraints,
        "simulation_capabilities": simulation_capabilities,
    }


async def main():
    """Interactive world model builder demonstration"""
    print("=" * 60)
    print("🚀 Interactive Example 3: World Model Builder")
    print("=" * 60)

    # Create adapter instance
    adapter = WorldModelBuilderAdapter("world_model_builder_001", "world_model_builder")

    # Initialize
    print("\n🔄 Initializing adapter...")
    await adapter.initialize()

    # Main loop
    while True:
        await show_main_menu()
        choice = await get_menu_choice()

        if choice == "1":  # Create world model
            print("\n🌍 Creating New World Model")

            # Select model type
            model_type = get_model_type()

            # Build model based on type
            if model_type == "1":
                model_data = build_pharmaceutical_model()
                model_id = "pharmaceutical_" + model_data["name"].replace(" ", "_").lower()
            elif model_type == "2":
                model_data = build_quality_control_model()
                model_id = "quality_" + model_data["name"].replace(" ", "_").lower()
            else:  # model_type == '3'
                model_data = build_custom_model()
                model_id = "custom_" + model_data["name"].replace(" ", "_").lower()

            # Register the model
            response = await adapter.execute(
                context=None,
                inputs={
                    "action": "create",
                    "model_id": model_id,
                    "model_data": model_data,
                },
            )

            if response.status == "ok":
                print(f"\n✅ World model created!")
                print(f"   Model ID: {response.data['model_id']}")
                print(f"   Name: {model_data['name']}")
                print(f"   Domain: {model_data['domain']}")
                print(f"   Entities: {len(response.data['entities'])}")
                print(f"   Rules: {response.data['rules_count']}")
                print(f"   Simulation capabilities: {len(response.data['simulation_capabilities'])}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "2":  # View model details
            model_id = input("\n🔍 Enter model ID: ").strip()
            if not model_id:
                print("❌ Model ID is required.")
                continue

            response = await adapter.execute(context=None, inputs={"action": "get", "model_id": model_id})

            if response.status == "ok":
                model = response.data["model"]
                print(f"\n📄 World Model Details:")
                print(f"   ID: {model_id}")
                print(f"   Name: {model['name']}")
                print(f"   Description: {model['description']}")
                print(f"   Version: {model['version']}")
                print(f"   Domain: {model['domain']}")

                print(f"\n📋 Entities:")
                for entity_type, items in model["entities"].items():
                    print(f"   {entity_type}: {', '.join(items[:3])}{'...' if len(items) > 3 else ''}")

                print(f"\n📜 Rules ({len(model['rules'])}):")
                for i, rule in enumerate(model["rules"][:3], 1):
                    print(f"   {i}. {rule}")
                if len(model["rules"]) > 3:
                    print(f"   ... and {len(model['rules']) - 3} more")

                if model["constraints"]:
                    print(f"\n🔧 Constraints:")
                    for key, value in model["constraints"].items():
                        print(f"   {key}: {value}")

                if model["simulation_capabilities"]:
                    print(f"\n🧪 Simulation Capabilities:")
                    for cap in model["simulation_capabilities"]:
                        print(f"   • {cap.replace('_', ' ').title()}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "3":  # Search models
            query = input("\n🔍 Enter search query: ").strip()

            response = await adapter.execute(context=None, inputs={"action": "search", "query": query})

            if response.status == "ok":
                results = response.data["results"]
                count = response.data["count"]

                print(f"\n📚 Found {count} models matching '{query}':")
                for i, model_id in enumerate(results, 1):
                    print(f"{i}. {model_id}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "4":  # List models
            response = await adapter.execute(context=None, inputs={"action": "list"})

            if response.status == "ok":
                models = response.data["models"]
                count = response.data["count"]

                print(f"\n📚 World Model Registry ({count} models):")
                for i, model_id in enumerate(models, 1):
                    print(f"{i}. {model_id}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "5":  # Run simulation
            # List models first
            list_response = await adapter.execute({"action": "list"})
            if list_response.status != "ok" or not list_response.data["models"]:
                print("\n⚠️ No world models available. Please create a model first.")
                continue

            print("\n🧪 Run Simulation")
            print("Available models:")
            for i, model_id in enumerate(list_response.data["models"], 1):
                print(f"{i}. {model_id}")

            model_choice = input("Select model number: ").strip()
            try:
                model_index = int(model_choice) - 1
                if 0 <= model_index < len(list_response.data["models"]):
                    model_id = list_response.data["models"][model_index]
                else:
                    print("❌ Invalid model number.")
                    continue
            except ValueError:
                print("❌ Please enter a valid number.")
                continue

            # Get simulation type
            print("\nSelect simulation type:")
            sim_types = [
                "process_variation",
                "quality_impact",
                "oos_analysis",
                "compliance",
            ]
            for i, sim_type in enumerate(sim_types, 1):
                print(f"{i}. {sim_type.replace('_', ' ').title()}")

            sim_choice = input("Enter choice (1-4): ").strip()
            try:
                sim_index = int(sim_choice) - 1
                if 0 <= sim_index < len(sim_types):
                    sim_type = sim_types[sim_index]
                else:
                    print("❌ Invalid simulation type.")
                    continue
            except ValueError:
                print("❌ Please enter a valid number.")
                continue

            # Run simulation
            response = await adapter.execute(
                context=None,
                inputs={
                    "action": "simulate",
                    "model_id": model_id,
                    "scenario": {
                        "type": sim_type,
                        "variation": "blend_speed" if "variation" in sim_type else None,
                    },
                },
            )

            if response.status == "ok":
                results = response.data["results"]
                print(f"\n🧪 Simulation Results:")
                print(f"   Model: {response.data['model_id']}")
                print(f"   Type: {results['type'].replace('_', ' ').title()}")
                print(f"   Confidence: {results['confidence']:.2f}")

                if "impact" in results:
                    print(f"\n📊 Impact:")
                    print(f"   {results['impact']}")

                if "recommendation" in results:
                    print(f"\n💡 Recommendation:")
                    print(f"   {results['recommendation']}")

                if "root_cause" in results:
                    print(f"\n🔍 Root Cause:")
                    print(f"   {results['root_cause']}")

                if "entities_affected" in results:
                    print(f"\n📋 Entities Affected:")
                    for entity in results["entities_affected"]:
                        print(f"   • {entity}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "6":  # Exit
            print("\n👋 Exiting World Model Builder...")

            # Health check before shutdown
            health = await adapter.health_check()
            print(f"\n📊 Final Health Check:")
            print(f"   Status: {health['status']}")
            print(f"   Models registered: {health['models_registered']}")
            print(f"   Adapter: {health['adapter']}")

            # Shutdown
            await adapter.shutdown()
            print("\n✅ World Model Builder shutdown complete.")

            print("\n" + "=" * 60)
            print("🎉 Interactive Example 3 completed!")
            print("=" * 60)
            break


if __name__ == "__main__":
    asyncio.run(main())
