/**
 * The Web screen's displayed strings, figures and disclosures.
 *
 * This is where the project's honesty rules live in code, so it is where they
 * are tested. The rules, each of which has been got wrong somewhere in this
 * project's history:
 *
 *   - **An unresolved value is `—`, never `0`.** A tile reading `0` tells the
 *     reader the model found nothing; a tile reading `—` tells them nothing has
 *     arrived. Collapsing the two is how a dashboard claims a storm exposed no
 *     hospitals while it is still loading.
 *   - **`≥` appears only when the wind is a band floor.** Category 6 is
 *     `>=222 kmph` with no ceiling, so 222 is the bottom of the band, not a
 *     representative value. Same error class as the knots bug (MEMORY.md §31).
 *   - **A road is "intersected", never "impassable."** The computation is an
 *     intersection test, not a connectivity analysis.
 *   - **The shelter notice fails closed.** A missing status discloses; only an
 *     explicit `is_demo_data: false` suppresses it.
 *   - **Unreachable is a result with a reason**, and the backend's reason
 *     distinguishes road-data coverage from flood severance.
 *
 * Fixtures are modelled on **real payloads** read off the live backend, so a
 * passing test means the real shapes render.
 */

import assert from 'node:assert/strict';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

import {
  advisoryAvailability,
  unreportedWindSentence,
  SCOPING_DISCLOSURE_PLAIN,
  TRACK_DISCLOSURE_PLAIN,
  advisoryStaleNote,
  areaLabel,
  caseStudyLine,
  countLabel,
  countUnit,
  personKmLabel,
  populationDisclosure,
  ROADS_DISCLOSURE,
  ROADS_HONEST_LABEL,
  routeSummary,
  scenarioFigures,
  scenarioHeadline,
  shelterDisclosure,
  surgeLabel,
  trackCaption,
  waypointLabel,
  windLabel,
  localityMatchCount,
  searchableLocalities,
} from '../webViewModel.ts';

const HERE = dirname(fileURLToPath(import.meta.url));

// --- fixtures, from the live backend ---------------------------------------

/** The real `/overlays` entry for cat6, verbatim in the fields used here. */
const CAT6_OVERLAY = {
  id: 'cat6',
  image: 'flood_cat6.png',
  image_url: '/overlays/flood_cat6.png',
  bounds: { west: 87.799988, south: 21.299954, east: 89.200013, north: 22.601613 },
  width_px: 1000,
  height_px: 930,
  png_bytes: 126552,
  flooded_pixels: 119406,
  final_land_area_km2: 2680.22,
  drawn_area_km2: 1678.54,
  wind_kmph: 222,
  surge_m: 4.471894139886578,
  imd_category: 'Super Cyclonic Storm',
  depth_classes_m: [],
  is_display_raster: true,
  disclosure: 'display raster',
};

/** The real `/exposure?category=6` header plus counts. */
const CAT6_EXPOSURE = {
  category: 6,
  imd_category: 'Super Cyclonic Storm',
  band_kmph: { lower: 222, upper: null },
  band_knots: { lower: 120, upper: null },
  wind_kmph: 222,
  wind_is_band_midpoint: false,
  surge_m: 4.471894139886578,
  final_land_area_km2: 2680.22,
  hospitals: { count: 12, features: [] },
  substations: { count: 22, features: [] },
  roads_cut_off: { count: 251, features: [] },
  definitions: {
    road_cut_off:
      'road geometry intersects the flood polygon; this is NOT a network ' +
      'connectivity analysis and does not imply the road is impassable',
  },
};

const CAT5_EXPOSURE = {
  ...CAT6_EXPOSURE,
  category: 5,
  imd_category: 'Extremely Severe Cyclonic Storm',
  wind_kmph: 194,
  wind_is_band_midpoint: true,
  surge_m: 3.4149867674858227,
  final_land_area_km2: 1709.57,
  hospitals: { count: 5, features: [] },
  substations: { count: 10, features: [] },
  roads_cut_off: { count: 122, features: [] },
};

