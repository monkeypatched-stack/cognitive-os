#!/usr/bin/env python3
"""Seed machines, equipment, and instruments for Tablet and Capsule lines."""

import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings
from services.common.neo4j_mirror import (
    mirror_document,
    safe_mirror,
    close_neo4j_mirror_driver,
)

NOW = datetime.now(timezone.utc)
PLANT_ID = "PLANT-TBL-IN-001"
TAB_LINE = "LINE-TAB-001"
CAP_LINE = "LINE-CAP-001"

MACHINES = [
    # ── Tablet Line: Dispensing ──
    (
        "MCH-TAB-01-01",
        "Material Transfer Trolley",
        "Mechanical Machine",
        "WS-TAB-01-01",
        "STAGE-01",
    ),
    ("MCH-TAB-01-02", "Floor Scale", "Mechanical Machine", "WS-TAB-01-02", "STAGE-01"),
    ("MCH-TAB-01-03", "SS Bin", "Mechanical Machine", "WS-TAB-01-02", "STAGE-01"),
    ("MCH-TAB-01-04", "Pallet Truck", "Mechanical Machine", "WS-TAB-01-03", "STAGE-01"),
    # ── Tablet Line: Sifting/Milling ──
    (
        "MCH-TAB-02-01",
        "Dust Extractor",
        "Mechanical Machine",
        "WS-TAB-02-02",
        "STAGE-03",
    ),
    # ── Tablet Line: Granulation ──
    (
        "MCH-TAB-03-01",
        "Chopper Motor",
        "Electrical Machine",
        "WS-TAB-03-01",
        "STAGE-02",
    ),
    (
        "MCH-TAB-03-02",
        "Impeller Motor",
        "Electrical Machine",
        "WS-TAB-03-01",
        "STAGE-02",
    ),
    (
        "MCH-TAB-03-03",
        "Overhead Stirrer",
        "Electrical Machine",
        "WS-TAB-03-02",
        "STAGE-02",
    ),
    (
        "MCH-TAB-03-04",
        "Hot Water Generator",
        "Electrical Machine",
        "WS-TAB-03-02",
        "STAGE-02",
    ),
    # ── Tablet Line: Drying (FBD) ──
    (
        "MCH-TAB-04-01",
        "Product Container",
        "Mechanical Machine",
        "WS-TAB-04-01",
        "STAGE-09",
    ),
    (
        "MCH-TAB-04-02",
        "Finger Bag Set",
        "Mechanical Machine",
        "WS-TAB-04-01",
        "STAGE-09",
    ),
    (
        "MCH-TAB-04-03",
        "Discharge Trolley",
        "Mechanical Machine",
        "WS-TAB-04-02",
        "STAGE-09",
    ),
    ("MCH-TAB-04-04", "Sieve", "Mechanical Machine", "WS-TAB-04-02", "STAGE-09"),
    # ── Tablet Line: Sizing/Milling ──
    (
        "MCH-TAB-05-01",
        "Dust Extractor",
        "Mechanical Machine",
        "WS-TAB-05-01",
        "STAGE-10",
    ),
    # ── Tablet Line: Blending ──
    ("MCH-TAB-06-01", "Bin Lifter", "Mechanical Machine", "WS-TAB-06-01", "STAGE-04"),
    ("MCH-TAB-06-02", "SS Bin", "Mechanical Machine", "WS-TAB-06-01", "STAGE-04"),
    (
        "MCH-TAB-06-03",
        "RPM Controller",
        "Electrical Machine",
        "WS-TAB-06-02",
        "STAGE-04",
    ),
    (
        "MCH-TAB-06-04",
        "Lubricant Dispenser",
        "Mechanical Machine",
        "WS-TAB-06-03",
        "STAGE-04",
    ),
    # ── Tablet Line: Compression ──
    (
        "MCH-TAB-07-01",
        "Punch & Die Set",
        "Mechanical Machine",
        "WS-TAB-07-01",
        "STAGE-05",
    ),
    ("MCH-TAB-07-02", "Force Feeder", "Mechanical Machine", "WS-TAB-07-01", "STAGE-05"),
    (
        "MCH-TAB-07-03",
        "Sampling Spatula",
        "Mechanical Machine",
        "WS-TAB-07-02",
        "STAGE-05",
    ),
    (
        "MCH-TAB-07-04",
        "Tablet Deduster",
        "Mechanical Machine",
        "WS-TAB-07-03",
        "STAGE-05",
    ),
    (
        "MCH-TAB-07-05",
        "Metal Detector",
        "Electrical Machine",
        "WS-TAB-07-03",
        "STAGE-05",
    ),
    # ── Tablet Line: Coating ──
    (
        "MCH-TAB-08-01",
        "Overhead Stirrer",
        "Electrical Machine",
        "WS-TAB-08-02",
        "STAGE-06",
    ),
    (
        "MCH-TAB-08-02",
        "Peristaltic Pump",
        "Mechanical Machine",
        "WS-TAB-08-02",
        "STAGE-06",
    ),
    (
        "MCH-TAB-08-03",
        "Spray Gun Assembly",
        "Mechanical Machine",
        "WS-TAB-08-01",
        "STAGE-06",
    ),
    (
        "MCH-TAB-08-04",
        "Peristaltic Pump",
        "Mechanical Machine",
        "WS-TAB-08-01",
        "STAGE-06",
    ),
    ("MCH-TAB-08-05", "AHU", "Mechanical Machine", "WS-TAB-08-03", "STAGE-06"),
    (
        "MCH-TAB-08-06",
        "Exhaust Blower",
        "Mechanical Machine",
        "WS-TAB-08-03",
        "STAGE-06",
    ),
    # ── Tablet Line: Inspection ──
    (
        "MCH-TAB-09-01",
        "Inspection Conveyor",
        "Mechanical Machine",
        "WS-TAB-09-01",
        "STAGE-07",
    ),
    (
        "MCH-TAB-09-02",
        "Illuminated Inspection Table",
        "Mechanical Machine",
        "WS-TAB-09-01",
        "STAGE-07",
    ),
    (
        "MCH-TAB-09-03",
        "Metal Detector",
        "Electrical Machine",
        "WS-TAB-09-02",
        "STAGE-07",
    ),
    (
        "MCH-TAB-09-04",
        "Rejection Bin",
        "Mechanical Machine",
        "WS-TAB-09-02",
        "STAGE-07",
    ),
    (
        "MCH-TAB-09-05",
        "Tablet Polisher",
        "Mechanical Machine",
        "WS-TAB-09-03",
        "STAGE-07",
    ),
    (
        "MCH-TAB-09-06",
        "Dust Collector",
        "Mechanical Machine",
        "WS-TAB-09-03",
        "STAGE-07",
    ),
    # ── Tablet Line: Packaging ──
    (
        "MCH-TAB-10-01",
        "Blister Packing Machine",
        "CNC / Automated Machine",
        "WS-TAB-10-01",
        "STAGE-08",
    ),
    (
        "MCH-TAB-10-02",
        "Foil Unwinder",
        "Mechanical Machine",
        "WS-TAB-10-01",
        "STAGE-08",
    ),
    (
        "MCH-TAB-10-03",
        "Tablet Counter",
        "CNC / Automated Machine",
        "WS-TAB-10-02",
        "STAGE-08",
    ),
    (
        "MCH-TAB-10-04",
        "Bottle Filler",
        "CNC / Automated Machine",
        "WS-TAB-10-02",
        "STAGE-08",
    ),
    (
        "MCH-TAB-10-05",
        "Desiccant Inserter",
        "Mechanical Machine",
        "WS-TAB-10-02",
        "STAGE-08",
    ),
    ("MCH-TAB-10-06", "Cap Sealer", "Mechanical Machine", "WS-TAB-10-02", "STAGE-08"),
    (
        "MCH-TAB-10-07",
        "Self-Adhesive Labeller",
        "CNC / Automated Machine",
        "WS-TAB-10-03",
        "STAGE-08",
    ),
    (
        "MCH-TAB-10-08",
        "Batch Coding Machine",
        "CNC / Automated Machine",
        "WS-TAB-10-03",
        "STAGE-08",
    ),
    (
        "MCH-TAB-10-09",
        "Cartoning Machine",
        "CNC / Automated Machine",
        "WS-TAB-10-04",
        "STAGE-08",
    ),
    (
        "MCH-TAB-10-10",
        "Leaflet Inserter",
        "Mechanical Machine",
        "WS-TAB-10-04",
        "STAGE-08",
    ),
    # ── Capsule Line: Stages 1-6 same machines as tablet ──
    (
        "MCH-CAP-01-01",
        "Material Transfer Trolley",
        "Mechanical Machine",
        "WS-CAP-01-01",
        "STAGE-CAP-01",
    ),
    (
        "MCH-CAP-01-02",
        "Floor Scale",
        "Mechanical Machine",
        "WS-CAP-01-02",
        "STAGE-CAP-01",
    ),
    ("MCH-CAP-01-03", "SS Bin", "Mechanical Machine", "WS-CAP-01-02", "STAGE-CAP-01"),
    (
        "MCH-CAP-01-04",
        "Pallet Truck",
        "Mechanical Machine",
        "WS-CAP-01-03",
        "STAGE-CAP-01",
    ),
    (
        "MCH-CAP-02-01",
        "Dust Extractor",
        "Mechanical Machine",
        "WS-CAP-02-02",
        "STAGE-CAP-02",
    ),
    (
        "MCH-CAP-03-01",
        "Chopper Motor",
        "Electrical Machine",
        "WS-CAP-03-01",
        "STAGE-CAP-03",
    ),
    (
        "MCH-CAP-03-02",
        "Impeller Motor",
        "Electrical Machine",
        "WS-CAP-03-01",
        "STAGE-CAP-03",
    ),
    (
        "MCH-CAP-03-03",
        "Overhead Stirrer",
        "Electrical Machine",
        "WS-CAP-03-02",
        "STAGE-CAP-03",
    ),
    (
        "MCH-CAP-03-04",
        "Hot Water Generator",
        "Electrical Machine",
        "WS-CAP-03-02",
        "STAGE-CAP-03",
    ),
    (
        "MCH-CAP-04-01",
        "Product Container",
        "Mechanical Machine",
        "WS-CAP-04-01",
        "STAGE-CAP-04",
    ),
    (
        "MCH-CAP-04-02",
        "Finger Bag Set",
        "Mechanical Machine",
        "WS-CAP-04-01",
        "STAGE-CAP-04",
    ),
    (
        "MCH-CAP-04-03",
        "Discharge Trolley",
        "Mechanical Machine",
        "WS-CAP-04-02",
        "STAGE-CAP-04",
    ),
    ("MCH-CAP-04-04", "Sieve", "Mechanical Machine", "WS-CAP-04-02", "STAGE-CAP-04"),
    (
        "MCH-CAP-05-01",
        "Dust Extractor",
        "Mechanical Machine",
        "WS-CAP-05-01",
        "STAGE-CAP-05",
    ),
    (
        "MCH-CAP-06-01",
        "Bin Lifter",
        "Mechanical Machine",
        "WS-CAP-06-01",
        "STAGE-CAP-06",
    ),
    ("MCH-CAP-06-02", "SS Bin", "Mechanical Machine", "WS-CAP-06-01", "STAGE-CAP-06"),
    (
        "MCH-CAP-06-03",
        "RPM Controller",
        "Electrical Machine",
        "WS-CAP-06-02",
        "STAGE-CAP-06",
    ),
    (
        "MCH-CAP-06-04",
        "Lubricant Dispenser",
        "Mechanical Machine",
        "WS-CAP-06-03",
        "STAGE-CAP-06",
    ),
    # ── Capsule Line: Capsule Filling ──
    (
        "MCH-CAP-07-01",
        "Capsule Rectifier",
        "Mechanical Machine",
        "WS-CAP-07-01",
        "STAGE-CAP-07",
    ),
    (
        "MCH-CAP-07-02",
        "Orientation Sorter",
        "Mechanical Machine",
        "WS-CAP-07-01",
        "STAGE-CAP-07",
    ),
    (
        "MCH-CAP-07-03",
        "Tamping Pin Set",
        "Mechanical Machine",
        "WS-CAP-07-02",
        "STAGE-CAP-07",
    ),
    (
        "MCH-CAP-07-04",
        "Dosing Disc Set",
        "Mechanical Machine",
        "WS-CAP-07-02",
        "STAGE-CAP-07",
    ),
    (
        "MCH-CAP-07-05",
        "Closing Module",
        "Mechanical Machine",
        "WS-CAP-07-02",
        "STAGE-CAP-07",
    ),
    (
        "MCH-CAP-07-06",
        "Sampling Spatula",
        "Mechanical Machine",
        "WS-CAP-07-03",
        "STAGE-CAP-07",
    ),
    # ── Capsule Line: Polishing/Dedust ──
    (
        "MCH-CAP-08-01",
        "Capsule Polishing Machine",
        "Mechanical Machine",
        "WS-CAP-08-01",
        "STAGE-CAP-08",
    ),
    (
        "MCH-CAP-08-02",
        "Dust Collector",
        "Mechanical Machine",
        "WS-CAP-08-01",
        "STAGE-CAP-08",
    ),
    (
        "MCH-CAP-08-03",
        "Capsule Deduster",
        "Mechanical Machine",
        "WS-CAP-08-02",
        "STAGE-CAP-08",
    ),
    (
        "MCH-CAP-08-04",
        "Metal Detector",
        "Electrical Machine",
        "WS-CAP-08-02",
        "STAGE-CAP-08",
    ),
    # ── Capsule Line: Inspection ──
    (
        "MCH-CAP-09-01",
        "Inspection Conveyor",
        "Mechanical Machine",
        "WS-CAP-09-01",
        "STAGE-CAP-09",
    ),
    (
        "MCH-CAP-09-02",
        "Illuminated Inspection Table",
        "Mechanical Machine",
        "WS-CAP-09-01",
        "STAGE-CAP-09",
    ),
    (
        "MCH-CAP-09-03",
        "Metal Detector",
        "Electrical Machine",
        "WS-CAP-09-02",
        "STAGE-CAP-09",
    ),
    (
        "MCH-CAP-09-04",
        "Rejection Bin",
        "Mechanical Machine",
        "WS-CAP-09-02",
        "STAGE-CAP-09",
    ),
    # ── Capsule Line: Packaging ──
    (
        "MCH-CAP-10-01",
        "Blister Packing Machine",
        "CNC / Automated Machine",
        "WS-CAP-10-01",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-02",
        "Foil Unwinder",
        "Mechanical Machine",
        "WS-CAP-10-01",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-03",
        "Capsule Counter",
        "CNC / Automated Machine",
        "WS-CAP-10-02",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-04",
        "Bottle Filler",
        "CNC / Automated Machine",
        "WS-CAP-10-02",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-05",
        "Desiccant Inserter",
        "Mechanical Machine",
        "WS-CAP-10-02",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-06",
        "Cap Sealer",
        "Mechanical Machine",
        "WS-CAP-10-02",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-07",
        "Self-Adhesive Labeller",
        "CNC / Automated Machine",
        "WS-CAP-10-03",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-08",
        "Batch Coding Machine",
        "CNC / Automated Machine",
        "WS-CAP-10-03",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-09",
        "Cartoning Machine",
        "CNC / Automated Machine",
        "WS-CAP-10-04",
        "STAGE-CAP-10",
    ),
    (
        "MCH-CAP-10-10",
        "Leaflet Inserter",
        "Mechanical Machine",
        "WS-CAP-10-04",
        "STAGE-CAP-10",
    ),
]

