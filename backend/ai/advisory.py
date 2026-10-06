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
        "=== COMPUTED FIGURES ===\n"
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
        prompt += f"\n=== REQUESTING LOCALITY ===\n{context}\n"
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
    return _generate_content(
        model=ADVISORY_MODEL,
        prompt=build_prompt(surge_zone, exposure, allocation, context, corrections),
        system_instruction=SYSTEM_PROMPT,
        schema=DistrictAdvisory,
    )


def _generate_content(
    *, model: str, prompt: str, system_instruction: str, schema: type
) -> object:
    """One Gemini call, the same way for both generators.

    Split out so `/risk-analyst` cannot quietly differ from `/advisory` on
    temperature, mime type or schema enforcement — the three settings that decide
    whether a response is a parseable structured object or prose that looks like
    one. It is also the seam the tests patch: a test that replaces this replaces
    the network, and nothing above it.

    `model` is passed rather than read from the constant here so the *call site*
    names it, which makes "which model does this use" answerable by reading the
    function that uses it. Both callers pass `ADVISORY_MODEL`; neither may pass
    anything else (see `test_the_model_string_is_pinned_with_no_fallback`).
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=model,
        contents=[prompt],
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    if response.parsed is None:
        # The response did not satisfy the schema (a bad `priority_level` is the
        # likely cause now that it is a Literal). Raise a named error so the
        # handler reports something readable instead of failing later on
        # `validate_advisory(None, ...)`.
        raise ValueError(
            f"Gemini returned no schema-valid {schema.__name__}. Raw text was: "
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

# ==========================================================================
# Task 8 — the AI Risk Analyst
# ==========================================================================


class RiskFinding(BaseModel):
    """One risk, the evidence behind it, and what *kind* of evidence it is.

    `evidence_kind` is the load-bearing field. Four kinds, deliberately
    including `general_knowledge`: a model that has to admit "this part is
    general knowledge" is a model that cannot pass a plausible-sounding
    hand-wave off as a figure this service computed. Making the admission part
    of the schema is cheaper and more reliable than asking for it in prose.
    """

    finding: str
    evidence: str
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    evidence_kind: Literal[
        "computed", "historical", "model_estimate", "general_knowledge"
    ]


class RiskAnalysis(BaseModel):
    """What `/risk-analyst` returns. Four required fields, no defaults.

    No defaults on purpose: an optional `disclaimer` is a disclaimer that gets
    left out on the run where it mattered most.
    """

    summary: str
    findings: list[RiskFinding]
    comparison_note: str
    disclaimer: str


RISK_SYSTEM_PROMPT = """You are an evidence-grounded risk analyst writing for
local disaster-management authorities in West Bengal. Rules, non-negotiable:

1. This is NOT an official warning. You are not IMD, you do not speak as IMD,
   and nothing you write may be presented as an official forecast or advisory.
   Say so in your disclaimer.
2. Use ONLY the numbers in the four blocks you are given. Never invent a
   number, a percentage, a distance, a population or a storm. If a figure is
   not in the input, do not supply one — omit the claim instead.
3. The storm-surge figure is produced by the DETERMINISTIC LAW
   1.2 x (wind/115)^2. That law is AUTHORITATIVE for surge in this service.
   The machine-learning estimate in the input is NOT the surge figure, is NOT a
   prediction, and must never be used as, or confused with, a surge value.
4. Every finding must cite the evidence it rests on, in the `evidence` field,
   and label that evidence's kind. Findings resting on general knowledge must
   say so rather than borrowing the authority of a computed figure.
