"""Actor System Configuration - Domain Mappings and Entity Definitions.

This file centralizes all hardcoded values that were previously scattered
throughout actor.py, making them configurable and maintainable.
"""

# Domain hint mappings: token -> (domain, entity_type)
DOMAIN_HINTS = {
    "weather": ("weather", ""),
    "flight": ("travel", "flight"),
    "fly": ("travel", "flight"),
    "doctor": ("healthcare", "doctor"),
    "physician": ("healthcare", "doctor"),
    "lawyer": ("legal", "lawyer"),
    "attorney": ("legal", "lawyer"),
    "taxi": ("transport", "taxi"),
    "uber": ("transport", "taxi"),
    "music": ("entertainment", "music"),
    "song": ("entertainment", "music"),
    "movie": ("entertainment", "movie"),
    "film": ("entertainment", "movie"),
    "recipe": ("cooking", "recipe"),
    "cook": ("cooking", "recipe"),
    "meeting": ("calendar", "meeting"),
    "schedule": ("calendar", "meeting"),
    "tutor": ("education", "tutor"),
    "teacher": ("education", "tutor"),
    "haircut": ("beauty", "haircut"),
    "barber": ("beauty", "haircut"),
    "spaceship": ("space", "spaceship"),
    "chess": ("games", "chess"),
}

# Entity to initial state mapping
ENTITY_TO_STATE = {
    "pizza": "order_pizza",
    "milk": "need_groceries",
    "groceries": "need_groceries",
    "flight": "search_flight",
    "doctor": "find_doctor",
    "lawyer": "search_lawyer",
    "taxi": "request_taxi",
    "music": "search_music",
    "movie": "search_movie",
    "recipe": "find_recipe",
    "meeting": "create_meeting",
    "tutor": "find_tutor",
    "haircut": "find_haircut",
    "spaceship": "find_spaceship",
    "chess": "play_chess",
}

# Domain question keywords for detection
DOMAIN_KEYWORDS = {
    "pizza": ["pizza", "order"],
    "milk": ["milk", "dairy"],
    "groceries": ["groceries", "grocery", "shopping"],
    "weather": ["weather", "forecast", "rain"],
    "flight": ["flight", "fly", "airport", "travel"],
    "doctor": ["doctor", "physician", "medical", "hospital"],
    "lawyer": ["lawyer", "attorney", "legal"],
    "taxi": ["taxi", "uber", "ride", "transport"],
    "music": ["music", "song", "play", "listen"],
    "movie": ["movie", "film", "netflix", "watch"],
    "recipe": ["recipe", "cook", "cooking"],
    "meeting": ["meeting", "schedule", "calendar"],
    "tutor": ["tutor", "teacher", "lesson"],
    "haircut": ["haircut", "barber", "salon"],
    "spaceship": ["spaceship", "space", "rocket"],
    "chess": ["chess", "game", "play"],
}

# Domain to start state mapping
DOMAIN_START_STATES = {
    "food": "order_pizza",
    "shopping": "need_groceries",
    "payment": "checkout",
    "weather": "check_weather",
    "travel": "search_flight",
    "healthcare": "find_doctor",
    "transport": "request_taxi",
    "entertainment": "search_music",
    "cooking": "find_recipe",
    "calendar": "create_meeting",
    "legal": "search_lawyer",
    "education": "find_tutor",
    "beauty": "find_haircut",
    "space": "find_spaceship",
    "games": "play_chess",
}

# Default world transitions (pizza + grocery domains)
DEFAULT_WORLD_TRANSITIONS = [
    # Pizza domain
    ("order_pizza", "select_restaurant", "food"),
    ("select_restaurant", "browse_menu", "food"),
    ("browse_menu", "select_pizza", "food"),
    ("select_pizza", "add_to_cart", "food"),
    ("add_to_cart", "checkout", "food"),
    ("checkout", "pay", "payment"),
    ("pay", "receive_pizza", "food"),
    ("receive_pizza", "task_complete", "food"),
    # Grocery domain
    ("need_groceries", "find_store", "shopping"),
    ("find_store", "browse_aisles", "shopping"),
    ("browse_aisles", "select_items", "shopping"),
    ("select_items", "add_to_cart", "shopping"),
    ("add_to_cart", "checkout", "shopping"),
    ("checkout", "pay", "payment"),
    ("pay", "receive_items", "shopping"),
    ("receive_items", "task_complete", "shopping"),
]