EQUIPMENT = [
    # ── Tablet Line: Dispensing ──
    ("EQP-TAB-01-01", "LAF Sampling Booth", "Dispensing", "WS-TAB-01-01", "STAGE-01"),
    ("EQP-TAB-01-02", "LAF Weighing Booth", "Dispensing", "WS-TAB-01-02", "STAGE-01"),
    # ── Tablet Line: Sifting/Milling ──
    ("EQP-TAB-02-01", "Vibro Sifter", "Filtration", "WS-TAB-02-01", "STAGE-03"),
    ("EQP-TAB-02-02", "Conical Mill (Co-Mill)", "Machine", "WS-TAB-02-02", "STAGE-03"),
    ("EQP-TAB-02-03", "Vibro Sifter", "Filtration", "WS-TAB-02-03", "STAGE-03"),
    # ── Tablet Line: Granulation ──
    ("EQP-TAB-03-01", "Rapid Mixer Granulator", "Machine", "WS-TAB-03-01", "STAGE-02"),
    ("EQP-TAB-03-02", "Binder Prep Vessel", "Storage", "WS-TAB-03-02", "STAGE-02"),
    # ── Tablet Line: Drying ──
    ("EQP-TAB-04-01", "Fluid Bed Dryer", "Machine", "WS-TAB-04-01", "STAGE-09"),
    ("EQP-TAB-04-02", "Fluid Bed Dryer", "Machine", "WS-TAB-04-02", "STAGE-09"),
    # ── Tablet Line: Sizing/Milling ──
    ("EQP-TAB-05-01", "Conical Mill (Co-Mill)", "Machine", "WS-TAB-05-01", "STAGE-10"),
    ("EQP-TAB-05-02", "Vibro Sifter", "Filtration", "WS-TAB-05-02", "STAGE-10"),
    # ── Tablet Line: Blending ──
    (
        "EQP-TAB-06-02",
        "Octagonal / Double Cone Blender",
        "Machine",
        "WS-TAB-06-02",
        "STAGE-04",
    ),
    (
        "EQP-TAB-06-03",
        "Octagonal / Double Cone Blender",
        "Machine",
        "WS-TAB-06-03",
        "STAGE-04",
    ),
    # ── Tablet Line: Compression ──
    ("EQP-TAB-07-01", "Rotary Tablet Press", "Machine", "WS-TAB-07-01", "STAGE-05"),
    # ── Tablet Line: Coating ──
    ("EQP-TAB-08-01", "Coating Solution Vessel", "Storage", "WS-TAB-08-02", "STAGE-06"),
    (
        "EQP-TAB-08-02",
        "Auto Coater / Perforated Pan",
        "Machine",
        "WS-TAB-08-01",
        "STAGE-06",
    ),
    # ── Capsule Line: Stages 1-6 same as tablet ──
    (
        "EQP-CAP-01-01",
        "LAF Sampling Booth",
        "Dispensing",
        "WS-CAP-01-01",
        "STAGE-CAP-01",
    ),
    (
        "EQP-CAP-01-02",
        "LAF Weighing Booth",
        "Dispensing",
        "WS-CAP-01-02",
        "STAGE-CAP-01",
    ),
    ("EQP-CAP-02-01", "Vibro Sifter", "Filtration", "WS-CAP-02-01", "STAGE-CAP-02"),
    (
        "EQP-CAP-02-02",
        "Conical Mill (Co-Mill)",
        "Machine",
        "WS-CAP-02-02",
        "STAGE-CAP-02",
    ),
    ("EQP-CAP-02-03", "Vibro Sifter", "Filtration", "WS-CAP-02-03", "STAGE-CAP-02"),
    (
        "EQP-CAP-03-01",
        "Rapid Mixer Granulator",
        "Machine",
        "WS-CAP-03-01",
        "STAGE-CAP-03",
    ),
    ("EQP-CAP-03-02", "Binder Prep Vessel", "Storage", "WS-CAP-03-02", "STAGE-CAP-03"),
    ("EQP-CAP-04-01", "Fluid Bed Dryer", "Machine", "WS-CAP-04-01", "STAGE-CAP-04"),
    ("EQP-CAP-04-02", "Fluid Bed Dryer", "Machine", "WS-CAP-04-02", "STAGE-CAP-04"),
    (
        "EQP-CAP-05-01",
        "Conical Mill (Co-Mill)",
        "Machine",
        "WS-CAP-05-01",
        "STAGE-CAP-05",
    ),
    ("EQP-CAP-05-02", "Vibro Sifter", "Filtration", "WS-CAP-05-02", "STAGE-CAP-05"),
    (
        "EQP-CAP-06-02",
        "Octagonal / Double Cone Blender",
        "Machine",
        "WS-CAP-06-02",
        "STAGE-CAP-06",
    ),
    (
        "EQP-CAP-06-03",
        "Octagonal / Double Cone Blender",
        "Machine",
        "WS-CAP-06-03",
        "STAGE-CAP-06",
    ),
    # ── Capsule Line: Capsule Filling ──
    ("EQP-CAP-07-01", "Capsule Hopper", "Storage", "WS-CAP-07-01", "STAGE-CAP-07"),
    (
        "EQP-CAP-07-02",
        "Automatic Capsule Filling Machine",
        "Machine",
        "WS-CAP-07-02",
        "STAGE-CAP-07",
    ),
    # ── Capsule Line: Polishing/Dedust ── (no equipment in table)
    # ── Capsule Line: Inspection ── (no equipment in table)
    # ── Capsule Line: Packaging ── (no equipment in table)
]