5. severity must be exactly one of CRITICAL, HIGH, MEDIUM, LOW.
6. comparison_note must describe only the comparison block given to you.
"""


def build_risk_prompt(
    context,
    exposure: dict,
    comparison: dict,
    peak_estimate,
    advisory: dict | None = None,
    origin_facts: dict | None = None,
) -> str:
    """The four evidence blocks, each labelled by the kind of claim it carries.

    Blocks rather than one continuous paragraph because a single unlabelled
    stream is exactly how a reader comes to treat an ML estimate, a computed
    figure and a historical fact as the same species of statement. The order is
    fixed and the headings are the only place those words appear in capitals, so
    `tests/test_risk_analyst.py` can address each block by name.

    `context` is a `ScenarioContext` and `peak_estimate` a `StormPeakEstimate`;
    both are taken untyped so this module does not import either — it reads
    fields, and a type import would pull the simulation and the ML layer into
    the AI module's dependency graph for no runtime reason.
    """
    wind = exposure["wind_kmph"]
    surge = exposure["surge"]["surge_m"]

    lines: list[str] = []
    lines.append(
        "Assess the cyclone risk below for the locality authorities. Nothing "
        "here is a forecast, and the surge number is a screening estimate "
        "rather than a site-specific prediction."
    )
    lines.append("")
    # Restated here as well as in the system prompt. The two are not redundant:
    # a system prompt can be deprioritised against a long user turn, and these
    # are the two rules where being deprioritised is not a stylistic loss but a
    # fabricated figure or a fabricated authority. Costs three lines, removes
    # the only failure mode that would make the whole feature worse than absent.
    lines.append(
        "Rules for your answer: use ONLY the numbers in the four blocks below — "
        "never invent a number, a percentage, a distance, a population or a "
        "storm; if a figure is not there, omit the claim instead. This is NOT "
        "an official warning: you are not IMD, do not speak as IMD, and nothing "
        "you write may be presented as an official forecast."
    )
    lines.append("")
    lines.append(f"cyclone_id: {context.cyclone_id}")
    lines.append(f"scenario_id: {context.scenario_id}")
    lines.append("")

    # --- block 1 -------------------------------------------------------
    lines.append("=== COMPUTED FIGURES ===")
    lines.append(
        "Produced by this service's own simulation. The surge figure comes from "
        "the deterministic law 1.2 x (wind/115)^2, which is AUTHORITATIVE for "
        "storm surge here; it is a screening estimate scaled from one observed "
        "event (Cyclone Remal, 2024) and it omits tide, pressure, bathymetry "
        "and storm size. It is NOT the machine-learning estimate below."
    )
    lines.append(f"wind_kmph: {wind}")
    lines.append(f"surge_m: {surge:.2f}")
    lines.append(f"final_land_area_km2: {exposure['final_land_area_km2']}")
    lines.append(
        f"district-wide hospitals exposed: {exposure['hospitals']['count']}"
    )
    lines.append(
        f"district-wide substations exposed: {exposure['substations']['count']}"
    )
    lines.append(
        f"district-wide roads cut off: {exposure['roads_cut_off']['count']}"
    )
    lines.append(
        "Those three counts are DISTRICT-WIDE totals, not per locality. "
        f"IMD category: {exposure['imd_category']}."
    )
    lines.append("")

    # --- block 2 -------------------------------------------------------
    lines.append("=== MODEL ESTIMATE (ML — not the surge figure, not a prediction) ===")
    lines.append(
        "A machine-learning estimate of this storm's PEAK intensity over its "
        "lifetime, in knots. It is an ML estimate, NOT the surge figure, and it "
        "is NOT a prediction of what will happen. The deterministic surge law "
        "in the block above stays authoritative."
    )
    lines.append(f"estimate_kt: {peak_estimate.estimate_kt} kt")
    lines.append(f"estimate_source: {peak_estimate.estimate_source}")
    lines.append(f"is_a_prediction: {peak_estimate.is_a_prediction}")
    lines.append(f"beats_baseline: {peak_estimate.beats_baseline}")
    lines.append(f"n_training: {peak_estimate.n_training}")
    lines.append(
        f"baseline: a flat median of the training targets, "
        f"{peak_estimate.baseline_kt} kt"
    )
    lines.append(
        "GATE VERDICT: beats_baseline is "
        f"{peak_estimate.beats_baseline}. "
        + (
            "The model did NOT beat the flat median baseline under leave-one-out cross-validation, "
            "so the shipped estimate_source is median_baseline and is_a_prediction is False. "
            "Treat this as a labelled reference point, never as a forecast and never as a surge value."
            if not peak_estimate.beats_baseline
            else "The model beat the flat median baseline under leave-one-out cross-validation, "
            "so the shipped estimate_source is model and is_a_prediction is True. "
            "Treat this as a real estimate, and still never as the surge figure."
        )
    )
    lines.append(f"limitation: {peak_estimate.limitation}")
    lines.append("")

    # --- block 3 -------------------------------------------------------
    lines.append("=== COMPARISON ===")
    lines.append(
        "Each cyclone computed independently at the same scenario, so the "
        "deltas are between figures produced the same way. Deltas are reported "
        "for exactly two cyclones."
    )
    lines.append(f"between: {comparison['deltas']['between']}")
    for entry in comparison["cyclones"]:
        lines.append(
            f"- {entry['cyclone_id']} ({entry['name']} {entry['season']}), "
            f"scenario {entry['scenario_id']} at {entry['wind_kmph']} kmph: "
            f"surge {entry['surge_m']:.4f} m, flooded land "
            f"{entry['flood_area_km2']} km2, hospitals "
            f"{entry['hospitals_exposed']}, substations "
            f"{entry['substations_exposed']}, roads "
            f"{entry['roads_cut_off']}"
        )
    for key, value in comparison["deltas"].items():
        if key.endswith("_delta"):
            lines.append(f"{key}: {value}")
    if "note" in comparison["deltas"]:
        lines.append(f"note: {comparison['deltas']['note']}")
    lines.append("")

    # --- block 4 -------------------------------------------------------
    lines.append("=== HISTORICAL CONTEXT ===")
    lines.append(
        "The verified historical pool. Name a storm from this pool only, never "
        "any other storm:"
    )
    lines.append(VERIFIED_HISTORICAL_POOL)

    if origin_facts:
        lines.append("=== REQUESTING LOCALITY ===")
        lines.append(
            "Facts computed for the locality the request came from, from the "
            "same routing and allocation the rest of the app serves:"
        )
        for key, value in origin_facts.items():
            lines.append(f"{key}: {value}")
        lines.append("")

    if advisory:
        lines.append("=== ADVISORY ===")
        lines.append(
            "The district advisory already issued for this scenario, which your "
            "analysis must be consistent with:"
        )
        lines.append(str(advisory.get("executive_summary", "")))
        lines.append("")

    return "\n".join(lines)


def generate_risk_analysis(
    context,
    exposure: dict,
    comparison: dict,
    peak_estimate,
    advisory: dict | None = None,
    origin_facts: dict | None = None,
) -> RiskAnalysis:
    """One Gemini call, schema-validated, pinned to `ADVISORY_MODEL`.

    No fallback model and no rotation: if the pinned model is unavailable the
    call fails, because a silently different model is a silently different
    judgement, and Rules.md forbids swapping model versions without saying so.
    """
    model = ADVISORY_MODEL
    return _generate_content(
        model=model,
        prompt=build_risk_prompt(
            context=context,
            exposure=exposure,
            comparison=comparison,
            peak_estimate=peak_estimate,
            advisory=advisory,
            origin_facts=origin_facts,
        ),
        system_instruction=RISK_SYSTEM_PROMPT,
        schema=RiskAnalysis,
    )


__all__ = [
    "ADVISORY_MODEL",
    "DistrictAdvisory",
    "EvacuationPriority",
    "RISK_SYSTEM_PROMPT",
    "RiskAnalysis",
    "RiskFinding",
    "SYSTEM_PROMPT",
    "VERIFIED_HISTORICAL_POOL",
    "build_prompt",
    "build_risk_prompt",
    "generate_advisory",
    "generate_risk_analysis",
    "plan_coverage",
    "validate_advisory",
]