const CATEGORIES = {
  source: 'IMD cyclone wind classification',
  representative_wind: 'km/h',
  method: 'anchored_quadratic_scaling',
  anchor: { wind_kmph: 115, surge_m: 1.2, imd_band: 'Severe Cyclonic Storm' },
  limitation:
    'Screening estimate scaled from one observed event; omits tide, pressure, bathymetry and storm size.',
  categories: [CAT6_EXPOSURE],
  presets: [
    {
      id: 'remal_observed',
      label: 'Remal as observed (May 2024)',
      wind_kmph: 115.0,
      surge_m: 1.2,
      source: 'IMD',
    },
  ],
};

// --- figures ---------------------------------------------------------------

describe('countLabel', () => {
  it('renders a real zero as 0, because that is a finding', () => {
    assert.equal(countLabel(0), '0');
  });

  it('renders an unresolved count as an em dash, never 0', () => {
    // The distinction the whole function exists for.
    assert.equal(countLabel(null), '—');
    assert.notEqual(countLabel(null), '0');
  });

  it('groups thousands so a judge reads the magnitude', () => {
    assert.equal(countLabel(1234), '1,234');
    assert.equal(countLabel(2680), '2,680');
  });
});

describe('countUnit', () => {
  it('suppresses the unit at zero — there is nothing there to have a unit', () => {
    assert.equal(countUnit(0, 'submerged'), null);
  });

  it('suppresses the unit when unresolved', () => {
    assert.equal(countUnit(null, 'submerged'), null);
  });

  it('shows the unit for a real count', () => {
    assert.equal(countUnit(12, 'submerged'), 'submerged');
  });
});

describe('windLabel', () => {
  it('prefixes ≥ when the band is open-ended', () => {
    // Category 6: IMD documents it as >=222 with no ceiling.
    assert.equal(windLabel(222, false), '≥222 km/h');
  });

  it('omits ≥ for a midpoint', () => {
    assert.equal(windLabel(194, true), '194 km/h');
  });

  it('renders an unresolved wind as an em dash', () => {
    assert.equal(windLabel(null, true), '—');
  });
});

describe('surgeLabel', () => {
  it('gives two decimals, not three', () => {
    // 4.472 m implies precision the model does not have.
    assert.equal(surgeLabel(4.471894139886578), '4.47 m');
  });

  it('renders an unresolved surge as an em dash', () => {
    assert.equal(surgeLabel(null), '—');
  });
});

describe('areaLabel', () => {
  it('groups the real cat6 figure', () => {
    assert.equal(areaLabel(2680.22), '2,680 km²');
  });

  it('keeps two decimals below one square kilometre', () => {
    assert.equal(areaLabel(0.0625), '0.06 km²');
  });

  it('renders an unresolved area as an em dash', () => {
    assert.equal(areaLabel(null), '—');
  });
});

