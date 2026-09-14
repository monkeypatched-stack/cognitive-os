"""plant_hierarchy predicates — plant/line/stage/workstation/machine/equipment queries."""

import re

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_plant_names(db):
    locs = db["plant_locations"]
    unique = {}
    async for doc in locs.find({"type": "plant", "level": 1}, {"location_id": 1, "name": 1}):
        lid = doc.get("location_id", "")
        if lid and lid not in unique:
            unique[lid] = doc.get("name", lid)
    return unique


async def _get_line_names(db):
    machines = db["pharmaceutical_machines"]
    lines = {}
    async for doc in machines.aggregate([{"$group": {"_id": "$line_id"}}]):
        lid = doc["_id"]
        if lid:
            lines[lid] = lid
    return lines


async def _get_stage_map(db):
    machines = db["pharmaceutical_machines"]
    stages = {}
    async for doc in machines.aggregate([{"$group": {"_id": "$stage_id", "lines": {"$addToSet": "$line_id"}}}]):
        sid = doc["_id"]
        if sid:
            stages[sid] = doc.get("lines", [])
    return stages


async def _find_entity(collection, name_pattern, name_field="name"):
    """Find documents matching a name pattern (case-insensitive partial match)."""
    regex = re.compile(re.escape(name_pattern), re.IGNORECASE)
    cursor = collection.find({name_field: regex})
    return await cursor.to_list(100)


async def _find_worker_by_name(db, name_pattern):
    workers = db["workers"]
    regex = re.compile(re.escape(name_pattern), re.IGNORECASE)
    doc = await workers.find_one({"name": regex})
    return doc


# ---------------------------------------------------------------------------
# Plant-level queries
# ---------------------------------------------------------------------------


