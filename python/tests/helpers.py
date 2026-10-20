from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
SCENARIOS = sorted((FIXTURES / "scenarios").glob("*.json"))
ROUTES = json.loads((ROOT / "routes.example.json").read_text(encoding="utf-8"))["routes"]
SECRET = "whsec_examples_test"

BRICKELL = "a1b2c3d4-0002-4000-8000-000000000001"
SW2 = "a1b2c3d4-0002-4000-8000-000000000002"
FREMONT = "a1b2c3d4-0002-4000-8000-000000000022"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