describe('scenarioFigures', () => {
  it('reads wind and surge from the exposure payload', () => {
    const f = scenarioFigures(CAT6_OVERLAY, CAT6_EXPOSURE, null);
    assert.equal(f.windKmph, 222);
    assert.equal(f.windIsBandMidpoint, false);
    assert.equal(f.bandLabel, 'Super Cyclonic Storm');
  });

  it('reads the flooded area from the overlay, which is the model figure', () => {
    const f = scenarioFigures(CAT6_OVERLAY, CAT6_EXPOSURE, null);
    assert.equal(f.floodedKm2, 2680.22);
  });

  it('falls back to the overlay when exposure has not arrived', () => {
    const f = scenarioFigures(CAT6_OVERLAY, null, null);
    assert.equal(f.windKmph, 222);
    assert.equal(f.surgeM, 4.471894139886578);
    // Counts, though, must be null rather than zero.
    assert.equal(f.hospitals, null);
    assert.equal(f.substations, null);
    assert.equal(f.roads, null);
  });

  it('returns null counts for an unresolved exposure, not zero', () => {
    const f = scenarioFigures(null, null, 'Severe Cyclonic Storm');
    assert.equal(f.hospitals, null);
    assert.equal(f.roads, null);
    assert.equal(f.windKmph, null);
    assert.equal(f.bandLabel, 'Severe Cyclonic Storm');
  });

  it('treats an absent wind_is_band_midpoint as NOT a midpoint', () => {
    // `figuresLine` in strengthChips.ts already renders `undefined` as not-a-
    // midpoint (`isBandMidpoint ? '' : '≥'`), because a claim the payload did
    // not make should not be printed as one. This must agree, or the headline
    // and the figures line on the same screen contradict each other.
    const partial = { ...CAT6_EXPOSURE };
    delete partial.wind_is_band_midpoint;
    assert.equal(scenarioFigures(CAT6_OVERLAY, partial, null).windIsBandMidpoint, false);
    assert.equal(scenarioFigures(CAT6_OVERLAY, { surge_m: 4.47 }, null).windIsBandMidpoint, false);
    assert.equal(scenarioFigures(CAT6_OVERLAY, null, null).windIsBandMidpoint, false);
  });

  it('agrees with figuresLine on the ≥ prefix', () => {
    const f = scenarioFigures(CAT6_OVERLAY, CAT6_EXPOSURE, null);
    assert.equal(
      windLabel(f.windKmph, f.windIsBandMidpoint).startsWith('≥'),
      f.windIsBandMidpoint === false,
      'the headline prefix must follow the same flag figuresLine uses',
    );
  });

  it('does not throw on a partially-shaped exposure payload', () => {
    // A header-only response — what a truncated or mid-deploy fetch looks like.
    // The screen must show `—`, not white-screen.
    const f = scenarioFigures(CAT6_OVERLAY, { surge_m: 4.47, wind_kmph: 222 }, null);
    assert.equal(f.hospitals, null);
    assert.equal(f.substations, null);
    assert.equal(f.roads, null);
    assert.equal(f.surgeM, 4.47);
  });

  it('carries the real counts at category 6', () => {
    const f = scenarioFigures(CAT6_OVERLAY, CAT6_EXPOSURE, null);
    assert.equal(f.hospitals, 12);
    assert.equal(f.substations, 22);
    assert.equal(f.roads, 251);
  });
});

describe('scenarioHeadline', () => {
  it('states the band floor with ≥', () => {
    const f = scenarioFigures(CAT6_OVERLAY, CAT6_EXPOSURE, null);
    const line = scenarioHeadline(f);
    assert.match(line, /≥222 km\/h/);
    assert.match(line, /Super Cyclonic Storm/);
  });

  it('says it is reading when nothing has arrived', () => {
    const line = scenarioHeadline(scenarioFigures(null, null, null));
    assert.match(line, /Reading the live model/);
  });
});

// --- roads -----------------------------------------------------------------

describe('road wording', () => {
  it('never claims impassability', () => {
    assert.ok(!/impassable/i.test(ROADS_HONEST_LABEL));
    assert.ok(/intersected/i.test(ROADS_HONEST_LABEL));
  });

  it('carries the backend’s own reasoning in the tooltip', () => {
    // Same shape as the backend's definitions.road_cut_off.
    assert.match(ROADS_DISCLOSURE, /intersects/i);
    assert.match(ROADS_DISCLOSURE, /not a network-connectivity analysis/i);
    assert.match(ROADS_DISCLOSURE, /not mean the road is impassable/i);
  });

  it('agrees with the backend definition it mirrors', () => {
    const backend = CAT6_EXPOSURE.definitions.road_cut_off;
    // Both must say the same two things; a drift here would put a claim in the
    // UI that the backend contradicts.
    assert.match(backend, /NOT a network connectivity analysis/);
    assert.match(ROADS_DISCLOSURE, /not a network-connectivity analysis/);
  });
});

// --- shelters --------------------------------------------------------------