async def _answer_plant_query(db, question):
    q = question.lower()
    plants = await _get_plant_names(db)

    # "how many plants"
    if re.search(r"how many plant|plant count|number of plant", q):
        count = len(plants)
        names = ", ".join(plants.values())
        return f"There {'is' if count == 1 else 'are'} {count} plant{'s' if count != 1 else ''}: {names}."

    # "name of the plant" / "what is the plant"
    if re.search(r"name of the plant|what is the plant|which plant", q):
        names = ", ".join(plants.values())
        return f"The plant{'s' if len(plants) > 1 else ''} in the system: {names}."

    # Extract plant name from question
    plant_name = None
    for pname in plants.values():
        if pname.lower() in q:
            plant_name = pname
            break
    if not plant_name:
        # Try partial match
        for pname in plants.values():
            words = pname.lower().split()
            if any(w in q for w in words if len(w) > 3):
                plant_name = pname
                break

    if not plant_name and plants:
        plant_name = list(plants.values())[0]

    if not plant_name:
        return "No plant found in the system."

    # Lines in plant
    if re.search(r"line[s]?\b", q):
        machines = db["pharmaceutical_machines"]
        line_ids = await machines.distinct("line_id")
        if not line_ids:
            return f"No lines found for {plant_name}."
        lines = []
        for lid in sorted(line_ids):
            count = await machines.count_documents({"line_id": lid})
            lines.append(f"  - {lid} ({count} machines)")
        return f"Lines in {plant_name}:\n" + "\n".join(lines)

    # Location
    if re.search(r"\blocation\b|\bwhere\b|\baddress\b|\bcity\b|\bcountry\b", q):
        locs = db["plant_locations"]
        doc = await locs.find_one({"type": "plant", "level": 1, "name": plant_name})
        if doc:
            parts = []
            for k in ["address", "city", "state", "country", "pincode", "location"]:
                if doc.get(k):
                    parts.append(f"{k.title()}: {doc[k]}")
            if parts:
                return f"Location of {plant_name}:\n" + "\n".join(f"  {p}" for p in parts)
        return f"Location details for {plant_name} not available in structured data."

    # Manager
    if re.search(r"manager|who.*manage|plant manager", q):
        workers = db["workers"]
        cursor = workers.find({"role": "manager"}).limit(5)
        docs = await cursor.to_list(5)
        if docs:
            lines = [f"Management at {plant_name}:"]
            for d in docs:
                lines.append(f"  - {d.get('name', '?')}: {d.get('title', 'N/A')} [{d.get('role', '?')}]")
            return "\n".join(lines)
        return f"No manager found for {plant_name}."

    # Emergency contact
    if re.search(r"emergency|contact|safety", q):
        workers = db["workers"]
        doc = await workers.find_one({"role": "safety"})
        if doc:
            return f"Emergency contact for {plant_name}:\n  - {doc.get('name', '?')}: {doc.get('title', 'N/A')} (Role: {doc.get('role', '?')})"
        return f"No emergency contact found for {plant_name}."

    # Utilities
    if re.search(r"utilit|power|water|gas|steam|hvac", q):
        machines = db["pharmaceutical_machines"]
        utility_machines = await _find_entity(machines, "AHU")
        utility_machines += await _find_entity(machines, "Hot Water Generator")
        utility_machines += await _find_entity(machines, "Dust Collector")
        utility_machines += await _find_entity(machines, "Dust Extractor")
        utility_machines += await _find_entity(machines, "Exhaust Blower")
        if utility_machines:
            lines = [f"Utilities at {plant_name}:"]
            seen = set()
            for m in utility_machines:
                name = m.get("name", "?")
                if name not in seen:
                    seen.add(name)
                    lines.append(f"  - {name} ({m.get('status', '?')})")
            return "\n".join(lines)
        return f"No utility information found for {plant_name}."

    # Capacity
    if re.search(r"capacity|throughput|output", q):
        machines = db["pharmaceutical_machines"]
        count = await machines.count_documents({})
        lines_ids = await machines.distinct("line_id")
        return f"Capacity overview for {plant_name}:\n  - {len(lines_ids)} production lines\n  - {count} machines total"

    # Shift schedule
    if re.search(r"shift|schedule|roster", q):
        workers = db["workers"]
        count = await workers.count_documents({})
        roles = {}
        async for doc in workers.find({}, {"role": 1}):
            r = doc.get("role", "unknown")
            roles[r] = roles.get(r, 0) + 1
        role_str = ", ".join(f"{v} {k}(s)" for k, v in sorted(roles.items()))
        return f"Workforce at {plant_name}: {count} workers — {role_str}.\nShift schedule details not available in current data."

    # Certifications
    if re.search(r"certif|accredit|qualif|compliance", q):
        sops = db["sops"]
        count = await sops.count_documents({})
        return f"{plant_name} has {count} SOPs/documents covering compliance and quality procedures. Specific certification records not available in current data."

    # Utilization
    if re.search(r"utili[sz]ation|efficiency|oee|performance", q):
        batches = db["production_batches"]
        total = await batches.count_documents({})
        completed = await batches.count_documents({"status": {"$in": ["Approved", "Released", "Completed"]}})
        rate = (completed / total * 100) if total else 0
        return f"Utilization for {plant_name}:\n  - Total batches: {total}\n  - Completed/Approved: {completed} ({rate:.1f}%)"

    # Default: plant summary
    machines = db["pharmaceutical_machines"]
    m_count = await machines.count_documents({})
    lines_ids = await machines.distinct("line_id")
    workers = db["workers"]
    w_count = await workers.count_documents({})
    return f"{plant_name}:\n  - {len(lines_ids)} production lines\n  - {m_count} machines\n  - {w_count} workers"


# ---------------------------------------------------------------------------
# Line-level queries
# ---------------------------------------------------------------------------


