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
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# The four levels, as a closed set rather than a comment. In the first live run
# the model returned "Immediate" three times, which a bare `str` accepted
# silently — the documented vocabulary was only ever a comment, so nothing
# checked it. `Literal` makes it part of the schema sent to Gemini, so the
# model is constrained at generation time and a violation fails loudly here
# instead of reaching a client.
PRIORITY_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


class EvacuationPriority(BaseModel):
    locality_name: str  # was block_name — real unit is "locality", not "block"
    priority_level: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
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


ADVISORY_MODEL = "gemini-3.8-flash"
# Pinned per Rules.md ("Pin the exact Gemini model string in code ... don't
# silently swap model versions"), so this change is deliberate and recorded
# rather than a quiet edit.
#
# 2026-09-28: bumped 3.7 -> 3.8 after a live observation run. `gemini-3.7-flash`
# returned 503 UNAVAILABLE ("this model is currently experiencing high demand")
# on five consecutive attempts over ~4 minutes, while `gemini-3.8-flash`
# answered the same trivial prompt in 3.0s. `models.list()` confirmed 3.7 was
# still a valid, available model for the key — it was capacity-blocked, not
# misnamed, and not our payload's fault (a four-word prompt failed identically).
# Logged as RESOLVED in MEMORY.md "Flagged for review" #19. If 3.8 is ever
# blocked the same way, the swap is a one-line change here — but do it as a
# recorded decision with a live test behind it, not in passing.


VERIFIED_HISTORICAL_POOL = """
Verified past Bay of Bengal cyclones — pick the closest analog by wind
speed and surge, using ONLY these. Never name a storm outside this list.

- Cyclone Amphan (2020): landfall ~155-165 kmph, West Bengal coast,
  surge 4-5 m at North/South 24 Parganas.
- Cyclone Yaas (2021): landfall ~120-130 kmph, Odisha/West Bengal coast,
  ~1.1 million people evacuated.
- Cyclone Bulbul (2019): landfall ~91 kmph near Sagar Island, surge 3-5 ft.
""".strip()


VERIFIED_EMERGENCY_CONTACTS = """
Verified emergency contact numbers — the ONLY numbers permitted in any field
of the advisory. Use ONLY these. Never invent, recall, or guess a helpline
number. If no number below is suitable, write no number at all.

- 112 — India's national emergency number (all-in-one: police, fire, ambulance).
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
7. priority_level must be exactly one of CRITICAL, HIGH, MEDIUM, LOW — these
   four strings only, in capitals. Never use a word like "Immediate",
   "Urgent", or "Moderate".
8. Any emergency contact or helpline number anywhere in the output must come
   from the verified contacts pool below, and from nowhere else. Never
   invent or recall a number. If the pool does not contain a suitable number,
   omit the number entirely rather than supplying your own.
9. You must include EVERY locality listed in the allocation data: one
   evacuation_plan entry per locality in that list, no more and no fewer. A
   locality you leave out is a place with people in it that this advisory
   never tells anyone to leave. Do not merge two localities into one entry
   and do not write "and surrounding areas" — one row, one locality name, the
   exact string as given in the allocation list.

""" + VERIFIED_HISTORICAL_POOL + \
"\n\n" + VERIFIED_EMERGENCY_CONTACTS


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
    if response.parsed is None:
        # The response did not satisfy the schema (a bad `priority_level` is the
        # likely cause now that it is a Literal). Raise a named error so the
        # handler reports something readable instead of failing later on
        # `validate_advisory(None, ...)`.
        raise ValueError(
            "Gemini returned no schema-valid DistrictAdvisory. Raw text was: "
            f"{(response.text or '')[:300]!r}"
        )
    return response.parsed


def plan_coverage(advisory: DistrictAdvisory, allocation: dict) -> dict:
    """How much of the allocation the plan actually names.

    Counts ALLOCATION localities only. The code-built origin entry is not one of
    them — Sagar has no allocation row — so a complete plan reads 12/12 rather
    than being inflated to 13/12 by an entry the coverage rule never asked for.

    One definition, shared by the validator and by the response metadata, so
    the number a client is shown cannot drift from the number that was checked.
    """
    nodes = [row["node"] for row in allocation["allocation"]]
    named = {item.locality_name for item in advisory.evacuation_plan}
    covered = [node for node in nodes if node in named]
    missing = [node for node in nodes if node not in named]
    return {
        "covered": len(covered),
        "total": len(nodes),
        "missing": missing,
        "label": f"{len(covered)}/{len(nodes)}",
    }


def validate_advisory(
    advisory: DistrictAdvisory, allocation: dict, origin: str | None = None
) -> list[str]:
    """Post-hoc honesty checks, not just schema shape.

    `origin` is the locality the caller asked about, when there is one. It is
    permitted to appear in `evacuation_plan` even when it has no allocation
    row, because the caller builds that entry in code from real routing data
    (see `main._ensure_origin_in_plan`). Without this exemption the origin
    guarantee and the invented-locality check would cancel each other out.

    Coverage is checked in the other direction too: a plan that names only some
    of the allocation's localities leaves the rest with no priority at all, and
    in a real advisory that is a place with people in it going unmentioned. The
    missing names go into the violation string verbatim, because that string is
    what the correction pass feeds back to the model. Priorities for them are
    NOT invented in code — a level is a judgement, and the only party that can
    make one honestly is the model, re-asked.
    """
    violations = []
    if len(advisory.sms_dispatch_draft) >= 160:
        violations.append(
            f"sms_dispatch_draft is {len(advisory.sms_dispatch_draft)} chars, must be <160"
        )
    known = {row["node"] for row in allocation["allocation"]}
    if origin is not None:
        known.add(origin)
    for item in advisory.evacuation_plan:
        if item.locality_name not in known:
            violations.append(f"locality '{item.locality_name}' not in allocation data")
    coverage = plan_coverage(advisory, allocation)
    if coverage["missing"]:
        violations.append(
            f"evacuation_plan covers only {coverage['label']} of the allocation "
            f"localities; it must name all of them. Missing: "
            f"{', '.join(coverage['missing'])}"
        )
    if allocation["shelter_status"]["is_demo_data"]:
        joined = (advisory.executive_summary + advisory.sms_dispatch_draft).lower()
        if not any(w in joined for w in ("provisional", "placeholder", "not surveyed")):
            violations.append("demo shelter data used but no disclosure language found")
    return violations