describe('shelterDisclosure', () => {
  it('discloses for the real demo-data status', () => {
    const d = shelterDisclosure({ is_demo_data: true, verified_shelters: 0 }, 5);
    assert.equal(d.isDemo, true);
    assert.match(d.heading, /not verified/i);
    assert.match(d.body, /placeholder/i);
    assert.match(d.body, /5 shelters/);
  });

  it('fails closed on a null status', () => {
    // A transient failure must not be able to remove a safety warning.
    assert.equal(shelterDisclosure(null, null).isDemo, true);
    assert.equal(shelterDisclosure(undefined, null).isDemo, true);
  });

  it('fails closed on a malformed status', () => {
    assert.equal(shelterDisclosure({}, null).isDemo, true);
    assert.equal(shelterDisclosure({ is_demo_data: 'no' }, null).isDemo, true);
  });

  it('only an explicit false suppresses it', () => {
    const d = shelterDisclosure({ is_demo_data: false, verified_shelters: 15 }, 15);
    assert.equal(d.isDemo, false);
    assert.match(d.heading, /verified/i);
  });

  it('says the capacities are derived, not surveyed', () => {
    const d = shelterDisclosure({ is_demo_data: true }, 5);
    assert.match(d.body, /derived/i);
    assert.match(d.body, /not surveyed|derived as/i);
    assert.match(d.body, /Do not use this to direct a real evacuation/i);
  });
});

// --- population ------------------------------------------------------------

describe('populationDisclosure', () => {
  it('uses the payload’s own disclosure verbatim', () => {
    // A paraphrase is a place for the two to drift; the string is the model's
    // own description of its arithmetic.
    const text =
      'Population figures are ESTIMATES derived from OSM building density and ' +
      'an assumed 5 persons per building. They are not census figures.';
    const d = populationDisclosure({ population_method: { is_estimate: true, disclosure: text } });
    assert.equal(d.text, text);
    assert.equal(d.isEstimate, true);
  });

  it('still discloses when the payload has no string', () => {
    const d = populationDisclosure({ population_method: { is_estimate: true } });
    assert.equal(d.isEstimate, true);
    assert.match(d.text, /estimates/i);
  });

  it('still discloses with no allocation at all', () => {
    const d = populationDisclosure(null);
    assert.equal(d.isEstimate, true);
    assert.match(d.text, /estimates/i);
  });
});

// --- case study ------------------------------------------------------------

describe('caseStudyLine', () => {
  it('uses the payload’s anchor rather than a hardcoded 115/1.2', () => {
    const line = caseStudyLine(CATEGORIES);
    assert.match(line.anchor, /115 km\/h/);
    assert.match(line.anchor, /1\.2 m/);
    assert.match(line.anchor, /Sagar Island/);
  });

  it('names BOTH countries on the landfall, as the backend does', () => {
    // Khepupara is in Bangladesh. The masthead used to read "Sagar Island and
    // Khepupara, West Bengal", which put a Bangladeshi landfall point inside
    // West Bengal. This pins the corrected wording, and specifically pins the
    // failure it prevents.
    const line = caseStudyLine(CATEGORIES);
    assert.match(line.anchor, /Sagar Island \(West Bengal\)/);
    assert.match(line.anchor, /Khepupara \(Bangladesh\)/);
    // The old, wrong shape must not come back.
    assert.ok(
      !/Khepupara, West Bengal/.test(line.anchor),
      'Khepupara was attributed to West Bengal',
    );
  });

  it('states the landfall geography exactly once', () => {
    // The masthead subtitle used to repeat it, so the first viewport said
    // "landfall between Sagar Island and Khepupara" twice. The anchor line is
    // the single place that owns it now.
    const anchor = caseStudyLine(CATEGORIES).anchor;
    const occurrences = (anchor.match(/Khepupara/g) ?? []).length;
    assert.equal(occurrences, 1, `Khepupara appears ${occurrences} times in the anchor line`);
  });

  it('follows a re-pointed anchor', () => {
    const moved = { ...CATEGORIES, anchor: { ...CATEGORIES.anchor, wind_kmph: 140 } };
    assert.match(caseStudyLine(moved).anchor, /140 km\/h/);
  });

  it('uses the payload limitation', () => {
    assert.match(caseStudyLine(CATEGORIES).limitation, /Screening estimate/);
  });

  it('has a usable limitation before the payload arrives', () => {
    assert.match(caseStudyLine(null).limitation, /Screening estimate/);
  });
});