async def _answer_line_query(db, question):
    q = question.lower()
    machines = db["pharmaceutical_machines"]

    # Find line name/ID from question
    line_id = None
    line_ids = await machines.distinct("line_id")
    for lid in line_ids:
        if lid.lower() in q or lid.replace("-", " ").lower() in q:
            line_id = lid
            break
    # Try friendly names
    if not line_id:
        friendly = {
            "tablet line a": "LINE-TAB-001",
            "tablet line": "LINE-TAB-001",
            "capsule line b": "LINE-CAP-001",
            "capsule line": "LINE-CAP-001",
        }
        for pattern, lid in friendly.items():
            if pattern in q:
                line_id = lid
                break

    if not line_id and line_ids:
        line_id = line_ids[0]

    if not line_id:
        return "No production lines found."

    # "list all lines"
    if re.search(r"list all line|all line|what line", q) and not re.search(r"tablet line|capsule line", q):
        lines = []
        for lid in sorted(line_ids):
            count = await machines.count_documents({"line_id": lid})
            wss = db["workstations"]
            ws_count = await wss.count_documents({"line_id": lid})
            lines.append(f"  - {lid}: {count} machines, {ws_count} workstations")
        return f"Production lines:\n" + "\n".join(lines)

    # Stages in line
    if re.search(r"stage[s]?\b", q):
        cursor = machines.aggregate(
            [
                {"$match": {"line_id": line_id}},
                {"$group": {"_id": "$stage_id"}},
                {"$sort": {"_id": 1}},
            ]
        )
        stages = []
        async for doc in cursor:
            sid = doc["_id"]
            if sid:
                m_count = await machines.count_documents({"line_id": line_id, "stage_id": sid})
                wss = db["workstations"]
                ws_count = await wss.count_documents({"line_id": line_id, "stage_id": sid})
                stages.append(f"  - {sid}: {m_count} machines, {ws_count} workstations")
        if stages:
            return f"Stages in {line_id}:\n" + "\n".join(stages)
        return f"No stages found for {line_id}."

    # Type
    if re.search(r"\btype\b|kind|category", q):
        categories = await machines.distinct("type_category", {"line_id": line_id})
        if categories:
            return f"Machine types in {line_id}: {', '.join(categories)}"
        return f"No type information for {line_id}."

    # Manager/supervisor
    if re.search(r"manager|supervisor|who.*manage|lead", q):
        workers = db["workers"]
        ws_ids = await db["workstations"].distinct("workstation_id", {"line_id": line_id})
        cursor = workers.find(
            {
                "workstation_id": {"$in": ws_ids},
                "role": {"$in": ["supervisor", "manager"]},
            }
        ).limit(5)
        docs = await cursor.to_list(5)
        if docs:
            lines = [f"Management for {line_id}:"]
            for d in docs:
                lines.append(f"  - {d.get('name', '?')}: {d.get('title', 'N/A')} [{d.get('role', '?')}]")
            return "\n".join(lines)
        return f"No supervisor/manager found for {line_id}."

    # Takt time
    if re.search(r"takt|cycle time|beat", q):
        wss = db["workstations"]
        cursor = wss.find({"line_id": line_id}).limit(10)
        docs = await cursor.to_list(10)
        times = [
            (d.get("name", "?"), d.get("cycle_time_target"), d.get("cycle_time_actual"))
            for d in docs
            if d.get("cycle_time_target")
        ]
        if times:
            lines = [f"Takt/cycle times for {line_id}:"]
            for name, target, actual in times:
                lines.append(f"  - {name}: target={target}s, actual={actual}s")
            return "\n".join(lines)
        return f"No takt time data for {line_id}."

    # Capacity
    if re.search(r"capacity|throughput|output", q):
        m_count = await machines.count_documents({"line_id": line_id})
        wss = db["workstations"]
        ws_count = await wss.count_documents({"line_id": line_id})
        return f"Capacity for {line_id}: {m_count} machines, {ws_count} workstations"

    # Certifications
    if re.search(r"certif|accredit|qualif", q):
        sops = db["sops"]
        cursor = sops.find(
            {
                "$or": [
                    {"line_id": line_id},
                    {"title": {"$regex": line_id, "$options": "i"}},
                ]
            }
        ).limit(10)
        docs = await cursor.to_list(10)
        if docs:
            lines = [f"SOPs/certifications for {line_id}:"]
            for d in docs:
                lines.append(f"  - {d.get('id', '?')}: {d.get('title', 'N/A')}")
            return "\n".join(lines)
        return f"No specific certifications found for {line_id}. General plant SOPs apply."

    # Default: line summary
    m_count = await machines.count_documents({"line_id": line_id})
    wss = db["workstations"]
    ws_count = await wss.count_documents({"line_id": line_id})
    stages_cursor = machines.aggregate([{"$match": {"line_id": line_id}}, {"$group": {"_id": "$stage_id"}}])
    stage_count = len([s async for s in stages_cursor])
    return f"{line_id}: {stage_count} stages, {ws_count} workstations, {m_count} machines"


# ---------------------------------------------------------------------------
# Stage-level queries
# ---------------------------------------------------------------------------