INSTRUMENTS = [
    # ── Tablet Line: Dispensing ──
    ("INS-TAB-01-01", "Moisture Analyzer", "Instrument", "WS-TAB-01-01", "STAGE-01"),
    ("INS-TAB-01-02", "Analytical Balance", "Instrument", "WS-TAB-01-02", "STAGE-01"),
    # ── Tablet Line: Granulation ──
    ("INS-TAB-03-01", "RPM Indicator", "Instrument", "WS-TAB-03-01", "STAGE-02"),
    ("INS-TAB-03-02", "Load Cell", "Instrument", "WS-TAB-03-01", "STAGE-02"),
    ("INS-TAB-03-03", "Thermometer", "Instrument", "WS-TAB-03-02", "STAGE-02"),
    # ── Tablet Line: Drying ──
    (
        "INS-TAB-04-01",
        "Inlet Air Temp Sensor",
        "Instrument",
        "WS-TAB-04-01",
        "STAGE-09",
    ),
    ("INS-TAB-04-02", "Exhaust Temp Sensor", "Instrument", "WS-TAB-04-01", "STAGE-09"),
    (
        "INS-TAB-04-03",
        "LOD Moisture Analyzer",
        "Instrument",
        "WS-TAB-04-02",
        "STAGE-09",
    ),
    # ── Tablet Line: Blending ──
    ("INS-TAB-06-01", "RPM Counter", "Instrument", "WS-TAB-06-02", "STAGE-04"),
    ("INS-TAB-06-02", "Timer", "Instrument", "WS-TAB-06-02", "STAGE-04"),
    ("INS-TAB-06-03", "Timer", "Instrument", "WS-TAB-06-03", "STAGE-04"),
    # ── Tablet Line: Compression ──
    (
        "INS-TAB-07-01",
        "Compression Force Sensor",
        "Instrument",
        "WS-TAB-07-01",
        "STAGE-05",
    ),
    (
        "INS-TAB-07-02",
        "Pre-compression Sensor",
        "Instrument",
        "WS-TAB-07-01",
        "STAGE-05",
    ),
    ("INS-TAB-07-03", "Hardness Tester", "Instrument", "WS-TAB-07-02", "STAGE-05"),
    ("INS-TAB-07-04", "Friability Tester", "Instrument", "WS-TAB-07-02", "STAGE-05"),
    (
        "INS-TAB-07-05",
        "Disintegration Tester",
        "Instrument",
        "WS-TAB-07-02",
        "STAGE-05",
    ),
    ("INS-TAB-07-06", "Vernier Caliper", "Instrument", "WS-TAB-07-02", "STAGE-05"),
    ("INS-TAB-07-07", "Analytical Balance", "Instrument", "WS-TAB-07-02", "STAGE-05"),
    # ── Tablet Line: Coating ──
    ("INS-TAB-08-01", "Thermometer", "Instrument", "WS-TAB-08-02", "STAGE-06"),
    ("INS-TAB-08-02", "Viscometer", "Instrument", "WS-TAB-08-02", "STAGE-06"),
    ("INS-TAB-08-03", "Inlet Temp Sensor", "Instrument", "WS-TAB-08-01", "STAGE-06"),
    ("INS-TAB-08-04", "Exhaust Temp Sensor", "Instrument", "WS-TAB-08-01", "STAGE-06"),
    ("INS-TAB-08-05", "Pan RPM Sensor", "Instrument", "WS-TAB-08-01", "STAGE-06"),
    (
        "INS-TAB-08-06",
        "Spray Rate Controller",
        "Instrument",
        "WS-TAB-08-01",
        "STAGE-06",
    ),
    ("INS-TAB-08-07", "RH Sensor", "Instrument", "WS-TAB-08-03", "STAGE-06"),
    (
        "INS-TAB-08-08",
        "Differential Pressure Gauge",
        "Instrument",
        "WS-TAB-08-03",
        "STAGE-06",
    ),
    # ── Tablet Line: Packaging ──
    ("INS-TAB-10-01", "Seal Temp Sensor", "Instrument", "WS-TAB-10-01", "STAGE-08"),
    ("INS-TAB-10-02", "Leak Tester", "Instrument", "WS-TAB-10-01", "STAGE-08"),
    ("INS-TAB-10-03", "Fill Weight Sensor", "Instrument", "WS-TAB-10-02", "STAGE-08"),
    (
        "INS-TAB-10-04",
        "Vision Inspection System",
        "Instrument",
        "WS-TAB-10-03",
        "STAGE-08",
    ),
    ("INS-TAB-10-05", "Checkweigher", "Instrument", "WS-TAB-10-04", "STAGE-08"),
    # ── Capsule Line: Capsule Filling ──
    (
        "INS-CAP-07-01",
        "Fill Weight Sensor",
        "Instrument",
        "WS-CAP-07-02",
        "STAGE-CAP-07",
    ),
    (
        "INS-CAP-07-02",
        "Capsule Rejection Sensor",
        "Instrument",
        "WS-CAP-07-02",
        "STAGE-CAP-07",
    ),
    (
        "INS-CAP-07-03",
        "Analytical Balance",
        "Instrument",
        "WS-CAP-07-03",
        "STAGE-CAP-07",
    ),
    (
        "INS-CAP-07-04",
        "Disintegration Tester",
        "Instrument",
        "WS-CAP-07-03",
        "STAGE-CAP-07",
    ),
    ("INS-CAP-07-05", "Vernier Caliper", "Instrument", "WS-CAP-07-03", "STAGE-CAP-07"),
    # ── Capsule Line: Packaging ──
    ("INS-CAP-10-01", "Seal Temp Sensor", "Instrument", "WS-CAP-10-01", "STAGE-CAP-10"),
    ("INS-CAP-10-02", "Leak Tester", "Instrument", "WS-CAP-10-01", "STAGE-CAP-10"),
    (
        "INS-CAP-10-03",
        "Fill Weight Sensor",
        "Instrument",
        "WS-CAP-10-02",
        "STAGE-CAP-10",
    ),
    (
        "INS-CAP-10-04",
        "Vision Inspection System",
        "Instrument",
        "WS-CAP-10-03",
        "STAGE-CAP-10",
    ),
    ("INS-CAP-10-05", "Checkweigher", "Instrument", "WS-CAP-10-04", "STAGE-CAP-10"),
]