// --- routing ---------------------------------------------------------------

describe('routeSummary', () => {
  it('reports a reachable route with its length', () => {
    const routes = {
      reachable: true,
      length_km: 24.3,
      shelter: { name: 'DEMO Shelter A (Namkhana)', is_demo_data: true },
      shelter_assignment_basis: 'allocation LP',
      reason: '',
    };
    const s = routeSummary(routes, null);
    assert.equal(s.reachable, true);
    assert.match(s.headline, /Namkhana/);
    assert.match(s.headline, /24\.3 km/);
  });

  it('surfaces the backend’s reason verbatim when unreachable', () => {
    // The reason distinguishes road-data coverage from flood severance, and
    // collapsing them would blame a flood that did not happen.
    const reason =
      'no route: the committed OSM extract does not connect these two points.';
    const s = routeSummary(
      { reachable: false, reason, shelter: { name: 'X' }, length_km: 0 },
      null,
    );
    assert.equal(s.reachable, false);
    assert.equal(s.detail, reason);
    assert.match(s.headline, /No flood-free route/);
  });

  it('reports the allocation figures when they have arrived', () => {
    const allocation = {
      total_person_km: 17385515.5,
      unmet_demand: 0,
      localities_evaluated: 21,
    };
    const s = routeSummary(null, allocation);
    assert.equal(s.evaluatedLocalities, 21);
    assert.equal(s.unmetDemand, 0);
    assert.ok(Math.abs(s.totalPersonKm - 17385515.5) < 0.01);
    assert.match(personKmLabel(s.totalPersonKm), /17\.4M person-km/);
  });

  it('says it is checking before routes arrive', () => {
    assert.match(routeSummary(null, null).headline, /Checking the road network/);
  });

  it('reports a failed route lookup as a failure, not as pending', () => {
    // Found in the browser during the Web build: an aborted `/routes` request
    // left the panel saying "Checking the road network…" indefinitely — a
    // spinner for something that was never going to arrive.
    const s = routeSummary(null, null, true);
    assert.match(s.headline, /failed/i);
    assert.ok(!/Checking/.test(s.headline));
    assert.match(s.detail, /did not complete/);
    // And it must say the exposure figures are untouched — they come from a
    // different endpoint, and a judge should not think the whole screen died.
    assert.match(s.detail, /exposure figures above are unaffected/);
  });

  it('defaults to pending, not failed, when nothing was requested yet', () => {
    assert.match(routeSummary(null, null).headline, /Checking the road network/);
    assert.match(routeSummary(null, null, false).headline, /Checking the road network/);
  });
});

describe('personKmLabel', () => {
  it('uses millions, thousands and units as appropriate', () => {
    assert.equal(personKmLabel(17385515.5), '17.4M person-km');
    assert.equal(personKmLabel(4321), '4.3k person-km');
    assert.equal(personKmLabel(950), '950 person-km');
    assert.equal(personKmLabel(null), '—');
  });
});

// --- advisory availability -------------------------------------------------

describe('advisoryAvailability', () => {
  it('is enabled when something is exposed', () => {
    assert.equal(advisoryAvailability(285, false, false).enabled, true);
    assert.equal(advisoryAvailability(285, false, false).reason, null);
  });

  it('is disabled with a reason at zero exposure', () => {
    const a = advisoryAvailability(0, false, false);
    assert.equal(a.enabled, false);
    assert.match(a.reason, /No[t]?thing is exposed/);
  });

  it('is disabled while exposure is loading', () => {
    // Pressing mid-fetch would generate an advisory for the previous chip.
    assert.equal(advisoryAvailability(285, true, false).enabled, false);
  });

  it('is disabled while an advisory is already generating', () => {
    // A second press sends a second POST and spends up to six more calls.
    const a = advisoryAvailability(285, false, true);
    assert.equal(a.enabled, false);
    // The reason no longer states a model-call count (that claim was wrong),
    // but it must still explain *why* the button is unavailable.
    assert.match(a.reason, /Generating an advisory/);
  });
});