async def _answer_stage_query(db, question):
    q = question.lower()
    machines = db["pharmaceutical_machines"]
    wss = db["workstations"]

    # Find stage from question
    stage_id = None
    # Try to match stage names to IDs
    stage_name_map = {
        "blending": "STAGE-01",
        "granulation": "STAGE-02",
        "sifting": "STAGE-03",
        "milling": "STAGE-03",
        "drying": "STAGE-04",
        "lubrication": "STAGE-05",
        "compression": "STAGE-06",
        "coating": "STAGE-07",
        "packaging": "STAGE-08",
        "dispensing": "STAGE-09",
        "weight check": "STAGE-10",
    }
    for name, sid in stage_name_map.items():
        if name in q:
            stage_id = sid
            break

    # Also try line-specific stages
    line_id = None
    for lid in await machines.distinct("line_id"):
        if lid.lower() in q or lid.replace("-", " ").lower() in q:
            line_id = lid
            break
    if not line_id:
        for pattern, lid in {
            "tablet line": "LINE-TAB-001",
            "capsule line": "LINE-CAP-001",
        }.items():
            if pattern in q:
                line_id = lid
                break

    # "list all stages"
    if re.search(r"list all stage|all stage|what stage", q):
        filter_q = {"line_id": line_id} if line_id else {}
        cursor = machines.aggregate(
            [
                {"$match": filter_q},
                {"$group": {"_id": "$stage_id", "line": {"$first": "$line_id"}}},
                {"$sort": {"_id": 1}},
            ]
        )
        stages = []
        async for doc in cursor:
            sid = doc["_id"]
            lid = doc.get("line", "?")
            if sid:
                m_count = await machines.count_documents({**filter_q, "stage_id": sid})
                ws_count = await wss.count_documents({**filter_q, "stage_id": sid})
                stages.append(f"  - {sid} (line {lid}): {m_count} machines, {ws_count} workstations")
        if stages:
            label = f" for {line_id}" if line_id else ""
            return f"Stages{label}:\n" + "\n".join(stages)
        return "No stages found."

    if not stage_id:
        return "Could not identify the stage from the question."

    # Takt time
    if re.search(r"takt|cycle time|beat", q):
        filter_q = {"stage_id": stage_id}
        if line_id:
            filter_q["line_id"] = line_id
        cursor = wss.find(filter_q).limit(10)
        docs = await cursor.to_list(10)
        times = [
            (d.get("name", "?"), d.get("cycle_time_target"), d.get("cycle_time_actual"))
            for d in docs
            if d.get("cycle_time_target")
        ]
        if times:
            lines = [f"Takt times for stage {stage_id}:"]
            for name, target, actual in times:
                lines.append(f"  - {name}: target={target}s, actual={actual}s")
            return "\n".join(lines)
        return f"No takt time data for stage {stage_id}."

    # Efficiency
    if re.search(r"efficien|performance|output|yield", q):
        filter_q = {"stage_id": stage_id}
        if line_id:
            filter_q["line_id"] = line_id
        m_count = await machines.count_documents(filter_q)
        ws_count = await wss.count_documents(filter_q)
        return f"Stage {stage_id}: {m_count} machines, {ws_count} workstations.\nEfficiency metrics not available in current data."

    # Workstations
    if re.search(r"workstation[s]?|station[s]?|who.*work|operator", q):
        filter_q = {"stage_id": stage_id}
        if line_id:
            filter_q["line_id"] = line_id
        cursor = wss.find(filter_q)
        docs = await cursor.to_list(20)
        if docs:
            lines = [f"Workstations in stage {stage_id}:"]
            for d in docs:
                lines.append(
                    f"  - {d.get('name', '?')} ({d.get('workstation_id', '?')}): operator={d.get('operator', 'N/A')}, status={d.get('status', '?')}"
                )
            return "\n".join(lines)
        return f"No workstations found for stage {stage_id}."

    # Default: stage summary
    filter_q = {"stage_id": stage_id}
    if line_id:
        filter_q["line_id"] = line_id
    m_count = await machines.count_documents(filter_q)
    ws_count = await wss.count_documents(filter_q)
    return f"Stage {stage_id}: {m_count} machines, {ws_count} workstations"


# ---------------------------------------------------------------------------
# Workstation-level queries
# ---------------------------------------------------------------------------


