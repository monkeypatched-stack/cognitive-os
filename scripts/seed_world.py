#!/usr/bin/env python3
"""ONE canonical seed for the MonkeyBrain "living world" (actors, societies,
geography, affiliations, commerce) — the domain the Living World Explorer
frontend renders. Replaces any ad-hoc/one-off seeding.

Architecture rule this script enforces throughout: seed STATIC WORLD STATE
only (who/what/where exists, and their standing relationships) — never
RUNTIME EVENTS (transactions, beliefs, perturbations, conversations,
experiences, plans, execution history, grounding sessions). Those are
produced exclusively by the Planetary/Cognitive Runtime when a real request
executes (POST /prompt). A clean seed must leave every event-history
endpoint empty; `validate` checks this explicitly.

Everything here goes through the real HTTP API (the same one the frontend
and the runtime use) — POST /actors, /societies, /planet/geo, /memberships,
/actors/{id}/affiliations, /merchants, /products, /wallets, /riders,
/societies/{id}/governance-policies. Nothing writes to Redis/Neo4j/Mongo
directly, and nothing invents a parallel model.

WHY idempotency is by-name, not by-ID: audited the real routes before
writing this (see PR discussion) — POST /actors, POST /planet/geo, and
list_product()/onboard_merchant() (kernel/domains/commerce.py) all mint a
fresh uuid4 ID server-side with no caller-supplied-ID override in their
request models. True deterministic IDs are therefore not achievable through
the real API without bypassing it, which the architecture explicitly rules
out. Instead every create_* helper below first looks up existing state by
its natural key (name [+ parent/store/actor_type]) via the matching GET/list
route and skips creation if found — so `seed`, run twice against the same
world, converges to the identical logical structure (same names, same
relationships, same counts) even though the underlying IDs were only
assigned once, on first creation.

Usage:
    python3 scripts/seed_world.py flush      # wipe Redis (world is 100%
                                              # Redis-backed — actors,
                                              # societies, geography, KG all
                                              # reload from it on boot).
                                              # Run this ONLY while the
                                              # backend is stopped, then
                                              # start it fresh before `seed`.
    python3 scripts/seed_world.py seed        # idempotent: create the world
    python3 scripts/seed_world.py validate    # assert static > 0, events = 0
    python3 scripts/seed_world.py demo        # run "Buy 2 liters of milk"
                                               # as Priya Sharma
    python3 scripts/seed_world.py all         # seed + validate (no demo)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("SEED_WORLD_API_BASE", "http://localhost:8031/api/v1/agentos")
USER = "seed-world"
REDIS_HOST = os.environ.get("SEED_WORLD_REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("SEED_WORLD_REDIS_PORT", "6379"))
# Optional Bearer token — required against a deployment with
# AGENTOS_AUTH_REQUIRED=true, where X-User-ID alone (USER, above) is
# rejected with 401 "Bearer token required". Unset by default so this
# script's behavior against an auth-disabled/dev backend is unchanged.
TOKEN = os.environ.get("SEED_WORLD_TOKEN", "")


# ─── HTTP helpers ────────────────────────────────────────────────────────

def api(method: str, path: str, body: dict | None = None, ok404: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-User-ID", USER)
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code == 404 and ok404:
            return None
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}") from None


def get(path, ok404: bool = False):
    return api("GET", path, ok404=ok404)


def post(path, body):
    return api("POST", path, body)


def patch(path, body):
    return api("PATCH", path, body)


# ─── find-or-create helpers (see module docstring for why by-name) ───────

def ensure_actor(name: str, actor_type: str, description: str, society_id: str = "", goals=None, metadata=None) -> tuple[str, bool]:
    for a in get("/actors"):
        if a["name"] == name and a["actor_type"] == actor_type:
            # Real gap this closes: ensure_actor previously only ever applied
            # `metadata` (e.g. HUMAN_STRATEGY_METADATA) at creation — an actor
            # seeded before this parameter existed (or before a given key was
            # added to it) would short-circuit here and keep missing metadata
            # forever, since PATCH /actors/{id} had no way to set it either
            # (fixed alongside this — see gateway_models.py::ActorUpdateRequest.
            # metadata). ActorResponse doesn't surface metadata (GET /actors
            # can't tell us what's already there), so this always PATCHes when
            # metadata is given — harmless, since the PATCH handler merges
            # rather than replaces.
            if metadata:
                patch(f"/actors/{a['actor_id']}", {"metadata": metadata})
            return a["actor_id"], False
    result = post("/actors", {
        "name": name, "actor_type": actor_type, "description": description,
        "society_id": society_id, "goals": goals or [], "metadata": metadata or {},
    })
    return result["actor_id"], True


def ensure_address(actor_id: str, address_type: str, value: str, is_primary: bool = True) -> tuple[str, bool]:
    """DeliveryCapability (kernel/domains/grocery.py) resolves the
    recipient's EntityType.ADDRESS entity by actor_id/is_primary — real
    gap this closes: no seeded actor ever had one, so a genuinely
    successful order (product selected, paid, confirmed) still failed at
    the very last step with "no delivery address on file for actor"."""
    existing = get(f"/actors/{actor_id}/addresses").get("addresses", [])
    for a in existing:
        if a.get("value") == value:
            return a.get("address_id", ""), False
    result = post(f"/actors/{actor_id}/addresses", {
        "actor_id": actor_id, "address_type": address_type, "value": value, "is_primary": is_primary,
    })
    return result.get("address_id", ""), True


# Real gap this closes: EvaluateStrategyCapability (kernel/domains/
# grocery.py) deliberately refuses to fabricate a utility ranking when an
# actor has no metadata["strategy"]["preferences"] set — correct, honest
# behavior for an actor that genuinely has none. But no human actor here
# was ever seeded WITH preferences, so that honest refusal became an
# unconditional block: confirmed live, a real planner-authored plan
# ("compare Whole Milk vs 2% Milk vs Organic Whole Milk, then buy") failed
# at EvaluateStrategy with "actor has no strategy preferences," cascading
# through every dependent step. A grocery shopper weighing cost against
# speed is a genuine, real default for this domain (not an invented
# personality trait) — every human actor gets it below.
HUMAN_STRATEGY_METADATA = {"strategy": {"preferences": {"cost": 1.0, "speed": 0.4}}}


def ensure_society(name: str, description: str, society_type: str) -> tuple[str, bool]:
    for s in get("/societies"):
        if s["name"] == name:
            return s["society_id"], False
    result = post("/societies", {"name": name, "description": description, "society_type": society_type})
    return result["society_id"], True


def ensure_geo(entity_type: str, name: str, parent_id: str | None, description: str = "", **type_kwargs) -> tuple[str, bool]:
    for e in get(f"/planet/geo?entity_type={entity_type}"):
        if e["name"] == name and e.get("parent_id") == parent_id:
            return e["entity_id"], False
    body = {"entity_type": entity_type, "name": name, "description": description, **type_kwargs}
    if parent_id:
        body["parent_id"] = parent_id
    result = post("/planet/geo", body)
    return result["entity_id"], True


def host(space_id: str, society_id: str) -> None:
    post(f"/planet/geo/{space_id}/host", {"society_id": society_id})


def ensure_world_location(name: str, latitude: float, longitude: float, address: str = "") -> tuple[str, bool]:
    for loc in get("/world/locations")["locations"]:
        if loc["name"] == name:
            return loc["location_id"], False
    result = post("/world/locations", {"name": name, "address": address, "latitude": latitude, "longitude": longitude})
    return result["location_id"], True


def ensure_geo_location(entity_id: str, location_id: str) -> None:
    # PATCH is a plain set, always safe to repeat with the same value.
    api("PATCH", f"/planet/geo/{entity_id}/location", {"world_location_id": location_id})


def ensure_membership(actor_id: str, society_id: str, role: str = "member") -> bool:
    try:
        post("/memberships", {"actor_id": actor_id, "society_id": society_id, "role": role})
        return True
    except RuntimeError as e:
        if "409" in str(e):
            return False
        raise


def ensure_affiliation(actor_id: str, affiliation_type: str, target_id: str, target_name: str, **kwargs) -> bool:
    existing = get(f"/actors/{actor_id}/affiliations")["affiliations"]
    if any(a["affiliation_type"] == affiliation_type and a["target_id"] == target_id for a in existing):
        return False
    post(f"/actors/{actor_id}/affiliations", {
        "affiliation_type": affiliation_type, "target_id": target_id, "target_name": target_name, **kwargs,
    })
    return True


def ensure_merchant(owner_id: str, store_name: str, **attrs) -> tuple[str, bool]:
    for m in get("/merchants"):
        if m.get("owner_id") == owner_id:
            return m["store_id"], False
    result = post("/merchants", {"merchant_id": owner_id, "store_name": store_name, **attrs})
    return result["store_id"], True


def ensure_product(store_id: str, merchant_id: str, name: str, price: float, quantity: int, **attrs) -> bool:
    existing = get("/products")["products"]
    if any(p["store_id"] == store_id and p["name"] == name for p in existing):
        return False
    post("/products", {"store_id": store_id, "merchant_id": merchant_id, "name": name, "price": price, "quantity": quantity, **attrs})
    return True


def ensure_wallet(owner_id: str, balance: float, newly_created_actor: bool) -> bool:
    # No GET /wallets exists (audited) — wallet creation is guarded behind
    # "did we just create this actor in THIS run", not a lookup, since a
    # wallet only ever needs to exist once per actor and actor creation
    # itself is already idempotent above.
    if not newly_created_actor:
        return False
    # Plain debit/cash wallet, not UPI Reserve Pay — PaymentCapability
    # (kernel/domains/grocery.py) only routes a wallet through the real
    # Razorpay UPI integration when account_type=="upi_reserve_pay"; no
    # tag at all (the default here) keeps Payment on the local simulated
    # payment_processor/CAS-balance path, which needs no external
    # credentials and actually debits/checks `balance` below.
    post("/wallets", {"owner": owner_id, "balance": balance,
                       "name": f"{owner_id}'s Wallet"})
    return True


def ensure_rider(name: str, newly_created_world: bool, **attrs) -> bool:
    # No GET /riders exists (audited, same as ensure_wallet's own note) --
    # gated on "is this seed run the one that just created the world"
    # rather than a lookup. Without at least one of these, DeliveryCapability
    # (kernel/domains/grocery.py) fails every single order with "no riders
    # available" regardless of actor, address, or product -- found live:
    # a fully-addressed, fully-stocked "buy milk" request still couldn't
    # complete because no production seed ever created a real Rider PERSON
    # entity (POST /riders' own docstring flags this exact gap).
    if not newly_created_world:
        return False
    post("/riders", {"name": name, **attrs})
    return True


def ensure_governance_policy(society_id: str, name: str, description: str, policy_type: str, rules: list[str]) -> bool:
    existing = get(f"/societies/{society_id}/governance-policies")["policies"]
    if any(p["name"] == name for p in existing):
        return False
    post(f"/societies/{society_id}/governance-policies", {
        "name": name, "description": description, "policy_type": policy_type, "rules": rules,
    })
    return True


# ─── Seed ──────────────────────────────────────────────────────────────────

def seed() -> dict:
    counts = {k: 0 for k in (
        "geography", "world_locations", "societies", "humans", "enterprises", "memberships",
        "affiliations", "products", "wallets", "riders", "governance_policies", "addresses",
    )}

    # 1. Geography — real 8-tier hierarchy (kernel/geography/entity.py's
    # PARENT_TIER), not flattened. Universe/Region/Floor/Coordinate are
    # optional insertions per that file's own docstring; skipped here as
    # unneeded scope, not because they don't exist.
    earth, c = ensure_geo("planet", "Earth", None); counts["geography"] += c
    usa, c = ensure_geo("country", "United States", earth); counts["geography"] += c
    california, c = ensure_geo("state", "California", usa); counts["geography"] += c
    santa_clara, c = ensure_geo("county", "Santa Clara County", california); counts["geography"] += c
    sunnyvale, c = ensure_geo("city", "Sunnyvale", santa_clara); counts["geography"] += c
    maple_ave, c = ensure_geo("street", "Maple Avenue", sunnyvale); counts["geography"] += c

    homes, c = ensure_geo("building", "Maple Avenue Homes", maple_ave, building_type="residential"); counts["geography"] += c
    wf_plaza, c = ensure_geo("building", "Whole Foods Plaza", maple_ave, building_type="commercial"); counts["geography"] += c
    tj_plaza, c = ensure_geo("building", "Trader Joe's Plaza", maple_ave, building_type="commercial"); counts["geography"] += c
    sw_plaza, c = ensure_geo("building", "Safeway Plaza", maple_ave, building_type="commercial"); counts["geography"] += c

    sharma_space, c = ensure_geo("space", "Sharma Family Apartment", homes, space_type="apartment"); counts["geography"] += c
    community_space, c = ensure_geo("space", "Neighborhood Common House", homes, space_type="house"); counts["geography"] += c
    wf_space, c = ensure_geo("space", "Whole Foods Retail Unit", wf_plaza, space_type="retail_unit"); counts["geography"] += c
    tj_space, c = ensure_geo("space", "Trader Joe's Retail Unit", tj_plaza, space_type="retail_unit"); counts["geography"] += c
    sw_space, c = ensure_geo("space", "Safeway Retail Unit", sw_plaza, space_type="retail_unit"); counts["geography"] += c

    # Real coordinates (Sunnyvale, CA) on the 4 Buildings — the frontend
    # World Map only plots entities with a linked WorldLocation; Spaces
    # with no location of their own inherit their Building's marker
    # (MapView.tsx/mapUtils.ts), so linking just the Buildings is enough
    # to surface every Space too.
    for building_id, name, lat, lng, address in [
        (homes, "Maple Avenue Homes", 37.3705, -122.0380, "1200 Maple Ave, Sunnyvale, CA"),
        (wf_plaza, "Whole Foods Plaza", 37.3670, -122.0340, "1400 Maple Ave, Sunnyvale, CA"),
        (tj_plaza, "Trader Joe's Plaza", 37.3650, -122.0410, "1100 Maple Ave, Sunnyvale, CA"),
        (sw_plaza, "Safeway Plaza", 37.3720, -122.0300, "1500 Maple Ave, Sunnyvale, CA"),
    ]:
        loc_id, c = ensure_world_location(name, lat, lng, address)
        counts["world_locations"] += c
        ensure_geo_location(building_id, loc_id)

    # 2. Societies + hosting. Every society an actor will be registered
    # into at creation-time MUST be hosted at a real Space BEFORE that
    # actor is created — PlanetaryRuntime.register_actor() (integration.py)
    # enforces "Society is associated with a Space" and falls back to the
    # (wrong) default bootstrap Space otherwise; hosting first, unlike
    # actor creation, is a pure geography write, so this ordering is safe
    # to repeat.
    sharma_family, c = ensure_society("Sharma Family", "Priya Sharma's household", "family"); counts["societies"] += c
    friends, c = ensure_society("Friends", "Priya Sharma's friend circle", "community"); counts["societies"] += c
    neighborhood, c = ensure_society("Neighborhood", "Maple Avenue neighbors", "community"); counts["societies"] += c
    wf_team, c = ensure_society("Whole Foods Team", "Whole Foods store operations", "organizational"); counts["societies"] += c
    tj_team, c = ensure_society("Trader Joe's Team", "Trader Joe's store operations", "organizational"); counts["societies"] += c
    sw_team, c = ensure_society("Safeway Team", "Safeway store operations", "organizational"); counts["societies"] += c

    host(sharma_space, sharma_family)
    host(community_space, friends)
    host(community_space, neighborhood)
    host(wf_space, wf_team)
    host(tj_space, tj_team)
    host(sw_space, sw_team)

    # Reconcile away PlanetaryRuntime's eager bootstrap "Default Planet"
    # geography chain (kernel/society/integration.py) now that a real
    # "Earth" exists and every real Society above is hosted under it — the
    # bootstrap chain was only ever scaffolding so register_actor() always
    # had a Space to fall back to before any real geography existed. Must
    # run before Arjun Mehta's registration below (society_id="", targeting
    # PlanetaryRuntime's own bootstrap Default Society), so his fallback
    # hosting lands on the reconciled real geography, not the synthetic
    # chain this call is about to delete.
    post("/planet/geo/reconcile-default", {})

    # Real readiness check, not a sleep or blind retry (Qualification Gap
    # Closure, BUG-001): reconcile-default only sets a real
    # default_bootstrap_space_id as a SIDE EFFECT of migrating a synthetic
    # bootstrap chain that may not exist on this boot (e.g. Redis already
    # held partial geography from an earlier interrupted seed run) — in
    # that case reconcile-default silently no-ops and Arjun Mehta's
    # registration below (society_id="", relying on that fallback) would
    # fail non-deterministically depending on exactly what partial state
    # was already there. ensure-default-space is a second, narrower
    # primitive that establishes a real fallback Space unconditionally,
    # given only that "Earth" (created above) exists — confirmed here
    # before proceeding, rather than discovered via a raised
    # RuntimeError from deep inside POST /actors.
    readiness = post("/planet/geo/ensure-default-space", {})
    if not readiness.get("space_id"):
        raise RuntimeError(
            "seed_world.py: /planet/geo/ensure-default-space returned no "
            "space_id after creating the canonical 'Earth' root above — "
            "geography bootstrap is not ready; refusing to register Arjun "
            "Mehta (society_id=\"\") against an unconfigured fallback Space."
        )

    # 3. Humans. Priya Sharma is the primary demo actor. Family shares her
    # home society; friend/neighbor each have their own so they're placed
    # at a distinct, real Space rather than borrowed geography. Arjun Mehta
    # (independent second shopper, for the "find cheapest eggs" comparison
    # scenario) deliberately uses society_id="" -> PlanetaryRuntime's own
    # bootstrap Default Society, since he needs no household of his own —
    # a deliberate scope cut, not an oversight.
    priya, c = ensure_actor("Priya Sharma", "human", "Customer managing household groceries", sharma_family,
                             goals=["buy groceries efficiently", "stay within household budget"],
                             metadata=HUMAN_STRATEGY_METADATA)
    counts["humans"] += c
    priya_new = c
    _, c = ensure_address(priya, "delivery", "1420 Maple Ave, Sunnyvale, CA", is_primary=True)
    counts["addresses"] += c
    raj, c = ensure_actor("Raj Sharma", "human", "Priya's spouse", sharma_family, metadata=HUMAN_STRATEGY_METADATA); counts["humans"] += c; raj_new = c
    ananya, c = ensure_actor("Ananya Sharma", "human", "Priya and Raj's daughter", sharma_family, metadata=HUMAN_STRATEGY_METADATA); counts["humans"] += c; ananya_new = c
    alice, c = ensure_actor("Alice Chen", "human", "Priya's friend", friends, metadata=HUMAN_STRATEGY_METADATA); counts["humans"] += c; alice_new = c
    bob, c = ensure_actor("Bob Martinez", "human", "Priya's neighbor on Maple Avenue", neighborhood, metadata=HUMAN_STRATEGY_METADATA); counts["humans"] += c; bob_new = c
    arjun, c = ensure_actor("Arjun Mehta", "human", "Independent customer comparing grocery prices", "",
                             goals=["find the best grocery deals"], metadata=HUMAN_STRATEGY_METADATA)
    counts["humans"] += c
    arjun_new = c

    # 4. Enterprises — one per store, home society = that store's own team.
    whole_foods, c = ensure_actor("Whole Foods", "enterprise", "Grocery store chain", wf_team); counts["enterprises"] += c
    wf_new = c
    trader_joes, c = ensure_actor("Trader Joe's", "enterprise", "Neighborhood grocery store", tj_team); counts["enterprises"] += c
    tj_new = c
    safeway, c = ensure_actor("Safeway", "enterprise", "Full-service grocery store", sw_team); counts["enterprises"] += c
    sw_new = c

    # 5. Explicit memberships (Membership as a First-Class Runtime
    # Resource — kernel/society/membership.py — distinct from the
    # affiliation edges in step 6; POST /actors already created each
    # actor's HOME membership, these are additional ones only where the
    # demo needs them). None needed beyond home registration for this
    # world, so nothing further to add here — kept as an explicit no-op
    # step so the section isn't silently missing from the script.

    # 6. Affiliations — the static affiliation topology (kernel/
    # affiliations/types.py's real 48-type catalog; ids verified against
    # that file, not invented). Reciprocal pairs get BOTH directions
    # written explicitly (one-sided-write API, audited) for family/
    # friendship/neighbor; customer-of-store stays one-directional since
    # that relationship is inherently asymmetric.
    for a_id, a_name, b_id, b_name, rel in [
        (priya, "Priya Sharma", raj, "Raj Sharma", "marriage"),
        (raj, "Raj Sharma", priya, "Priya Sharma", "marriage"),
        (priya, "Priya Sharma", ananya, "Ananya Sharma", "family"),
        (raj, "Raj Sharma", ananya, "Ananya Sharma", "family"),
        (ananya, "Ananya Sharma", priya, "Priya Sharma", "daughter_of"),
        (ananya, "Ananya Sharma", raj, "Raj Sharma", "daughter_of"),
        (priya, "Priya Sharma", alice, "Alice Chen", "friendship"),
        (alice, "Alice Chen", priya, "Priya Sharma", "friendship"),
        (priya, "Priya Sharma", bob, "Bob Martinez", "affiliated_with"),
        (bob, "Bob Martinez", priya, "Priya Sharma", "affiliated_with"),
    ]:
        counts["affiliations"] += ensure_affiliation(a_id, rel, b_id, b_name)

    for actor_id, society_id, society_name in [
        (priya, sharma_family, "Sharma Family"), (raj, sharma_family, "Sharma Family"),
        (ananya, sharma_family, "Sharma Family"), (alice, friends, "Friends"),
        (bob, neighborhood, "Neighborhood"),
    ]:
        counts["affiliations"] += ensure_affiliation(actor_id, "member_of", society_id, society_name)

    for shopper_id in (priya, arjun):
        for store_id, store_name in [(whole_foods, "Whole Foods"), (trader_joes, "Trader Joe's"), (safeway, "Safeway")]:
            counts["affiliations"] += ensure_affiliation(shopper_id, "customer", store_id, store_name)

    # 7 + 8 + 9. Inventory + pricing — real Store/Product KG entities,
    # price set per-product-per-store (there's no separate pricing model
    # to seed — ProductCreateRequest.price IS the price).
    wf_store, c = ensure_merchant(whole_foods, "Whole Foods", delivery_fee=2.99, address="Whole Foods Plaza, Maple Avenue, Sunnyvale, CA")
    for name, price, qty, unit, category in [
        ("Whole Milk (1 Gallon)", 4.29, 50, "gallon", "dairy"),
        ("Organic Whole Milk (1 Gallon)", 6.49, 20, "gallon", "dairy"),
        ("Large Eggs (Dozen)", 5.49, 80, "dozen", "dairy"),
        ("Sourdough Bread", 6.99, 30, "loaf", "bakery"),
        ("Ground Coffee (12oz)", 9.99, 40, "bag", "pantry"),
        ("Frozen Cheese Pizza", 7.99, 40, "each", "frozen"),
    ]:
        counts["products"] += ensure_product(wf_store, whole_foods, name, price, qty, unit=unit, category=category)

    tj_store, c = ensure_merchant(trader_joes, "Trader Joe's", delivery_fee=1.99, address="Trader Joe's Plaza, Maple Avenue, Sunnyvale, CA")
    for name, price, qty, unit, category in [
        ("2% Milk (1 Gallon)", 3.49, 40, "gallon", "dairy"),
        ("Cage-Free Eggs (Dozen)", 4.29, 60, "dozen", "dairy"),
        ("Bananas", 0.29, 200, "lb", "produce"),
        ("Organic Avocados", 1.49, 100, "each", "produce"),
        ("Butter (1 lb)", 4.99, 35, "lb", "dairy"),
    ]:
        counts["products"] += ensure_product(tj_store, trader_joes, name, price, qty, unit=unit, category=category)

    sw_store, c = ensure_merchant(safeway, "Safeway", delivery_fee=3.49, address="Safeway Plaza, Maple Avenue, Sunnyvale, CA")
    for name, price, qty, unit, category in [
        ("Whole Milk (1 Gallon)", 3.99, 45, "gallon", "dairy"),
        ("Large Eggs (Dozen)", 4.79, 70, "dozen", "dairy"),
        ("Chicken Breast (per lb)", 5.99, 50, "lb", "meat"),
        ("Butter (1 lb)", 4.49, 30, "lb", "dairy"),
        ("Wheat Bread", 3.99, 40, "loaf", "bakery"),
    ]:
        counts["products"] += ensure_product(sw_store, safeway, name, price, qty, unit=unit, category=category)

    # Wallets — only for actors this run actually created (see
    # ensure_wallet's docstring for why that's the correct idempotency
    # guard given there's no GET /wallets to look up against).
    for actor_id, is_new, balance in [
        (priya, priya_new, 250.0), (raj, raj_new, 200.0), (ananya, ananya_new, 20.0),
        (alice, alice_new, 150.0), (bob, bob_new, 150.0), (arjun, arjun_new, 150.0),
    ]:
        counts["wallets"] += ensure_wallet(actor_id, balance, is_new)

    # Delivery riders — real Rider PERSON entities DeliveryCapability
    # assigns orders to (see ensure_rider's docstring). Two riders so a
    # cart isn't forced onto a single point of failure; one refrigerated
    # for any future cold-chain product even though today's seeded
    # products don't set attributes["cold_chain"].
    counts["riders"] += ensure_rider("Sam Rivera", priya_new, rating=4.8, estimated_minutes=25.0)
    counts["riders"] += ensure_rider("Jordan Lee", priya_new, rating=4.6, estimated_minutes=35.0, refrigerated=True)

    # 10-12. Capabilities / Agents / Providers: audited — no persistent,
    # seedable model exists for any of these (api/routes/capabilities.py
    # and agents.py are read-only over code-registered state; no
    # providers.py route file at all). Nothing to seed here; the frontend's
    # Capability/Agent graph renders directly from the 295 Broca agents
    # and their registered capabilities, which exist as soon as the
    # backend process boots, seed or no seed.

    # 13. Governance policies — real, persisted (Society.
    # SocietyGovernanceEngine), one per society that plausibly needs one
    # for the demo.
    counts["governance_policies"] += ensure_governance_policy(
        sharma_family, "Household Grocery Budget", "Keep grocery spending within the household budget.",
        "guideline", ["prefer in-stock items", "compare prices across stores before ordering"],
    )
    for team_id, store_name in [(wf_team, "Whole Foods"), (tj_team, "Trader Joe's"), (sw_team, "Safeway")]:
        counts["governance_policies"] += ensure_governance_policy(
            team_id, "Fair Pricing Policy", f"{store_name} lists honest, currently-available pricing.",
            "guideline", ["price must reflect real available inventory"],
        )

    counts["_ids"] = {
        "priya_sharma": priya, "raj_sharma": raj, "ananya_sharma": ananya,
        "alice_chen": alice, "bob_martinez": bob, "arjun_mehta": arjun,
        "whole_foods": whole_foods, "trader_joes": trader_joes, "safeway": safeway,
    }
    return counts


# ─── Validate ────────────────────────────────────────────────────────────

def validate() -> bool:
    ok = True

    def check_static(label, n):
        nonlocal ok
        status = "OK" if n > 0 else "MISSING"
        if n == 0:
            ok = False
        print(f"  {label:<28} {n:>4}  [{status}]")

    def check_zero(label, n):
        nonlocal ok
        status = "OK" if n == 0 else "NOT ZERO"
        if n != 0:
            ok = False
        print(f"  {label:<28} {n:>4}  [{status}]")

    actors = get("/actors")
    humans = [a for a in actors if a["actor_type"] == "human"]
    enterprises = [a for a in actors if a["actor_type"] == "enterprise"]
    societies = get("/societies")
    geo = get("/planet/geo")
    products = get("/products")["products"]
    merchants = get("/merchants")

    print("STATIC STATE")
    check_static("Humans", len(humans))
    check_static("Enterprises", len(enterprises))
    check_static("Societies", len(societies))
    check_static("Geography entities", len(geo))
    check_static("Merchants/stores", len(merchants))
    check_static("Products", len(products))

    total_affiliations = 0
    total_executions = 0
    for a in actors:
        affs = get(f"/actors/{a['actor_id']}/affiliations")["affiliations"]
        total_affiliations += len(affs)
        hist = get(f"/actors/{a['actor_id']}/execution-history", ok404=True)
        if hist is not None:
            total_executions += len(hist) if isinstance(hist, list) else len(hist.get("history", []))
    check_static("Affiliations (all actors)", total_affiliations)

    print("\nEVENT STATE (must be zero on a clean seed)")
    check_zero("Execution history (all actors)", total_executions)

    print(f"\n{'PASS' if ok else 'FAIL'}")
    return ok


# ─── Demo ────────────────────────────────────────────────────────────────

def demo() -> None:
    actors = get("/actors")
    priya = next((a for a in actors if a["name"] == "Priya Sharma"), None)
    if priya is None:
        print("Priya Sharma not found — run `seed` first.")
        sys.exit(1)
    print(f"POST /prompt as Priya Sharma ({priya['actor_id']}): \"Buy 2 liters of milk.\"")
    req = urllib.request.Request(
        BASE + "/prompt", method="POST",
        data=json.dumps({"question": "Buy 2 liters of milk.", "run_simulate": False}).encode(),
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-User-ID", priya["actor_id"])
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read())
    print(json.dumps(result, indent=2)[:2000])


# ─── Flush ───────────────────────────────────────────────────────────────

def flush_redis() -> None:
    import socket
    print(f"Connecting to redis {REDIS_HOST}:{REDIS_PORT} ...")
    s = socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=5)
    cmd = b"FLUSHDB"
    s.sendall(b"*1\r\n$" + str(len(cmd)).encode() + b"\r\n" + cmd + b"\r\n")
    reply = s.recv(100)
    s.close()
    if reply.startswith(b"+OK"):
        print("Redis FLUSHDB: OK")
        print("The world is 100% Redis-backed and reloads on boot — RESTART the")
        print("agentos backend now (it will boot with zero actors/societies/")
        print("geography/KG), then run: python3 scripts/seed_world.py seed")
    else:
        print(f"Unexpected reply: {reply!r}")
        sys.exit(1)


# ─── Main ────────────────────────────────────────────────────────────────

def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if cmd == "flush":
        flush_redis()
    elif cmd == "seed":
        counts = seed()
        ids = counts.pop("_ids")
        print("Seed complete\n")
        for k, v in counts.items():
            print(f"  {k.replace('_', ' ').title():<24} {v:>4}")
        print("\nPrimary demo actor: Priya Sharma", ids["priya_sharma"])
        print("\nRuntime events (unseeded, produced only by the runtime):")
        print("  Transactions / Beliefs / Perturbations / Grounding sessions: 0")
        print("\nWorld is ready. Run `python3 scripts/seed_world.py validate` to verify,")
        print("or `python3 scripts/seed_world.py demo` to run the demo request.")
    elif cmd == "validate":
        ok = validate()
        sys.exit(0 if ok else 1)
    elif cmd == "demo":
        demo()
    elif cmd == "all":
        seed()
        print()
        ok = validate()
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