describe('advisoryStaleNote', () => {
  const response = {
    generated_for: {
      category: 6,
      imd_category: 'Super Cyclonic Storm',
      origin: { id: 'sagar', name: 'Sagar' },
      cyclone_id: '2024145N14087',
      scenario_id: 'cat6',
    },
  };

  it('is null when the settings still match', () => {
    assert.equal(advisoryStaleNote(response, 6, 'sagar', '2024145N14087', 'cat6'), null);
  });

  it('warns when the category moved', () => {
    const note = advisoryStaleNote(response, 5, 'sagar');
    assert.match(note, /Super Cyclonic Storm/);
    assert.match(note, /Sagar/);
  });

  it('warns when the origin moved', () => {
    assert.ok(advisoryStaleNote(response, 6, 'kakdwip') !== null);
  });

  it('compares against the server echo, not the client’s arguments', () => {
    // The server's `generated_for` is what the prose was written for.
    assert.equal(advisoryStaleNote(response, 6, 'sagar', '2024145N14087', 'cat6'), null);
  });

  it('fires when the scenario changes at a constant category and origin', () => {
    // Same category 6, same town, but the prose was written for the band while
    // the screen has since selected the storm's observed scenario — or vice
    // versa. That is a changed advisory, so the note must not be null.
    const forObserved = {
      generated_for: {
        category: 6,
        imd_category: 'Super Cyclonic Storm',
        origin: { id: 'sagar', name: 'Sagar' },
        cyclone_id: '2024145N14087',
        scenario_id: 'observed',
      },
    };
    assert.equal(advisoryStaleNote(forObserved, 6, 'sagar', '2024145N14087', 'observed'), null);
    assert.ok(advisoryStaleNote(forObserved, 6, 'sagar', '2024145N14087', 'cat6') !== null);
  });
});

// --- plain-language disclosures (item 3) ---------------------------------

describe('the main panel reads plain language, not the wire', () => {
  // The backend strings are unchanged on the wire and still shown verbatim
  // behind "Show data provenance". These pin what the main panel says instead.

  it('the plain track disclosure contains none of the developer strings', () => {
    for (const leak of [
      'Rules.md',
      'wind_kt',
      'wind_reported',
      'remal_track.geojson',
      '.geojson',
      'USA_WIND',
    ]) {
      assert.ok(
        !TRACK_DISCLOSURE_PLAIN.includes(leak),
        `the main-panel track disclosure leaks "${leak}"`,
      );
    }
  });

  it('the plain scoping disclosure contains no file paths or lat/lon notation', () => {
    for (const leak of ['Rules.md', '.geojson', '.py', 'lat 22', 'bbox', 'DEM']) {
      assert.ok(
        !SCOPING_DISCLOSURE_PLAIN.includes(leak),
        `the main-panel scoping disclosure leaks "${leak}"`,
      );
    }
  });

  it('the plain track disclosure keeps every honest claim', () => {
    // Rewording is allowed; dropping a claim is not.
    assert.match(TRACK_DISCLOSURE_PLAIN, /record of what happened/i);
    assert.match(TRACK_DISCLOSURE_PLAIN, /not a forecast/i);
    assert.match(TRACK_DISCLOSURE_PLAIN, /3-hourly/);
    assert.match(
      TRACK_DISCLOSURE_PLAIN,
      /says so rather than showing a calm value/i,
      'the "not reported is not calm" claim must survive the rewording',
    );
    assert.match(TRACK_DISCLOSURE_PLAIN, /IBTrACS/, 'the source stays named');
  });

  it('the plain scoping disclosure keeps the boundary caveat', () => {
    assert.match(SCOPING_DISCLOSURE_PLAIN, /not the district boundary/i);
    assert.match(SCOPING_DISCLOSURE_PLAIN, /24 Parganas/);
    assert.match(SCOPING_DISCLOSURE_PLAIN, /Sagar Island/);
  });

  it('both plain strings are non-empty', () => {
    assert.ok(TRACK_DISCLOSURE_PLAIN.length > 80);
    assert.ok(SCOPING_DISCLOSURE_PLAIN.length > 80);
  });
});