# Domain-specific transition templates
DOMAIN_TRANSITIONS = {
    "travel": [
        ("{entity}", "select_option", "travel"),
        ("select_option", "book_item", "travel"),
        ("book_item", "confirm_booking", "travel"),
        ("confirm_booking", "task_complete", "travel"),
    ],
    "healthcare": [
        ("{entity}", "select_provider", "healthcare"),
        ("select_provider", "book_appointment", "healthcare"),
        ("book_appointment", "confirm_appointment", "healthcare"),
        ("confirm_appointment", "task_complete", "healthcare"),
    ],
    "transport": [
        ("{entity}", "find_driver", "transport"),
        ("find_driver", "confirm_ride", "transport"),
        ("confirm_ride", "complete_ride", "transport"),
        ("complete_ride", "task_complete", "transport"),
    ],
    "entertainment": [
        ("{entity}", "select_content", "entertainment"),
        ("select_content", "play_content", "entertainment"),
        ("play_content", "task_complete", "entertainment"),
    ],
    "cooking": [
        ("{entity}", "select_recipe", "cooking"),
        ("select_recipe", "prepare_ingredients", "cooking"),
        ("prepare_ingredients", "cook_meal", "cooking"),
        ("cook_meal", "task_complete", "cooking"),
    ],
    "calendar": [
        ("{entity}", "select_time", "calendar"),
        ("select_time", "invite_participants", "calendar"),
        ("invite_participants", "confirm_meeting", "calendar"),
        ("confirm_meeting", "task_complete", "calendar"),
    ],
}

# Generic fallback transition template
GENERIC_FALLBACK_TRANSITIONS = [
    ("{entity}", "select_option", "{domain}"),
    ("select_option", "confirm_action", "{domain}"),
    ("confirm_action", "task_complete", "{domain}"),
]

# Default constraints
DEFAULT_CONSTRAINTS = [
    {"type": "delivery", "value": "standard"},
    {"type": "payment", "value": "online"},
]

# Planning parameters
PLANNING_CONFIG = {
    "budget": 20.0,  # seconds
    "max_steps": 20,
}

# Actor configuration
ACTOR_CONFIG = {
    "auto_register_unseen_domains": True,
    "confidence_threshold": 0.7,
    "default_confidence": 1.0,
}

# Global policies (enforced across all actors)
GLOBAL_POLICIES = {
    "max_execution_time": {
        "enabled": True,
        "rules": [{"value": 300, "unit": "seconds"}],  # 5 minutes
        "version": "1.0.0",
    },
    "max_planning_steps": {
        "enabled": True,
        "rules": [{"value": 20}],
        "version": "1.0.0",
    },
    "min_confidence": {
        "enabled": True,
        "rules": [{"value": 0.7}],
        "version": "1.0.0",
    },
}

# Local/domain-specific policies
LOCAL_POLICIES = {
    "healthcare": {
        "require_approval": True,
        "audit_level": "high",
        "compliance": ["HIPAA", "FDA"],
    },
    "manufacturing": {
        "safety_checks": ["lockout_tagout", "pre_start_safety"],
        "require_approval": True,
        "audit_level": "high",
        "compliance": ["GMP", "OSHA"],
    },
    "finance": {
        "require_approval": True,
        "audit_level": "high",
        "compliance": ["SOX", "NIST"],
    },
    "travel": {
        "require_approval": False,
        "audit_level": "medium",
        "compliance": [],
    },
    "food": {
        "require_approval": False,
        "audit_level": "low",
        "compliance": ["FDA"],
    },
    "shopping": {
        "require_approval": False,
        "audit_level": "low",
        "compliance": [],
    },
}

# Community-level policies (applied to groups of related domains)
COMMUNITY_POLICIES = {
    "food_services": {
        "domains": ["food", "grocery", "restaurant"],
        "require_approval": False,
        "audit_level": "low",
        "compliance": ["FDA"],
        "shared_rules": ["nutritional_info_required", "allergen_labeling"],
    },
    "healthcare_adjacent": {
        "domains": ["healthcare", "pharmacy", "wellness"],
        "require_approval": True,
        "audit_level": "high",
        "compliance": ["HIPAA", "FDA"],
        "shared_rules": ["phi_protection", "consent_required"],
    },
    "logistics": {
        "domains": ["travel", "shipping", "delivery"],
        "require_approval": False,
        "audit_level": "medium",
        "compliance": [],
        "shared_rules": ["tracking_required"],
    },
}
