"""backend/ai/advisory.py — Module D: Gemini district advisory synthesis.

Field names below now match backend/main.py exactly, verified against the
uploaded source (not guessed):

    /surge-zone:  category, imd_category, wind_kmph, surge_m, surge{...},
                  final_land_area_km2, drawn_area_km2, is_estimate,
                  area_disclosure, frame_count, definitions, geojson
    /exposure:    hospitals{count, features}, substations{count, features},
                  roads_cut_off{count, features}, definitions, is_estimate
                  — district-wide totals, NOT broken out per locality
    /routes:      origin{id,name,lon,lat,...}, shelter{...},
                  shelter_assignment_basis, shelter_status{is_demo_data,
                  verified_shelters,...}, capacity_basis{shelters_are_real,
                  rule,...}, reachable, reason, length_km
    /allocation:  allocation[{node, assignments:[{shelter,...}]}],
                  shelter_loads, unmet_demand, total_person_km, message,
                  shelter_status, capacity_basis, population_method

There is no per-locality hospital/substation/road breakdown anywhere in the
API — exposure is district-wide only. Per-locality priority has to be built
from population + distance/reachability (/allocation, /routes), not from
infrastructure counts that don't exist at that granularity. Don't let the
prompt imply otherwise.
"""

import os
from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class EvacuationPriority(BaseModel):
    locality_name: str  # was block_name — real unit is "locality", not "block"
    priority_level: str  # CRITICAL, HIGH, MEDIUM, LOW
    reasoning: str


class DistrictAdvisory(BaseModel):
    executive_summary: str
    evacuation_plan: list[EvacuationPriority]
    sms_dispatch_draft: str = Field(description="Under 160 chars")
    post_landfall_risks: str = Field(
        description="Freshwater/salinization/livelihood risk narrative"
    )
    historical_context: str = Field(
        description="One-sentence comparison to a past Bay of Bengal cyclone"
    )


ADVISORY_MODEL = "gemini-3.7-flash"


VERIFIED_HISTORICAL_POOL = """
Verified past Bay of Bengal cyclones — pick the closest analog by wind
speed and surge, using ONLY these. Never name a storm outside this list.

- Cyclone Amphan (2020): landfall ~155-165 kmph, West Bengal coast,
  surge 4-5 m at North/South 24 Parganas.
- Cyclone Yaas (2021): landfall ~120-130 kmph, Odisha/West Bengal coast,
  ~1.1 million people evacuated.
- Cyclone Bulbul (2019): landfall ~91 kmph near Sagar Island, surge 3-5 ft.
""".strip()


SYSTEM_PROMPT = """You are the Emergency Response Officer for West Bengal
Disaster Management Authority. Write a district evacuation advisory from
the structured data given. Rules, non-negotiable:

1. If shelter_status.is_demo_data is true, say shelter capacity figures are
   provisional/placeholder, not surveyed. Never state a shelter occupancy
   number as a real facility count.
2. Use only final_land_area_km2 as the flooded area, never drawn_area_km2.
3. evacuation_plan locality_name values must come only from the localities
   listed in the allocation data given — never invent a locality.
4. Hospital/substation/road counts are DISTRICT-WIDE totals, not per
   locality. Do not attribute a specific hospital to a specific locality
   unless the data explicitly says so.
5. historical_context must name one storm from the verified pool below,
   never any other storm.
6. sms_dispatch_draft must be under 160 characters. Count before finalizing.

""" + VERIFIED_HISTORICAL_POOL


def build_prompt(
    surge_zone: dict,
    exposure: dict,
    allocation: dict,
    context: str = "",
    corrections: str = "",
) -> str:
    localities = [row["node"] for row in allocation["allocation"]]
    prompt = (
        f"CATEGORY: {surge_zone['imd_category']} ({surge_zone['wind_kmph']} kmph)\n"
        # Rounded for legibility: the raw float is 3.8625000000000043, which
        # reads as a defect and invites the model to quote it verbatim.
        f"SURGE: {surge_zone['surge_m']:.2f} m\n"
        f"FLOODED AREA: {surge_zone['final_land_area_km2']} km2 (modelled)\n"
        f"HOSPITALS AFFECTED (district-wide): {exposure['hospitals']['count']}\n"
        f"SUBSTATIONS AFFECTED (district-wide): {exposure['substations']['count']}\n"
        f"ROADS CUT OFF (district-wide): {exposure['roads_cut_off']['count']}\n"
        f"LOCALITIES IN THIS ALLOCATION: {localities}\n"
        f"UNMET DEMAND: {allocation['unmet_demand']}\n"
        f"SHELTER STATUS: {allocation['shelter_status']}\n"
        f"CAPACITY BASIS: {allocation['capacity_basis']}\n"
    )
    if context:
        prompt += f"\n{context}\n"
    if corrections:
        prompt += (
            "\nYour previous draft failed these checks. Fix every one and "
            f"return only the corrected JSON:\n{corrections}\n"
        )
    return prompt


def generate_advisory(
    surge_zone: dict,
    exposure: dict,
    allocation: dict,
    context: str = "",
    corrections: str = "",
) -> DistrictAdvisory:
    """Call Gemini and return a schema-validated DistrictAdvisory.

    `context` and `corrections` are optional and additive: with neither, this
    is a single clean call. `corrections` exists so the caller can re-ask once
    with the output of `validate_advisory` instead of surfacing a violation to
    the user — see the retry policy in main.py.
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=ADVISORY_MODEL,
        contents=[
            build_prompt(surge_zone, exposure, allocation, context, corrections)
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=DistrictAdvisory,
        ),
    )
    return response.parsed


def validate_advisory(advisory: DistrictAdvisory, allocation: dict) -> list[str]:
    """Post-hoc honesty checks, not just schema shape."""
    violations = []
    if len(advisory.sms_dispatch_draft) >= 160:
        violations.append(
            f"sms_dispatch_draft is {len(advisory.sms_dispatch_draft)} chars, must be <160"
        )
    known = {row["node"] for row in allocation["allocation"]}
    for item in advisory.evacuation_plan:
        if item.locality_name not in known:
            violations.append(f"locality '{item.locality_name}' not in allocation data")
    if allocation["shelter_status"]["is_demo_data"]:
        joined = (advisory.executive_summary + advisory.sms_dispatch_draft).lower()
        if not any(w in joined for w in ("provisional", "placeholder", "not surveyed")):
            violations.append("demo shelter data used but no disclosure language found")
    return violations