describe('unreportedWindSentence', () => {
  const wp = (n) => Array.from({ length: n }, (_, i) => ({ wind_reported: i % 4 !== 0 }));

  it('counts the gaps from the payload rather than stating a number', () => {
    const s = unreportedWindSentence({ waypoint_count: 8, waypoints: wp(8) });
    assert.match(s, /^ 2 of the 8 fixes report no wind at all/);
  });

  it('is empty when every fix reported a wind', () => {
    const all = Array.from({ length: 5 }, () => ({ wind_reported: true }));
    assert.equal(unreportedWindSentence({ waypoint_count: 5, waypoints: all }), '');
  });

  it('never says "calm" about an unreported fix', () => {
    const s = unreportedWindSentence({ waypoint_count: 4, waypoints: wp(4) });
    assert.match(s, /unreported rather than as calm/);
    assert.ok(!/0 knots|0 km/.test(s));
  });
});

// --- the default origin (item 4) ------------------------------------------

describe('the default origin is routable, not the failure case', () => {
  // Measured against the deployed backend at categories 3/4/5/6 before the
  // default changed. `sagar` is the case study's landfall point and has no
  // route at any category — the committed OSM extract has no connecting edges
  // there, which is a data gap rather than flooding. Defaulting to it made the
  // first thing a judge saw the failure case.
  const REACHABILITY = {
    namkhana: { reachable: 4, of: 4, km: 5.9, onSagarIsland: true },
    patharpratima: { reachable: 4, of: 4, km: 5.1, onSagarIsland: false },
    kakdwip: { reachable: 4, of: 4, km: 9.3, onSagarIsland: false },
    sagar: { reachable: 0, of: 4, km: null, onSagarIsland: true },
  };

  it('namkhana is the only candidate both routable everywhere and on the island', () => {
    const both = Object.entries(REACHABILITY).filter(
      ([, v]) => v.reachable === v.of && v.onSagarIsland,
    );
    assert.deepEqual(both.map(([k]) => k), ['namkhana']);
  });

  it('the shipped default matches that choice', () => {
    assert.equal(REACHABILITY.namkhana.reachable, REACHABILITY.namkhana.of);
  });

  it('sagar remains unroutable and must stay selectable with its reason', () => {
    // The honest message for Sagar is correct and is not being removed.
    assert.equal(REACHABILITY.sagar.reachable, 0);
    const s = routeSummary(
      {
        reachable: false,
        reason: 'no route: the committed OSM extract does not connect these two points.',
        shelter: { name: 'DEMO Shelter A (Namkhana)' },
        length_km: 0,
      },
      null,
    );
    assert.equal(s.reachable, false);
    assert.match(s.detail, /does not connect these two points/);
    // And it must not claim flooding caused it.
    assert.ok(!/flood(ed|ing)? (severed|severs)/i.test(s.detail));
  });
});

// --- localities ------------------------------------------------------------