def _make_machine(mid, name, type_cat, ws_id, stage_id):
    line_id = TAB_LINE if mid.startswith("MCH-TAB") else CAP_LINE
    return {
        "_id": mid,
        "machine_id": mid,
        "name": name,
        "type_category": type_cat,
        "manufacturer": "Pharma Equipment Co.",
        "serial_no": f"SN-{mid}",
        "model_no": f"MOD-{mid}",
        "plant_id": PLANT_ID,
        "workstation_id": ws_id,
        "stage_id": stage_id,
        "line_id": line_id,
        "status": "Operational",
        "area_classification": "ISO 8",
        "description": name,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _make_equipment(eid, name, category, ws_id, stage_id):
    line_id = TAB_LINE if eid.startswith("EQP-TAB") else CAP_LINE
    return {
        "_id": eid,
        "equipment_id": eid,
        "name": name,
        "category": category,
        "manufacturer": "Pharma Equipment Co.",
        "workstation_id": ws_id,
        "plant_id": PLANT_ID,
        "stage_id": stage_id,
        "line_id": line_id,
        "status": "active",
        "description": name,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _make_instrument(iid, name, category, ws_id, stage_id):
    line_id = TAB_LINE if iid.startswith("INS-TAB") else CAP_LINE
    return {
        "_id": iid,
        "equipment_id": iid,
        "name": name,
        "category": category,
        "manufacturer": "Pharma Equipment Co.",
        "workstation_id": ws_id,
        "plant_id": PLANT_ID,
        "stage_id": stage_id,
        "line_id": line_id,
        "status": "active",
        "description": name,
        "created_at": NOW,
        "updated_at": NOW,
    }


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    await db["pharmaceutical_machines"].delete_many({})
    await db["pharmaceutical_equipment"].delete_many({})

    m_count = 0
    for mid, name, type_cat, ws_id, stage_id in MACHINES:
        doc = _make_machine(mid, name, type_cat, ws_id, stage_id)
        await db["pharmaceutical_machines"].insert_one(dict(doc))
        await safe_mirror(mirror_document("pharmaceutical_machines", doc, "seed"))
        m_count += 1

    e_count = 0
    for eid, name, category, ws_id, stage_id in EQUIPMENT:
        doc = _make_equipment(eid, name, category, ws_id, stage_id)
        await db["pharmaceutical_equipment"].insert_one(dict(doc))
        await safe_mirror(mirror_document("pharmaceutical_equipment", doc, "seed"))
        e_count += 1

    i_count = 0
    for iid, name, category, ws_id, stage_id in INSTRUMENTS:
        doc = _make_instrument(iid, name, category, ws_id, stage_id)
        await db["pharmaceutical_equipment"].insert_one(dict(doc))
        await safe_mirror(mirror_document("pharmaceutical_equipment", doc, "seed"))
        i_count += 1

    print(f"Machines:  {m_count}")
    print(f"Equipment: {e_count}")
    print(f"Instruments: {i_count}")
    print(f"Total: {m_count + e_count + i_count}")

    await close_neo4j_mirror_driver()
    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