async def _answer_workstation_query(db, question):
    q = question.lower()
    wss = db["workstations"]
    machines = db["pharmaceutical_machines"]

    # Find workstation from question
    ws_name = None
    ws_id = None
    all_names = await wss.distinct("name")
    for name in all_names:
        if name.lower() in q:
            ws_name = name
            break

    # Also check line context
    line_id = None
    for lid in await machines.distinct("line_id"):
        if lid.lower() in q or lid.replace("-", " ").lower() in q:
            line_id = lid
            break
    if not line_id:
        for pattern, lid in {
            "tablet line": "LINE-TAB-001",
            "capsule line": "LINE-CAP-001",
        }.items():
            if pattern in q:
                line_id = lid
                break

    if ws_name:
        filter_q = {"name": ws_name}
        if line_id:
            filter_q["line_id"] = line_id
        doc = await wss.find_one(filter_q)
        if doc:
            ws_id = doc.get("workstation_id")

    if not ws_id:
        return "Could not identify the workstation from the question."

    doc = await wss.find_one({"workstation_id": ws_id})
    if not doc:
        return f"Workstation {ws_id} not found."

    # Worker/operator
    if re.search(r"worker|operator|who.*work|staff|person", q):
        return (
            f"Worker at {doc.get('name', ws_id)}:\n  - {doc.get('operator', 'N/A')} (station: {doc.get('name', '?')})"
        )

    # Machines
    if re.search(r"machine[s]?|equipment|device", q):
        cursor = machines.find({"workstation_id": ws_id})
        docs = await cursor.to_list(20)
        if docs:
            lines = [f"Machines at {doc.get('name', ws_id)}:"]
            for d in docs:
                lines.append(f"  - {d.get('name', '?')} ({d.get('machine_id', '?')}): {d.get('status', '?')}")
            return "\n".join(lines)
        return f"No machines found at {doc.get('name', ws_id)}."

    # Equipment (from pharmaceutical_equipment)
    if re.search(r"equipment|instrument|tool", q):
        equip = db["pharmaceutical_equipment"]
        cursor = equip.find({"workstation_id": ws_id})
        docs = await cursor.to_list(20)
        if docs:
            lines = [f"Equipment at {doc.get('name', ws_id)}:"]
            for d in docs:
                lines.append(f"  - {d.get('name', '?')} ({d.get('equipment_id', '?')}): {d.get('status', '?')}")
            return "\n".join(lines)
        return f"No equipment found at {doc.get('name', ws_id)}."

    # Constraints
    if re.search(r"constraint|limit|bottleneck|capacity", q):
        lines = [
            f"Workstation {doc.get('name', ws_id)}:",
            f"  - Status: {doc.get('status', '?')}",
            f"  - Mode: {doc.get('mode', '?')}",
            f"  - Bottleneck: {'Yes' if doc.get('is_bottleneck') else 'No'}",
            f"  - Cycle time target: {doc.get('cycle_time_target', 'N/A')}s",
            f"  - Cycle time actual: {doc.get('cycle_time_actual', 'N/A')}s",
        ]
        return "\n".join(lines)

    # Default: workstation summary
    lines = [
        f"Workstation: {doc.get('name', '?')} ({ws_id})",
        f"  - Line: {doc.get('line_id', '?')}",
        f"  - Stage: {doc.get('stage_id', '?')}",
        f"  - Operator: {doc.get('operator', 'N/A')}",
        f"  - Status: {doc.get('status', '?')}",
        f"  - Mode: {doc.get('mode', '?')}",
        f"  - Cycle time: {doc.get('cycle_time_actual', '?')}s (target: {doc.get('cycle_time_target', '?')}s)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Machine-level queries
# ---------------------------------------------------------------------------


async def _answer_machine_query(db, question):
    q = question.lower()
    machines = db["pharmaceutical_machines"]

    # Find machine from question — check machines first, then equipment as fallback
    machine_name = None
    all_names = await machines.distinct("name")
    for name in all_names:
        if name.lower() in q:
            machine_name = name
            break

    # Also check equipment collection
    equip = db["pharmaceutical_equipment"]
    equip_name_match = None
    if not machine_name:
        all_equip = await equip.distinct("name")
        for name in all_equip:
            if name.lower() in q:
                equip_name_match = name
                break

    if equip_name_match:
        # Route to equipment handler instead
        return await _answer_equipment_query(db, question)

    # Find line context
    line_id = None
    for lid in await machines.distinct("line_id"):
        if lid.lower() in q or lid.replace("-", " ").lower() in q:
            line_id = lid
            break
    if not line_id:
        for pattern, lid in {
            "tablet line": "LINE-TAB-001",
            "capsule line": "LINE-CAP-001",
        }.items():
            if pattern in q:
                line_id = lid
                break

    if not machine_name:
        return "Could not identify the machine from the question."

    filter_q = {"name": machine_name}
    if line_id:
        filter_q["line_id"] = line_id

    doc = await machines.find_one(filter_q)
    if not doc:
        return f"Machine '{machine_name}' not found" + (f" on {line_id}" if line_id else "") + "."

    # Manufacturer
    if re.search(r"manufacturer|maker|brand|vendor|supplier", q):
        return f"Manufacturer for {doc.get('name', '?')}:\n  - {doc.get('manufacturer', 'N/A')}\n  - Model: {doc.get('model_no', 'N/A')}\n  - Serial: {doc.get('serial_no', 'N/A')}"

    # Location
    if re.search(r"location|where|plant|line|stage|station", q):
        return f"Location for {doc.get('name', '?')}:\n  - Plant: {doc.get('plant_id', '?')}\n  - Line: {doc.get('line_id', '?')}\n  - Stage: {doc.get('stage_id', '?')}\n  - Workstation: {doc.get('workstation_id', '?')}"

    # Taxonomy
    if re.search(r"taxonom|type|category|class", q):
        return f"Taxonomy for {doc.get('name', '?')}:\n  - Type: {doc.get('type_category', 'N/A')}\n  - Area classification: {doc.get('area_classification', 'N/A')}"

    # Maintenance frequency
    if re.search(r"mainten|service|frequency|schedule|preventive", q):
        return f"Maintenance for {doc.get('name', '?')}:\n  - Specific maintenance schedule not available in current data.\n  - Check work orders for maintenance history."

    # Lifecycle
    if re.search(r"lifecycle|install|commission|decommission|age|date", q):
        return f"Lifecycle for {doc.get('name', '?')}:\n  - Created: {doc.get('created_at', 'N/A')}\n  - Updated: {doc.get('updated_at', 'N/A')}"

    # Work orders
    if re.search(r"work.?order|WO|calibration|maintenance event", q):
        wo = db["work_orders"]
        cursor = wo.find(
            {
                "$or": [
                    {"title": {"$regex": machine_name, "$options": "i"}},
                    {"description": {"$regex": machine_name, "$options": "i"}},
                    {"equipment_name": {"$regex": machine_name, "$options": "i"}},
                ]
            }
        ).limit(10)
        docs = await cursor.to_list(10)
        if docs:
            lines = [f"Work orders for {machine_name}:"]
            for d in docs:
                lines.append(f"  - {d.get('work_order_id', '?')}: {d.get('title', 'N/A')} [{d.get('status', '?')}]")
            return "\n".join(lines)
        return f"No work orders found for {machine_name}."

    # Status
    if re.search(r"status|state|condition|operational|running|down", q):
        return f"Status for {doc.get('name', '?')} ({doc.get('machine_id', '?')}):\n  - Status: {doc.get('status', 'N/A')}\n  - Description: {doc.get('description', 'N/A')}"

    # Default: machine summary
    return (
        f"Machine: {doc.get('name', '?')} ({doc.get('machine_id', '?')})\n"
        f"  - Type: {doc.get('type_category', 'N/A')}\n"
        f"  - Manufacturer: {doc.get('manufacturer', 'N/A')}\n"
        f"  - Serial: {doc.get('serial_no', 'N/A')}\n"
        f"  - Model: {doc.get('model_no', 'N/A')}\n"
        f"  - Status: {doc.get('status', 'N/A')}\n"
        f"  - Location: {doc.get('line_id', '?')} / {doc.get('stage_id', '?')} / {doc.get('workstation_id', '?')}"
    )


# ---------------------------------------------------------------------------
# Equipment-level queries
# ---------------------------------------------------------------------------


async def _answer_equipment_query(db, question):
    q = question.lower()
    equip = db["pharmaceutical_equipment"]

    # Find equipment from question — check equipment first, then machines as fallback
    equip_name = None
    all_names = await equip.distinct("name")
    for name in all_names:
        if name.lower() in q:
            equip_name = name
            break

    # Also check machines collection
    machines = db["pharmaceutical_machines"]
    machine_name_match = None
    if not equip_name:
        all_machines = await machines.distinct("name")
        for name in all_machines:
            if name.lower() in q:
                machine_name_match = name
                break

    if machine_name_match:
        # Route to machine handler instead
        return await _answer_machine_query(db, question)

    if not equip_name:
        return "Could not identify the equipment from the question."

    doc = await equip.find_one({"name": {"$regex": re.escape(equip_name), "$options": "i"}})
    if not doc:
        return f"Equipment '{equip_name}' not found."

    # Specification
    if re.search(r"spec|detail|info|what is|describe", q):
        lines = [f"Specification for {doc.get('name', '?')}:"]
        for k, v in doc.items():
            if k not in ("_id", "created_at", "updated_at") and v:
                lines.append(f"  - {k}: {v}")
        return "\n".join(lines)

    # Location
    if re.search(r"location|where|plant|line|stage|station", q):
        return f"Location for {doc.get('name', '?')}:\n  - Plant: {doc.get('plant_id', '?')}\n  - Line: {doc.get('line_id', '?')}\n  - Stage: {doc.get('stage_id', '?')}\n  - Workstation: {doc.get('workstation_id', '?')}"

    # Supplier
    if re.search(r"supplier|vendor|manufacturer|maker|brand", q):
        return f"Supplier for {doc.get('name', '?')}:\n  - Manufacturer: {doc.get('manufacturer', 'N/A')}"

    # Calibration
    if re.search(r"calibrat|calib", q):
        wo = db["work_orders"]
        cursor = (
            wo.find(
                {
                    "$or": [
                        {"title": {"$regex": equip_name, "$options": "i"}},
                        {"title": {"$regex": "calibration", "$options": "i"}},
                    ],
                    "status": {"$ne": "Cancelled"},
                }
            )
            .sort("created_at", -1)
            .limit(5)
        )
        docs = await cursor.to_list(5)
        if docs:
            lines = [f"Recent calibration events for {equip_name}:"]
            for d in docs:
                lines.append(f"  - {d.get('work_order_id', '?')}: {d.get('title', 'N/A')} [{d.get('status', '?')}]")
            return "\n".join(lines)
        return f"No calibration records found for {equip_name}."

    # Cleaning
    if re.search(r"clean|wash|sanit", q):
        return f"Cleaning records for {equip_name}: Not available in current data."

    # Maintenance
    if re.search(r"mainten|service|repair", q):
        wo = db["work_orders"]
        cursor = (
            wo.find(
                {
                    "$or": [
                        {"title": {"$regex": equip_name, "$options": "i"}},
                        {"description": {"$regex": equip_name, "$options": "i"}},
                    ]
                }
            )
            .sort("created_at", -1)
            .limit(5)
        )
        docs = await cursor.to_list(5)
        if docs:
            lines = [f"Maintenance records for {equip_name}:"]
            for d in docs:
                lines.append(f"  - {d.get('work_order_id', '?')}: {d.get('title', 'N/A')} [{d.get('status', '?')}]")
            return "\n".join(lines)
        return f"No maintenance records found for {equip_name}."

    # Type
    if re.search(r"\btype\b|kind|category|class", q):
        return f"Equipment type for {doc.get('name', '?')}:\n  - Category: {doc.get('category', 'N/A')}"

    # Status
    if re.search(r"status|state|condition|active|inactive", q):
        return f"Status for {doc.get('name', '?')}:\n  - Status: {doc.get('status', 'N/A')}"

    # Default: equipment summary
    return (
        f"Equipment: {doc.get('name', '?')} ({doc.get('equipment_id', '?')})\n"
        f"  - Category: {doc.get('category', 'N/A')}\n"
        f"  - Manufacturer: {doc.get('manufacturer', 'N/A')}\n"
        f"  - Status: {doc.get('status', 'N/A')}\n"
        f"  - Location: {doc.get('line_id', '?')} / {doc.get('stage_id', '?')} / {doc.get('workstation_id', '?')}"
    )


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def plant_hierarchy_question_answer(client, question, force=False):
    """Answer plant/line/stage/workstation/machine/equipment questions."""
    try:
        db = client["demo"]
        q = question.lower()

        # Work order queries FIRST (calibration keyword overlaps with equipment)
        if re.search(r"work.?order|WO-|calibration.*weight|assigned to.*work", q):
            from src.monkey_brain.kernel.plan.intents.predicates.work_order_query import (
                work_order_query_question_answer,
            )

            return await work_order_query_question_answer(client, question, force)

        if re.search(r"\bsop\b|procedure|protocol|work instruction", q):
            from src.monkey_brain.kernel.plan.intents.predicates.sop_query import (
                sop_query_question_answer,
            )

            return await sop_query_question_answer(client, question, force)

        # Determine query level — check equipment BEFORE machines for equipment-specific items
        # Equipment items: Vibro Sifter, Analytical Balance, sensors, instruments
        if re.search(
            r"\bequipment\b.*\b(Vibro Sifter|Force Feeder|Analytical Balance|Level Sensor|Force Feeder|Disintegration|Hardness|Friability|Moisture|Viscometer|Thermometer|Vernier|Timer|RPM Counter|Load Cell|Pressure|Temperature|RH|LOD|Spray|Seal|Pan RPM|Fill Weight|Compression|Inlet|Exhaust|Checkweigher|Coating|Capsule|Tablet|Vision)",
            q,
        ) and not re.search(r"workstation|station", q):
            return (await _answer_equipment_query(db, question), [], [], True)

        if re.search(
            r"\bequipment[s]?\b|instrument|sensor|balance|calibrat|analytical|disintegrat|hardness|friab|moisture|viscometer|thermometer|verni",
            q,
        ) and not re.search(r"workstation|station", q):
            return (await _answer_equipment_query(db, question), [], [], True)

        if re.search(
            r"\bmachine[s]?\b|force feeder|bin blender|rotary tablet|coating pan|granulator|press|deduster|polisher|packaging machine|bottle filler|capsule|blister|cartoning|metal detector",
            q,
        ) and not re.search(r"workstation|station", q):
            return (await _answer_machine_query(db, question), [], [], True)

        if re.search(r"worker|operator|who does|report to|shift|scheduled task|technician", q):
            # Check if it's about a specific worker
            workers = db["workers"]
            for w in await workers.find({}).to_list(100):
                if w.get("name", "").lower() in q:
                    # Delegate to worker handler
                    from src.monkey_brain.kernel.plan.intents.predicates.worker import (
                        worker_question_answer,
                    )

                    return await worker_question_answer(client, question, force)

        # Check stage BEFORE workstation (stage queries often ask about workstations within a stage)
        if re.search(
            r"\bstage[s]?\b|blending|granulation|drying|compression|coating|packaging|dispensing|lubrication",
            q,
        ):
            return (await _answer_stage_query(db, question), [], [], True)

        if re.search(r"workstation|station|blender station|sifter station|press station", q):
            return (await _answer_workstation_query(db, question), [], [], True)

        if re.search(r"\bline[s]?\b|tablet line|capsule line|production line", q):
            return (await _answer_line_query(db, question), [], [], True)

        if re.search(
            r"plant|facilit|site|how many plant|plant manager|plant location|plant certif|plant utili",
            q,
        ):
            return (await _answer_plant_query(db, question), [], [], True)

        # Fallback: try plant query
        return (await _answer_plant_query(db, question), [], [], True)

    except Exception as e:
        return (f"Error in plant hierarchy query: {e}", [], [], False)


# ---------------------------------------------------------------------------
# Predicate
# ---------------------------------------------------------------------------


def is_plant_hierarchy(question):
    """Match plant/line/stage/workstation/machine/equipment questions."""
    q = question.lower()
    keywords = [
        "plant",
        "line",
        "stage",
        "workstation",
        "station",
        "machine",
        "equipment",
        "blending",
        "granulation",
        "drying",
        "compression",
        "coating",
        "packaging",
        "dispensing",
        "tablet line",
        "capsule line",
        "takt",
        "cycle time",
        "manufacturer",
        "serial",
        "model",
        "calibration",
        "vibro sifter",
        "force feeder",
        "bin blender",
        "analytical balance",
        "rotary tablet press",
        "coating pan",
        "fluid bed dryer",
        "rapid mixer granulator",
        "conical mill",
        "how many plant",
        "plant manager",
        "plant location",
        "line manager",
        "line type",
        "line capacity",
        "stage efficiency",
        "stage takt",
        "workstation operator",
        "workstation machine",
        "machine status",
        "machine location",
        "machine manufacturer",
        "equipment status",
        "equipment location",
        "equipment type",
    ]
    return any(kw in q for kw in keywords)