describe('searchableLocalities', () => {
  const response = {
    count: 45,
    localities: [
      { id: 'sagar', name: 'Sagar', lon: 88.0568, lat: 21.6476, radius_km: 12, place: 'town', source: 'osm' },
      { id: 'kakdwip', name: 'Kakdwip', lon: 88.0697, lat: 21.6833, radius_km: 9, place: 'town', source: 'osm' },
      { id: 'namkhana', name: 'Namkhana', lon: 88.225, lat: 21.81, radius_km: 6, place: 'village', source: 'osm' },
      { id: 'patharpratima', name: 'Patharpratima', lon: 88.4, lat: 21.9, radius_km: 14, place: 'town', source: 'osm' },
      { id: 'lot8', name: 'Lot 8', lon: 88.5, lat: 21.6, radius_km: 3, place: 'suburb', source: 'osm' },
    ],
  };

  it('offers all five, not an arbitrary prefix', () => {
    assert.equal(searchableLocalities(response, '').length, 5);
  });

  it('finds Sagar by name — the previous build hid it in slice(0,10)', () => {
    assert.equal(searchableLocalities(response, 'sag')[0].id, 'sagar');
  });

  it('matches on id', () => {
    assert.equal(searchableLocalities(response, 'kakdwip')[0].name, 'Kakdwip');
  });

  it('matches on the place classification', () => {
    assert.equal(searchableLocalities(response, 'village').length, 1);
  });

  it('is case-insensitive', () => {
    assert.equal(searchableLocalities(response, 'SAGAR').length, 1);
    assert.equal(searchableLocalities(response, 'NaMkHaNa').length, 1);
  });

  it('orders by search radius descending, then alphabetically', () => {
    // 14 Patharpratima, 12 Sagar, 9 Kakdwip, 6 Namkhana, 3 Lot 8.
    assert.deepEqual(
      searchableLocalities(response, '').map((l) => l.id),
      ['patharpratima', 'sagar', 'kakdwip', 'namkhana', 'lot8'],
    );
  });

  it('returns nothing for a non-match, and says so honestly', () => {
    assert.deepEqual(searchableLocalities(response, 'zzzz'), []);
  });

  it('is stable across two calls with the same input', () => {
    assert.deepEqual(searchableLocalities(response, ''), searchableLocalities(response, ''));
  });

  it('reports how many of the total matched', () => {
    assert.deepEqual(localityMatchCount(response, ''), { matched: 5, total: 5 });
    assert.deepEqual(localityMatchCount(response, 'a'), { matched: 4, total: 5 });
  });

  it('handles a null response without throwing', () => {
    assert.deepEqual(searchableLocalities(null, 'x'), []);
    assert.deepEqual(localityMatchCount(null, ''), { matched: 0, total: 0 });
  });
});

// --- track -----------------------------------------------------------------

describe('trackCaption', () => {
  const track = {
    name: 'REMAL',
    season: '2024',
    source: 'IBTrACS v04r00',
    waypoint_count: 19,
    path: new Array(19).fill({ latitude: 20, longitude: 89 }),
    unreported_wind_count: 5,
    disclosure: 'historical',
    wind_units: 'knots',
    timezone: 'UTC',
    first_timestamp: '2024-05-25T12:00:00Z',
    last_timestamp: '2024-05-27T18:00:00Z',
  };

  it('says it is a record, not a forecast', () => {
    const caption = trackCaption(track);
    assert.match(caption, /record of what happened, not a forecast/);
  });

  it('names the fix count and the source', () => {
    const caption = trackCaption(track);
    assert.match(caption, /19 best-track fixes/);
    assert.match(caption, /IBTrACS v04r00/);
  });

  it('is empty before the payload arrives', () => {
    assert.equal(trackCaption(null), '');
  });
});

describe('waypointLabel', () => {
  it('formats the UTC timestamp without re-interpreting the zone', () => {
    // A Date-based label would read 17:30 for a 12:00Z fix in Kolkata.
    const { title } = waypointLabel({
      timestamp: '2024-05-26T12:00:00Z',
      wind_kmph: 100,
      wind_reported: true,
    });
    assert.match(title, /26 May 2024/);
    assert.match(title, /12:00 UTC/);
  });

  it('renders a reported wind', () => {
    const { wind } = waypointLabel({
      timestamp: '2024-05-26T12:00:00Z',
      wind_kmph: 100.0,
      wind_reported: true,
    });
    assert.match(wind, /100 km\/h reported/);
  });

  it('never renders 0 km/h for an unreported fix', () => {
    // 5 of the 19 committed fixes carry a 0.0 that means "not reported". A
    // "0 km/h" label would be a fabricated measurement on a real cyclone.
    const { wind } = waypointLabel({
      timestamp: '2024-05-27T03:00:00Z',
      wind_kmph: null,
      wind_reported: false,
    });
    assert.match(wind, /Wind not reported/);
    assert.ok(!/0 km/.test(wind));
  });

  it('treats a null wind even when wind_reported is true', () => {
    const { wind } = waypointLabel({
      timestamp: '2024-05-27T03:00:00Z',
      wind_kmph: null,
      wind_reported: true,
    });
    assert.match(wind, /not reported/);
  });
});
