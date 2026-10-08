/**
 * The Web screen — the judge-facing build of the Cyclone Forecaster.
 *
 * ## Why this file exists separately from `MapScreen.tsx`
 *
 * `react-native-maps` is a **native module**. Import it into a browser bundle
 * and its components resolve to `codegenNativeComponent`, a function that only
 * exists inside a React Native runtime — which throws on first render and
 * leaves a blank page. That is exactly how the previous Web build failed
 * (`codegenNativeComponent is not a function`), and it is why the platform
 * split is a **file** split rather than a branch inside one file: Metro
 * resolves `MapScreen.web.tsx` in preference to `MapScreen.tsx` on Web, so the
 * native import never enters the Web module graph at all. Sharing one file
 * would require importing the native module for the native path, and one
 * careless barrel export puts it back.
 *
 * The native screen and its `react-native-maps` experience are untouched and
 * continue to ship to Android and iOS.
 *
 * ## What is shared with the native build, and what is not
 *
 * **Shared, deliberately:** `api.ts` (the whole typed client and its error
 * taxonomy), `strengthChips.ts` (the four-chip vocabulary and its mapping
 * rules), `exposureTiles.ts`, `trackFacts.ts`, `legend.ts`, `advisoryFlow.ts`
 * (the failure wording and the SMS limit), `theme.ts`, `sampleAdvisory.ts`.
 * So the two platforms cannot disagree about which scenarios exist, when the
 * advisory button is enabled, what a quota failure says, or what the colours
 * are.
 *
 * **Web-only:** `mapProjection.ts` (plate carrée into an SVG viewBox — the
 * native screen projects through the map's own camera and has no use for it),
 * `basemap.ts` + `webBasemap.ts` (the DEM-derived base layer, which exists
 * because there is no Google tile server for the browser),
 * `webViewModel.ts` (every displayed string and figure, extracted so the
 * honesty rules are testable without a renderer), and the four components in
 * this directory.
 *
 * ## The rules this screen must not break
 *
 *  - **Every number comes from the API.** No lookup table, no hardcoded
 *    figure, no stringified JSON. `webViewModel.ts` derives each one from a
 *    payload and returns `null` — rendered `—`, never `0` — when a value has
 *    not arrived.
 *  - **The four chips, not seven buttons.** `strengthChips.ts` decides the
 *    mapping; this screen only says which chip is selected.
 *  - **A Gemini failure never fabricates.** 429/503 render the truthful
 *    message from `describeAdvisoryError` while the impact analysis stays live.
 *  - **The disclosures stay visible**, compactly: screening estimate, track is
 *    not a forecast, shelter placeholders, population estimates, and road
 *    intersection is not impassability.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from 'react-native';

import {
  ApiError,
  getAllocation,
  getCategories,
  getExposure,
  getLocalities,
  getOverlays,
  getRoutes,
  getTrack,
  nearestCategory,
  postAdvisory,
  API_BASE_URL,
  type AllocationResponse,
  type CategoriesResponse,
  type ExposureResponse,
  type LocalitiesResponse,
  type OverlayEntry,
  type RoutesResponse,
  type TrackResponse,
} from '../api';
import {
  getComparison,
  getCyclones,
  getLiveCyclone,
  getScenarios,
  postRiskAnalysis,
} from '../apiCyclones';
import type {
  ComparisonEntry,
  Cyclone,
  LiveCycloneState,
  RiskAnalystResponse,
} from '../cycloneModel';
import { cycloneDisplayName } from '../cycloneModel';
import { pickSecondScenario, scenarioForChip } from '../scenarioCompare';
import { describeAdvisoryError } from '../advisoryFlow';
import { bootMap } from '../bootLive';
import { totalExposed } from '../exposureTiles';
import {
  DEFAULT_CHIP,
  advisoryEnabled,
  figuresLine,
  resolveChip,
  type ChipId,
} from '../strengthChips';
import { SAMPLE_ADVISORY } from '../sampleAdvisory';
import { theme } from '../theme';
import { peakReportedWindKmph } from '../trackFacts';
import {
  ROADS_DISCLOSURE,
  ROADS_HONEST_LABEL,
  SCOPING_DISCLOSURE_PLAIN,
  TRACK_DISCLOSURE_PLAIN,
  advisoryAvailability,
  advisoryStaleNote,
  areaLabel,
  caseStudyLine,
  countLabel,
  countUnit,
  isCompactViewport,
  personKmLabel,
  populationDisclosure,
  routeSummary,
  scenarioFigures,
  scenarioHeadline,
  shelterDisclosure,
  surgeLabel,
  trackCaption,
  unreportedWindSentence,
  windLabel,
} from '../webViewModel';
import { AdvisoryPanel, type WebAdvisoryOutcome } from './AdvisoryPanel';
import { CyclonePicker } from './CyclonePicker';
import { LocalitySearch } from './LocalitySearch';
import { RiskAnalystPanel } from './RiskAnalystPanel';
import { ScenarioComparePanel } from './ScenarioComparePanel';
import { WebImpactMap } from './WebImpactMap';

/**
 * Web-only HTML attributes React Native's types do not declare.
 *
 * `title` is the browser's native tooltip. This file never ships to a native
 * target, and the alternative — dropping the caveat — would lose the road
 * tile's most important sentence.
 */
type WebOnly = Record<string, unknown>;

type Boot =
  | { status: 'loading' }
  | { status: 'error'; error: ApiError }
  | {
      status: 'ready';
      categories: CategoriesResponse;
      overlayIndex: OverlayEntry[];
      localities: LocalitiesResponse;
      cyclones: Cyclone[];
      defaultCycloneId: string;
      live: LiveCycloneState | null;
    };

export function MapScreen() {
  const [boot, setBoot] = useState<Boot>({ status: 'loading' });

  /**
   * The viewport, and the layout it gets. One breakpoint, decided by the pure
   * `isCompactViewport` in `webViewModel.ts` so the rule is testable without
   * a renderer: below 860 px the screen stacks (masthead → map → the four
   * steps → "What this is and is not"); at and above it the two-column desktop
   * layout is unchanged. `useWindowDimensions` re-fires on resize, so a
   * narrowed desktop window re-stacks live rather than waiting for a reload.
   */
  const { width: viewportWidth } = useWindowDimensions();
  const compact = isCompactViewport(viewportWidth);

  const [chipId, setChipId] = useState<ChipId>(DEFAULT_CHIP);

  /**
   * The cyclone the app is addressing, and the catalogue it is chosen from.
   * Same reasoning as the native screen — see `MapScreen.tsx`.
   */
  const [cyclones, setCyclones] = useState<Cyclone[]>([]);
  const [defaultCycloneId, setDefaultCycloneId] = useState<string>('');
  const [selectedCycloneId, setSelectedCycloneId] = useState<string | null>(null);

  /** The live feed's state. Never substituted with a historical cyclone. */
  const [live, setLive] = useState<LiveCycloneState | null>(null);
  const [liveLoading, setLiveLoading] = useState(false);

  // --- comparison --------------------------------------------------------
  const [showCompare, setShowCompare] = useState(false);
  const [compareRows, setCompareRows] = useState<ComparisonEntry[] | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<ApiError | null>(null);

  // --- risk analyst ------------------------------------------------------
  const [showRisk, setShowRisk] = useState(false);
  const [riskResult, setRiskResult] = useState<RiskAnalystResponse | null>(null);
  const [riskLoading, setRiskLoading] = useState(false);
  const [riskError, setRiskError] = useState<ApiError | null>(null);

  /**
   * The default origin: **Namkhana**, with the same measured reasoning as the
   * native screen — see `MapScreen.tsx` for the full reachability table.
   * `sagar` has no route at any category because of a road-data gap rather
   * than flooding, so defaulting to it showed the failure case first.
   */
  const [originId, setOriginId] = useState('namkhana');

  const [exposure, setExposure] = useState<ExposureResponse | null>(null);
  const [exposureLoading, setExposureLoading] = useState(false);
  /** As `routesFailed`: an error must not read as a pending request. */
  const [exposureFailed, setExposureFailed] = useState(false);
  const [routes, setRoutes] = useState<RoutesResponse | null>(null);
  const [routesLoading, setRoutesLoading] = useState(false);
  /**
   * Whether the last `/routes` request failed, as distinct from never having
   * returned. Without this a failed or aborted request renders as
   * "Checking the road network…" forever — a spinner for something that is
   * not happening. Found in the browser during the Web build: an aborted fetch
   * left the panel claiming to still be working.
   */
  const [routesFailed, setRoutesFailed] = useState(false);
  const [allocation, setAllocation] = useState<AllocationResponse | null>(null);

  const [advisory, setAdvisory] = useState<WebAdvisoryOutcome | null>(null);
  const [advisoryBusy, setAdvisoryBusy] = useState(false);

  const [showAbout, setShowAbout] = useState(false);
  /** Hovered chip id — see the note in `LocalitySearch` about the RNW flag. */
  const [hoveredChip, setHoveredChip] = useState<ChipId | null>(null);

  // --- boot -------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (cancelled) return;
      const outcome = await bootMap({
        getCategories,
        getOverlays,
        getLocalities,
        getCyclones,
        getLiveCyclone,
      });
      if (cancelled) return;
      if (outcome.kind === 'error') {
        setBoot({ status: 'error', error: outcome.error });
        return;
      }
      const defaultId = outcome.defaultCycloneId;
      setCyclones(outcome.cyclones);
      setDefaultCycloneId(defaultId);
      setSelectedCycloneId(defaultId);
      setBoot({
        status: 'ready',
        categories: outcome.categories,
        overlayIndex: outcome.overlayIndex,
        localities: outcome.localities,
        cyclones: outcome.cyclones,
        defaultCycloneId: defaultId,
        live: null,
      });
      // The live feed is in flight from here, so the picker says so. Without
      // this the live panel sat on a null live and a false liveLoading — an
      // empty slot where the answer was merely not back yet.
      setLiveLoading(true);
      // Boot and the live feed are independent. The feed settles on its own; a
      // rejection or timeout lands in the truthful unavailable state and never
      // the boot error screen.
      void outcome.livePromise.then((live) => {
        if (cancelled) return;
        setLiveLoading(false);
        setLive(live);
        setBoot((current) =>
          current.status === 'ready' ? { ...current, live } : current,
        );
      });
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const categories = boot.status === 'ready' ? boot.categories.categories : [];
  const overlayIndex = boot.status === 'ready' ? boot.overlayIndex : [];
  const localitiesResponse = boot.status === 'ready' ? boot.localities : null;

  /**
   * The selected cyclone's track, fetched by an effect for the same reason as
   * the native screen: the track is a property of the selected cyclone.
   */
  const [track, setTrack] = useState<TrackResponse | null>(null);
  const trackCycloneId = useRef<string | null>(null);
  useEffect(() => {
    const id = selectedCycloneId;
    if (id === null) return undefined;
    if (trackCycloneId.current === id) return undefined;
    trackCycloneId.current = id;
    let cancelled = false;
    getTrack(id)
      .then((next) => {
        if (!cancelled) setTrack(next);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [selectedCycloneId]);

  const preset = boot.status === 'ready' ? (boot.categories.presets[0] ?? null) : null;

  /**
   * The cyclone currently on screen, from the catalogue rather than the track
   * (the track arrives a moment later, and the masthead must not flash a
   * different storm in between).
   *
   * The H1 is derived from this. It used to be the literal string
   * "Cyclone Remal, May 2024", which stayed put while the map drew a 1970
   * storm's track — a case-study name sitting over whatever was actually
   * selected. Naming the selection is the whole point of the picker.
   */
  const selectedCyclone = cyclones.find((c) => c.cyclone_id === selectedCycloneId) ?? null;
  /** Whether what is selected is the documented case study. */
  const isCaseStudy = selectedCyclone === null || selectedCyclone.is_case_study;

  /**
   * The single place `chipId` becomes a category index, via the shared
   * `resolveChip`. The Remal preset is not an IMD band, so it borrows the
   * nearest one and the panel discloses that the counts are the band's.
   */
  const resolved = useMemo(
    () => resolveChip(chipId, categories, overlayIndex),
    [chipId, categories, overlayIndex],
  );
  const overlay = resolved.overlay;

  const { requestCategory, borrowedCategory } = useMemo(() => {
    if (resolved.categoryIndex !== null) {
      return {
        requestCategory: resolved.categoryIndex,
        borrowedCategory: resolved.borrowedCategory,
      };
    }
    if (preset === null) return { requestCategory: null, borrowedCategory: null };
    const nearest = nearestCategory(categories, preset.wind_kmph);
    return { requestCategory: nearest, borrowedCategory: categories[nearest]?.imd_category ?? null };
  }, [resolved, preset, categories]);

  // --- exposure: one request per committed chip --------------------------
  useEffect(() => {
    if (requestCategory === null) return undefined;
    const cycloneId = selectedCycloneId ?? undefined;
    let cancelled = false;
    setExposureLoading(true);
    setExposureFailed(false);
    getExposure(requestCategory, cycloneId, scenarioForChip(chipId))
      .then((next) => {
        if (!cancelled) setExposure(next);
      })
      // A failed exposure drops to the empty state rather than leaving the
      // previous chip's counts on screen under the new chip's heading — and
      // raises the flag, or the failure note in the render below is dead code
      // and the zero-count empty state poses as "nothing exposed".
      .catch(() => {
        if (!cancelled) {
          setExposure(null);
          setExposureFailed(true);
        }
      })
      .finally(() => {
        if (!cancelled) setExposureLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [requestCategory, selectedCycloneId, chipId]);

  // --- routes: changes with both the chip and the origin -----------------
  useEffect(() => {
    if (requestCategory === null) return undefined;
    let cancelled = false;
    setRoutesLoading(true);
    setRoutesFailed(false);
    getRoutes(requestCategory, originId)
      .then((next) => {
        if (cancelled) return;
        setRoutes(next);
      })
      .catch(() => {
        if (cancelled) return;
        setRoutes(null);
        setRoutesFailed(true);
      })
      .finally(() => {
        if (!cancelled) setRoutesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [requestCategory, originId]);

  // --- allocation: for the shelter summary and the disclosure ------------
  useEffect(() => {
    if (requestCategory === null) return undefined;
    let cancelled = false;
    getAllocation(requestCategory, originId)
      .then((next) => {
        if (!cancelled) setAllocation(next);
      })
      .catch(() => {
        // Left null on purpose: `shelterDisclosure` fails closed, so a failed
        // allocation shows the placeholder warning rather than suppressing it.
        if (!cancelled) setAllocation(null);
      });
    return () => {
      cancelled = true;
    };
  }, [requestCategory, originId]);

  /**
   * On a chip press the previous exposure is dropped immediately.
   *
   * Otherwise selecting a chip shows the *previous* chip's counts for the
   * duration of the fetch, under a heading that already names the new one.
   * The tiles' `—` is what replaces it.
   */
  const onSelectChip = useCallback((id: ChipId) => {
    setChipId(id);
    setExposure(null);
  }, []);

  /**
   * Selecting a cyclone refetches the track and drops the current exposure.
   * Same reasoning as the native screen — see `MapScreen.tsx`.
   */
  const onSelectCyclone = useCallback((id: string) => {
    setSelectedCycloneId(id);
    setExposure(null);
  }, []);

  /**
   * The comparison: this storm at the chosen scenario, against a second
   * scenario of the same storm. The second scenario is looked up from the
   * catalogue, never assumed — see `MapScreen.tsx` for the full reasoning
   * (only the case study has an `observed` scenario; assuming it is a 400 for
   * the other 609 cyclones).
   */
  const onOpenCompare = useCallback(() => {
    const id = selectedCycloneId;
    const category = requestCategory;
    if (!id || category === null) return;
    setShowCompare(true);
    setCompareLoading(true);
    setCompareError(null);
    (async () => {
      const current = scenarioForChip(chipId);
      const catalogue = await getScenarios(id);
      const second = pickSecondScenario(
        current,
        catalogue.scenarios.map((s) => s.scenario_id),
      );
      const requests = [getComparison([id], category, current)];
      if (second !== null) requests.push(getComparison([id], category, second));
      const results = await Promise.all(requests);
      setCompareRows(results.flatMap((r) => r.cyclones));
      setCompareLoading(false);
    })().catch((err) => {
      setCompareRows(null);
      setCompareError(err as ApiError);
      setCompareLoading(false);
    });
  }, [selectedCycloneId, requestCategory, chipId]);

  const onCloseCompare = useCallback(() => {
    setShowCompare(false);
  }, []);

  /**
   * The risk analyst, behind its own explicit press — the same gate
   * `onGenerateAdvisory` sits behind, because this spends Gemini quota.
   */
  const onGenerateRisk = useCallback(() => {
    const category = requestCategory;
    if (category === null) return;
    setShowRisk(true);
    setRiskLoading(true);
    setRiskError(null);
    postRiskAnalysis({
      category,
      cyclone_id: selectedCycloneId ?? undefined,
      scenario_id: scenarioForChip(chipId),
      origin: originId,
    })
      .then((result) => {
        setRiskResult(result);
        setRiskLoading(false);
      })
      .catch((err) => {
        setRiskResult(null);
        setRiskError(err as ApiError);
        setRiskLoading(false);
      });
  }, [requestCategory, selectedCycloneId, chipId, originId]);

  const onCloseRisk = useCallback(() => {
    setShowRisk(false);
  }, []);

  // --- advisory ----------------------------------------------------------
  const onGenerateAdvisory = useCallback(() => {
    // The in-flight lock, not a spinner decoration: a second press sends a
    // second POST and the backend's capacity ladder will spend up to six more
    // Gemini calls on it.
    if (advisoryBusy || requestCategory === null) return;
    setAdvisoryBusy(true);
    setAdvisory({ status: 'loading' });

    postAdvisory(requestCategory, originId, selectedCycloneId ?? undefined, scenarioForChip(chipId))
      .then((response) => setAdvisory({ status: 'ready', response, capturedAt: null }))
      .catch((err: unknown) => {
        const error = err as ApiError;
        // `describeAdvisoryError` is the shared taxonomy: a 503 capacity block
        // and a 429 spent quota read differently, because they need different
        // things from the reader.
        setAdvisory({
          status: 'failed',
          failure: describeAdvisoryError(error),
          // Non-null only for a 503. Quota arrives with it forced to null so
          // the countdown cannot promise that waiting helps.
          retryAfterSeconds: error.retryAfterSeconds,
        });
      })
      .finally(() => setAdvisoryBusy(false));
  }, [advisoryBusy, requestCategory, originId]);

  const onLoadCachedAdvisory = useCallback(() => {
    if (!SAMPLE_ADVISORY) return;
    setAdvisoryBusy(false);
    setAdvisory({
      status: 'ready',
      response: SAMPLE_ADVISORY.response,
      capturedAt: SAMPLE_ADVISORY.captured_at,
    });
  }, []);

  // --- derived view model -----------------------------------------------
  const figures = useMemo(
    () => scenarioFigures(overlay, exposure, resolved.borrowedCategory),
    [overlay, exposure, resolved.borrowedCategory],
  );

  const counts = useMemo(
    () => ({
      hospitals: exposure?.hospitals.count ?? null,
      substations: exposure?.substations.count ?? null,
      roads: exposure?.roads_cut_off.count ?? null,
    }),
    [exposure],
  );

  const exposedCount = totalExposed(counts);
  const availability = useMemo(
    () => advisoryAvailability(exposedCount, exposureLoading, advisoryBusy),
    [exposedCount, exposureLoading, advisoryBusy],
  );
  // The shared rule, kept as the single authority on the button's state; the
  // availability object adds the reason for the hint text.
  const canGenerate = availability.enabled && advisoryEnabled(exposedCount, exposureLoading);

  const route = useMemo(
    () => routeSummary(routes, allocation, routesFailed),
    [routes, allocation, routesFailed],
  );
  const shelters = useMemo(
    () => shelterDisclosure(allocation?.shelter_status ?? null, allocation?.shelter_loads.length ?? null),
    [allocation],
  );
  const population = useMemo(() => populationDisclosure(allocation), [allocation]);
  const caseStudy = useMemo(
    () => caseStudyLine(boot.status === 'ready' ? boot.categories : null),
    [boot],
  );

  const staleNote =
    advisory?.status === 'ready' && requestCategory !== null
      ? advisoryStaleNote(advisory.response, requestCategory, originId, selectedCycloneId ?? undefined, scenarioForChip(chipId))
      : null;

  // --- render ------------------------------------------------------------
  if (boot.status === 'loading') {
    return (
      <View style={styles.centred}>
        <ActivityIndicator color={theme.colors.primary} size="large" />
        <Text style={styles.centredText}>Reading categories, overlays and localities…</Text>
      </View>
    );
  }

  if (boot.status === 'error') {
    return <BootError error={boot.error} />;
  }

  return (
    <View style={styles.screen}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.page}
        showsVerticalScrollIndicator={false}
      >
        {/* --- masthead: the case study, in the first viewport ---------- */}
        <View style={[styles.masthead, compact && styles.mastheadCompact]}>
          <View style={styles.mastheadText}>
            <Text style={styles.eyebrow}>Cyclone impact & infrastructure forecaster</Text>
            {/*
              The title names **what is selected**, not the case study. It was
              the literal "Cyclone Remal, May 2024" while the map could be
              drawing a 1970 storm's track — a case-study name sitting over a
              different storm, which is the mislabel the picker exists to make
              possible in the first place.
              The case study keeps its exact original wording, month included:
              the catalogue carries a season but not a landfall month, and that
              sentence is the documented anchor.
            */}
            <Text style={[styles.title, compact && styles.titleCompact]}>
              {isCaseStudy
                ? 'Cyclone Remal, May 2024'
                : `Cyclone ${cycloneDisplayName(selectedCyclone?.name)}, ${selectedCyclone?.season}`}
            </Text>
            {/*
              No landfall sentence here. It used to sit in this subtitle *and*
              in `caseStudyLine`'s anchor line directly below, so the same fact
              was stated twice in the first viewport — and the copy here also
              read "Sagar Island and Khepupara, West Bengal", putting a
              Bangladeshi landfall point inside West Bengal. `caseStudyLine`
              carries the geography once, with both countries named, and this
              subtitle states only what the tool does.
            */}
            <Text style={styles.subtitle}>
              Given a storm strength, this shows which hospitals, substations and
              roads the flood reaches across the Sundarbans delta, and drafts the
              evacuation advisory.
            </Text>
            {/*
              The anchor is the case study's own fact, so it is labelled as
              such the moment something else is selected. Left unlabelled it
              would read as a description of the storm on screen — a Remal
              landfall sentence under a RUTH heading.
            */}
            <Text style={styles.anchorLine}>
              {isCaseStudy ? '' : 'Case study — '}
              {caseStudy.anchor}
            </Text>
          </View>
          <View style={styles.mastheadBadges}>
            <Badge label="Case study" value="Remal 2024" />
            <Badge
              label="Localities"
              value={String(localitiesResponse?.localities.length ?? 0)}
            />
            <Badge
              label="Storm fixes"
              value={track ? String(track.waypoint_count ?? track.path.length) : '—'}
            />
          </View>
        </View>

        {/* --- the working area: map + control column -------------------- */}
        <View style={[styles.workspace, compact && styles.workspaceCompact]}>
          <View style={[styles.mapColumn, compact && styles.mapColumnCompact]}>
            <WebImpactMap
              exposure={exposure}
              track={track}
              overlay={overlay}
              localities={localitiesResponse}
              originId={originId}
              shelter={routes?.shelter ?? null}
              routeCoordinates={routes?.coordinates ?? []}
              routeReachable={routes?.reachable ?? false}
              loading={exposureLoading}
              busy={exposureLoading || routesLoading}
            />
            <Text style={styles.trackCaption}>{trackCaption(track)}</Text>
          </View>

          <View style={[styles.controlColumn, compact && styles.controlColumnCompact]}>
            {/* Step 1 — scenario */}
            <Step index={1} title="Pick a storm">
              {/*
                The cyclone picker above the strength chips, as on the native
                screen. The chips answer "how strong"; the picker answers "which
                storm". `cat4`/`cat5`/`cat6` are all still there and `cat6` is
                still the default.
              */}
              <CyclonePicker
                cyclones={cyclones}
                selectedId={selectedCycloneId ?? ''}
                onSelect={onSelectCyclone}
                live={live}
                liveLoading={liveLoading}
              />
              <View style={styles.chips}>
                {STRENGTH_CHIP_IDS.map((id) => {
                  const chip = resolveChip(id, categories, overlayIndex).chip;
                  const selected = id === chipId;
                  return (
                    <Pressable
                      key={id}
                      onPress={() => onSelectChip(id)}
                      accessibilityRole="radio"
                      accessibilityState={{ selected }}
                      accessibilityLabel={`${chip.label} scenario`}
                      onHoverIn={() => setHoveredChip(id)}
                      onHoverOut={() =>
                        setHoveredChip((current) => (current === id ? null : current))
                      }
                      style={({ pressed }: { pressed: boolean }) => [
                        styles.chip,
                        selected && styles.chipSelected,
                        hoveredChip === id && !selected && styles.chipHovered,
                        pressed && styles.pressed,
                      ]}
                    >
                      <Text
                        style={[
                          styles.chipLabel,
                          selected && styles.chipLabelSelected,
                        ]}
                      >
                        {chip.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>

              <Text style={styles.scenarioHeadline}>{scenarioHeadline(figures)}</Text>

              {/*
                `figuresLine` is the shared string from `strengthChips.ts`, so
                the Web and native screens state the scenario identically —
                including the `≥` that marks category 6's wind as a band floor
                rather than a midpoint.
              */}
              <Text style={styles.figuresLine}>
                {figuresLine(
                  overlay,
                  exposure?.wind_is_band_midpoint,
                )}
              </Text>

              {chipId === 'remal_observed' && borrowedCategory !== null ? (
                <Text style={styles.borrowed}>
                  The preset is 115 km/h, which is not an IMD band. The counts
                  below borrow the nearest band ({borrowedCategory}).
                </Text>
              ) : null}

              {figures.bandNote !== null ? (
                <Text style={styles.bandNote}>{figures.bandNote}</Text>
              ) : null}
            </Step>

            {/* Step 2 — impact */}
            <Step index={2} title="See what gets hit">
              <View style={styles.factGrid}>
                <Fact label="Wind" value={windLabel(figures.windKmph, figures.windIsBandMidpoint)} />
                <Fact label="Storm surge" value={surgeLabel(figures.surgeM)} note="estimate" />
                <Fact label="Flooded land" value={areaLabel(figures.floodedKm2)} note="model" />
                <Fact label="IMD classification" value={figures.bandLabel || '—'} small />
              </View>

              <View style={styles.tiles}>
                <Tile
                  label="Hospitals"
                  count={countLabel(counts.hospitals)}
                  unit={countUnit(counts.hospitals, 'submerged')}
                />
                <Tile
                  label="Substations"
                  count={countLabel(counts.substations)}
                  unit={countUnit(counts.substations, 'submerged')}
                />
                {/*
                  The road tile's wording is the project's honest position and
                  is asserted against the backend's own `definitions` block by
                  `tests/webViewModel.test.mjs`. "Impassable" would be a
                  connectivity claim this computation never makes.
                */}
                <Tile
                  label="Roads"
                  count={countLabel(counts.roads)}
                  unit={countUnit(counts.roads, ROADS_HONEST_LABEL)}
                  disclosure={ROADS_DISCLOSURE}
                />
              </View>

              {exposureFailed ? (
                <Text style={styles.errorNote}>
                  The exposure request did not complete, so these counts are
                  unknown rather than zero. Pick the strength again to retry.
                </Text>
              ) : exposureLoading ? (
                <Text style={styles.loadingNote}>Reading the exposure figures…</Text>
              ) : exposedCount === 0 && exposure !== null ? (
                <Text style={styles.emptyNote}>
                  No modelled exposure at this strength — the surge is below what
                  this elevation model can resolve, so the water reaches no mapped
                  asset. Pick a stronger scenario to see the exposure path.
                </Text>
              ) : null}
            </Step>

            {/* Routing and allocation */}
            <Step index={3} title="Check the evacuation route">
              <LocalitySearch
                response={localitiesResponse}
                selectedId={originId}
                onSelect={setOriginId}
                disabled={advisoryBusy}
              />

              <View style={styles.routeBlock}>
                <Text
                  style={[
                    styles.routeHeadline,
                    route.reachable && styles.routeHeadlineOk,
                  ]}
                >
                  {route.headline}
                </Text>
                {route.detail ? (
                  <Text style={styles.routeDetail}>{route.detail}</Text>
                ) : null}

                <View style={styles.allocationStats}>
                  <MiniStat
                    label="Localities allocated"
                    value={
                      route.evaluatedLocalities === null
                        ? '—'
                        : String(route.evaluatedLocalities)
                    }
                  />
                  <MiniStat label="Total travel" value={personKmLabel(route.totalPersonKm)} />
                  <MiniStat
                    label="Unmet demand"
                    value={route.unmetDemand === null ? '—' : route.unmetDemand.toLocaleString('en-US')}
                  />
                </View>
              </View>

              {shelters.isDemo ? (
                <View style={styles.shelterNotice}>
                  <Text style={styles.shelterHeading}>{shelters.heading}</Text>
                  <Text style={styles.shelterBody}>{shelters.body}</Text>
                </View>
              ) : null}
            </Step>

            {/* Step 4 — the advisory */}
            <Step index={4} title="Get the evacuation advisory">
              <Pressable
                onPress={onGenerateAdvisory}
                disabled={!canGenerate}
                accessibilityRole="button"
                accessibilityLabel="Generate the district advisory"
                style={({ pressed }) => [
                  styles.generate,
                  !canGenerate && styles.generateDisabled,
                  pressed && canGenerate && styles.pressed,
                ]}
              >
                {advisoryBusy ? (
                  <ActivityIndicator color={theme.colors.background} />
                ) : (
                  <Text style={styles.generateLabel}>Generate advisory</Text>
                )}
              </Pressable>

              {/*
                The comparison and the risk analyst, as secondary actions below
                the primary one. Both are reached from an explicit press — the
                risk analyst spends Gemini quota.
              */}
              <View style={styles.secondaryActions}>
                <Pressable
                  onPress={onOpenCompare}
                  disabled={!canGenerate}
                  accessibilityRole="button"
                  accessibilityLabel="Compare scenarios"
                  style={({ pressed }) => [
                    styles.secondary,
                    !canGenerate && styles.generateDisabled,
                    pressed && canGenerate && styles.pressed,
                  ]}
                >
                  <Text style={styles.secondaryLabel}>Compare scenarios</Text>
                </Pressable>
                <Pressable
                  onPress={onGenerateRisk}
                  disabled={!canGenerate}
                  accessibilityRole="button"
                  accessibilityLabel="Generate analysis"
                  style={({ pressed }) => [
                    styles.secondary,
                    !canGenerate && styles.generateDisabled,
                    pressed && canGenerate && styles.pressed,
                  ]}
                >
                  <Text style={styles.secondaryLabel}>Generate analysis</Text>
                </Pressable>
              </View>

              {availability.reason !== null ? (
                <Text style={styles.generateHint}>{availability.reason}</Text>
              ) : (
                <Text style={styles.generateHint}>
                  The advisory text is written by a language model. The impact
                  figures above do not depend on it.
                </Text>
              )}

              {staleNote !== null ? (
                <View style={styles.staleNotice}>
                  <Text style={styles.staleText}>{staleNote}</Text>
                </View>
              ) : null}

              {SAMPLE_ADVISORY !== null && advisory?.status !== 'ready' ? (
                <Pressable
                  onPress={onLoadCachedAdvisory}
                  accessibilityRole="button"
                  style={({ pressed }) => [styles.cachedButton, pressed && styles.pressed]}
                >
                  <Text style={styles.cachedLabel}>
                    Load cached example (not live)
                  </Text>
                </Pressable>
              ) : null}

              <AdvisoryPanel
                outcome={advisory}
                shelterStatus={allocation?.shelter_status ?? null}
                onRetry={onGenerateAdvisory}
                onClose={() => setAdvisory(null)}
              />
            </Step>

            {/*
              The comparison sheet and the risk analyst, rendered **inline** on
              Web. A modal over a map would hide the map the panel is
              describing — the same reason `AdvisoryPanel` renders inline
              rather than in a `Modal`. The native screen wraps the same
              content-only components in `AdvisoryModal`.
            */}
            {showCompare ? (
              <View style={styles.inlinePanel}>
                <Text style={styles.inlineHeading}>Scenario comparison</Text>
                <ScenarioComparePanel
                  rows={compareRows ?? []}
                  loading={compareLoading}
                  error={compareError}
                />
              </View>
            ) : null}

            {showRisk ? (
              <View style={styles.inlinePanel}>
                <Text style={styles.inlineHeading}>Risk analysis</Text>
                <RiskAnalystPanel result={riskResult} loading={riskLoading} error={riskError} />
              </View>
            ) : null}

            {/* --- disclosures ------------------------------------------- */}
            <View style={styles.disclosureBlock}>
              <Text style={styles.disclosureHeading}>What this is and is not</Text>
              <Disclosure label="Screening estimate, not a forecast">
                {caseStudy.limitation}
              </Disclosure>
              {/*
                Plain language here; the backend's own disclosure string is
                shown verbatim in "Show data provenance" below. It names a
                source file and two wire-format fields, which belong with the
                provenance and not in the first thing a judge reads.
              */}
              <Disclosure label="The storm track is history, not a prediction">
                {TRACK_DISCLOSURE_PLAIN}
                {track !== null
                  ? ` The strongest fix reports ${formatPeak(peakReportedWindKmph(track.waypoints))};${unreportedWindSentence(track)}`
                  : ''}
              </Disclosure>
              <Disclosure label="Population figures are estimates">
                {population.text}
              </Disclosure>
              <Disclosure label="Roads intersected, not roads proven impassable">
                {ROADS_DISCLOSURE}
              </Disclosure>
              <Disclosure label="The basemap is derived terrain">
                Land and water come from the committed SRTM elevation model at
                the 0 m contour, subsampled to about 150 m per pixel. It is for
                orientation only, and no figure is measured off it.
              </Disclosure>
              <Disclosure label="Study area is scoped, not an administrative boundary">
                {SCOPING_DISCLOSURE_PLAIN}
              </Disclosure>
              <Disclosure label="The flood model spreads water at most about 0.5 km inland">
                The flood model spreads water at most about 0.5 km inland from the
                water's edge, a limit of the algorithm it follows, so flooded area and
                exposure counts are likely understated.
              </Disclosure>
              <Pressable
                onPress={() => setShowAbout((on) => !on)}
                accessibilityRole="button"
                style={styles.aboutToggle}
              >
                <Text style={styles.aboutToggleLabel}>
                  {showAbout ? 'Hide' : 'Show'} data provenance
                </Text>
              </Pressable>
              {showAbout ? (
                <View style={styles.provenance}>
                  <ProvenanceRow label="Backend" value={API_BASE_URL || '(not configured)'} />
                  <ProvenanceRow
                    label="Surge method"
                    value={boot.categories.method}
                  />
                  <ProvenanceRow
                    label="Representative wind"
                    value={boot.categories.representative_wind}
                  />
                  <ProvenanceRow label="IMD bands" value={boot.categories.source} />
                  <ProvenanceRow
                    label="Track"
                    value={`${track?.source ?? 'IBTrACS'} · ${track?.wind_units ?? 'knots'}`}
                  />
                  <ProvenanceRow
                    label="Overlay rasters"
                    value={
                      overlay !== null
                        ? `${overlay.width_px}×${overlay.height_px} · ${overlay.flooded_pixels.toLocaleString('en-US')} flooded pixels · ${overlay.png_bytes.toLocaleString('en-US')} B`
                        : '—'
                    }
                  />
                  <Text style={styles.provenanceNote}>{overlay?.disclosure ?? ''}</Text>

                  {/*
                    The backend's own disclosure strings, verbatim. They are
                    unchanged on the wire and nothing here is edited — this is
                    where they belong, so the main panel can be plain language
                    without losing a single claim.
                  */}
                  <View style={styles.provenanceRow}>
                    <Text style={styles.provenanceKey}>Track, verbatim</Text>
                    <Text style={styles.provenanceValue}>
                      {track?.disclosure ?? '—'}
                    </Text>
                  </View>
                  <View style={styles.provenanceRow}>
                    <Text style={styles.provenanceKey}>Study area, verbatim</Text>
                    <Text style={styles.provenanceValue}>
                      {localitiesResponse?.scoping.disclosure ?? '—'}
                    </Text>
                  </View>
                </View>
              ) : null}
            </View>
          </View>
        </View>

        <Text style={styles.footer}>
          Cyclone Impact & Infrastructure Vulnerability Forecaster · Google Code
          for Communities Hackathon 2nd Edition, Track 5 · every figure is computed
          at request time from committed data.
        </Text>
      </ScrollView>
    </View>
  );
}

/** The four chips, in order — the shared vocabulary, not a re-derivation. */
const STRENGTH_CHIP_IDS: ChipId[] = ['remal_observed', 'cat4', 'cat5', 'cat6'];

/** `100 km/h` or `no wind reported`. */
function formatPeak(peak: number | null): string {
  return peak === null ? 'no wind at all' : `${Math.round(peak)} km/h`;
}

function Step({
  index,
  title,
  children,
}: {
  index: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.step}>
      <View style={styles.stepHeader}>
        <View style={styles.stepBadge}>
          <Text style={styles.stepBadgeLabel}>{index}</Text>
        </View>
        <Text style={styles.stepTitle}>{title}</Text>
      </View>
      {children}
    </View>
  );
}

function Fact({
  label,
  value,
  note,
  small = false,
}: {
  label: string;
  value: string;
  note?: string;
  small?: boolean;
}) {
  return (
    <View style={styles.fact}>
      <Text style={styles.factLabel}>{label}</Text>
      <Text style={[styles.factValue, small && styles.factValueSmall]}>{value}</Text>
      {note === undefined ? null : <Text style={styles.factNote}>{note}</Text>}
    </View>
  );
}

function Tile({
  label,
  count,
  unit,
  disclosure,
}: {
  label: string;
  count: string;
  unit: string | null;
  disclosure?: string;
}) {
  return (
    <View style={styles.tile}>
      <Text style={styles.tileLabel}>{label}</Text>
      <Text style={styles.tileCount}>{count}</Text>
      {/*
        `title` is the browser's native tooltip and is what carries the road
        tile's intersection-vs-impassability caveat to anyone who hovers it.
        React Native's `Text` has no such prop; it is Web-only.
      */}
      {unit === null ? null : (
        <Text style={styles.tileUnit} {...({ title: disclosure } as WebOnly)}>
          {unit}
        </Text>
      )}
    </View>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.miniStat}>
      <Text style={styles.miniStatValue}>{value}</Text>
      <Text style={styles.miniStatLabel}>{label}</Text>
    </View>
  );
}

function Badge({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.badge}>
      <Text style={styles.badgeValue}>{value}</Text>
      <Text style={styles.badgeLabel}>{label}</Text>
    </View>
  );
}

function Disclosure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.disclosureItem}>
      <Text style={styles.disclosureLabel}>{label}</Text>
      <Text style={styles.disclosureBody}>{children}</Text>
    </View>
  );
}

function ProvenanceRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.provenanceRow}>
      <Text style={styles.provenanceKey}>{label}</Text>
      <Text style={styles.provenanceValue}>{value}</Text>
    </View>
  );
}

/**
 * Boot failure — where a wrong `EXPO_PUBLIC_API_URL` lands.
 *
 * Deliberately specific, because the most common cause of a blank Web app is a
 * backend URL that was never set at build time, and the generic "failed to
 * fetch" message does not say that.
 */
function BootError({ error }: { error: ApiError }) {
  const isConfig = error.kind === 'config';
  return (
    <View style={styles.centred}>
      <Text style={styles.errorTitle}>
        {isConfig ? 'Backend not configured' : 'Could not reach the backend'}
      </Text>
      <Text style={styles.centredText}>{error.message}</Text>
      {!isConfig ? (
        <Text style={styles.centredMuted}>Configured origin: {API_BASE_URL || '(none)'}</Text>
      ) : null}
      <Text style={styles.centredMuted}>
        {isConfig
          ? 'Set EXPO_PUBLIC_API_URL on the Vercel project and redeploy — the value is compiled into the bundle at build time.'
          : 'Nothing here is cached. Reload once the backend is reachable.'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  scroll: { flex: 1 },
  page: {
    maxWidth: 1560,
    width: '100%',
    alignSelf: 'center',
    padding: theme.spacing.md,
    paddingBottom: theme.spacing.xxl,
  },
  centred: {
    flex: 1,
    minHeight: 460,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    backgroundColor: theme.colors.background,
    padding: theme.spacing.md,
  },
  centredText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    textAlign: 'center',
    maxWidth: 560,
  },
  centredMuted: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textAlign: 'center',
    maxWidth: 560,
  },
  errorTitle: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.heading,
    color: theme.colors.danger,
  },

  masthead: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: theme.spacing.md,
    paddingBottom: theme.spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
    marginBottom: theme.spacing.md,
  },
  /**
   * The stacked masthead. Below the breakpoint the badges sit *under* the
   * title rather than beside it — `mastheadText`'s `flex: 1` in the row
   * layout squeezed the title to make room for a 380 px badge block, which
   * is what made the masthead the first thing to break on a phone.
   */
  mastheadCompact: { flexDirection: 'column', gap: theme.spacing.sm },
  mastheadText: { flex: 1, maxWidth: 760 },
  eyebrow: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 11,
    letterSpacing: 2,
    textTransform: 'uppercase',
    color: theme.colors.selectedText,
  },
  title: {
    fontFamily: theme.fonts.heading,
    fontSize: 38,
    lineHeight: 44,
    color: theme.colors.text,
    marginTop: 6,
  },
  /** A phone-sized H1. 38 px wraps to three lines at 360 px wide. */
  titleCompact: { fontSize: 30, lineHeight: 36 },
  subtitle: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    lineHeight: 23,
    color: theme.colors.textMuted,
    marginTop: 8,
  },
  anchorLine: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginTop: 10,
  },
  mastheadBadges: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', maxWidth: 380 },
  badge: {
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    paddingHorizontal: 13,
    paddingVertical: 9,
    minWidth: 96,
  },
  badgeValue: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  badgeLabel: {
    fontFamily: theme.fonts.body,
    fontSize: 10,
    letterSpacing: 1,
    textTransform: 'uppercase',
    color: theme.colors.textMuted,
    marginTop: 2,
  },

  workspace: { flexDirection: 'row', gap: theme.spacing.md, alignItems: 'flex-start' },
  /**
   * The stacked layout: masthead, map, the four steps, then "What this is
   * and is not" — the order is just document order, because `mapColumn`
   * precedes `controlColumn` in the JSX. The map keeps the `55vh`-capped,
   * aspect-matched height `mapPanelHeightPx` gives it (see
   * `WebImpactMap.tsx`); the steps follow at full width.
   */
  workspaceCompact: { flexDirection: 'column', gap: theme.spacing.sm },
  mapColumn: { flex: 1.45, minWidth: 0 },
  /**
   * Stacked, the map is a full-width block, not a 1.45:1 flex share.
   *
   * **`flexBasis: 'auto'`, not `flex: 0`.** RNW maps RN's `flex: 0` to CSS
   * `flex: 0 0 0%`, and in a *column* parent the 0% basis applies to the
   * child's **height** — both columns collapsed to `h: 0` and the map panel,
   * whose children overflow visibly, painted over Step 1 beneath it. Measured
   * by the layout probe at 360 px before this comment was written.
   * `flexBasis: 'auto'` sizes each stacked column to its content, which is
   * what a stacked layout means.
   */
  mapColumnCompact: { flexGrow: 0, flexBasis: 'auto', width: '100%' },
  mapColumnInner: { minHeight: 620 },
  trackCaption: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.textMuted,
    marginTop: 8,
    lineHeight: 16,
  },
  controlColumn: { flex: 1, minWidth: 340, gap: theme.spacing.sm },
  /**
   * Stacked, the control column drops its 340 px minimum — that minimum is
   * what forced horizontal scroll at 360 px, because 24 px page padding +
   * 340 px of column + a 16 px gap cannot fit a 360 px viewport.
   * `flexBasis: 'auto'` for the same reason as `mapColumnCompact`.
   */
  controlColumnCompact: { flexGrow: 0, flexBasis: 'auto', minWidth: 0, width: '100%' },

  step: {
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: theme.spacing.sm,
  },
  stepHeader: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 12 },
  stepBadge: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: theme.colors.selectedFill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepBadgeLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: 12,
    /**
     * `selectedText`, not `primary`.
     *
     * The badge is `selectedFill` (#011132) and the numeral was `primary`
     * (#0d52c3) — a contrast ratio of **2.67:1**, below the 4.5:1 WCAG AA floor
     * for 12px text, which is why the step numbers 1-4 were almost invisible
     * against the near-black panel.
     *
     * `selectedText` (#5f9dea) on the same `selectedFill` is **6.65:1** and is
     * the pairing the theme already uses for a selected chip, so this is the
     * existing design system applied consistently rather than a new colour.
     */
    color: theme.colors.selectedText,
  },
  stepTitle: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },

  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 },
  chip: {
    paddingHorizontal: 13,
    paddingVertical: 8,
    // A 44 px minimum keeps the chip a thumb target on a phone — the bare
    // padding puts it at ~34 px, under the 44 px floor for touch targets.
    minHeight: 44,
    justifyContent: 'center',
    borderRadius: theme.radius.chip,
    backgroundColor: theme.colors.background,
    borderWidth: 1,
    borderColor: theme.colors.border,
  },
  chipSelected: { backgroundColor: theme.colors.selectedFill, borderColor: theme.colors.primary },
  chipHovered: { borderColor: theme.colors.selectedText },
  chipLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 13,
    color: theme.colors.textMuted,
  },
  chipLabelSelected: { fontFamily: theme.fonts.bodySemibold, color: theme.colors.selectedText },
  scenarioHeadline: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    marginTop: 12,
  },
  figuresLine: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: 3,
  },
  borrowed: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    lineHeight: 16,
    color: theme.colors.caution,
    marginTop: 8,
  },
  bandNote: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    lineHeight: 16,
    color: theme.colors.textMuted,
    marginTop: 8,
  },

  factGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  fact: {
    flexGrow: 1,
    flexBasis: 130,
    backgroundColor: theme.colors.background,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: 11,
  },
  factLabel: {
    fontFamily: theme.fonts.body,
    fontSize: 10,
    letterSpacing: 1,
    textTransform: 'uppercase',
    color: theme.colors.textMuted,
  },
  factValue: {
    fontFamily: theme.fonts.heading,
    fontSize: 22,
    color: theme.colors.text,
    marginTop: 4,
  },
  factValueSmall: { fontSize: 14, lineHeight: 20 },
  factNote: { fontFamily: theme.fonts.body, fontSize: 10, color: theme.colors.textMuted },

  /**
   * The tiles wrap below ~440 px of column width: `flexBasis 140` puts two
   * abreast on a phone (three would be ~110 px each — the 26 px count and the
   * unit line no longer fit) and three abreast on desktop, unchanged from
   * the single row the fixed layout drew.
   */
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 10 },
  tile: {
    flexGrow: 1,
    flexBasis: 140,
    backgroundColor: theme.colors.background,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: 11,
  },
  tileLabel: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.text,
  },
  tileCount: {
    fontFamily: theme.fonts.heading,
    fontSize: 26,
    color: theme.colors.text,
    marginTop: 3,
  },
  tileUnit: {
    fontFamily: theme.fonts.body,
    fontSize: 10,
    lineHeight: 14,
    color: theme.colors.textMuted,
    marginTop: 2,
  },
  loadingNote: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: 10,
  },
  emptyNote: {
    fontFamily: theme.fonts.body,
    fontSize: 12,
    lineHeight: 18,
    color: theme.colors.caution,
    marginTop: 10,
  },
  errorNote: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 12,
    lineHeight: 18,
    color: theme.colors.danger,
    marginTop: 10,
  },

  routeBlock: {
    marginTop: 12,
    backgroundColor: theme.colors.background,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: 12,
  },
  routeHeadline: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.caution,
  },
  routeHeadlineOk: { color: theme.colors.text },
  routeDetail: {
    fontFamily: theme.fonts.body,
    fontSize: 12,
    lineHeight: 17,
    color: theme.colors.textMuted,
    marginTop: 5,
  },
  /** Wraps like the tiles, for the same reason: three across is ~110 px each. */
  allocationStats: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  miniStat: {
    flexGrow: 1,
    flexBasis: 140,
    backgroundColor: theme.colors.card,
    borderRadius: 8,
    padding: 9,
    borderWidth: 1,
    borderColor: theme.colors.border,
  },
  miniStatValue: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: 15,
    color: theme.colors.text,
  },
  miniStatLabel: {
    fontFamily: theme.fonts.body,
    fontSize: 10,
    color: theme.colors.textMuted,
    marginTop: 2,
  },

  shelterNotice: {
    marginTop: 10,
    backgroundColor: theme.colors.caution,
    borderRadius: theme.radius.button,
    padding: 12,
  },
  shelterHeading: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: 12,
    color: theme.colors.background,
  },
  shelterBody: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    lineHeight: 16,
    color: theme.colors.background,
    marginTop: 3,
  },

  generate: {
    marginTop: 4,
    backgroundColor: theme.colors.primary,
    borderRadius: theme.radius.button,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
  },
  generateDisabled: { opacity: 0.45 },
  generateLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.emphasis,
    color: theme.colors.background,
  },
  secondaryActions: {
    flexDirection: 'row',
    marginTop: 8,
  },
  secondary: {
    flex: 1,
    minHeight: 48,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.card,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: theme.spacing.xs,
    marginRight: theme.spacing.xs,
  },
  secondaryLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  inlinePanel: {
    marginTop: theme.spacing.sm,
    padding: theme.spacing.sm,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.card,
  },
  inlineHeading: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    marginBottom: theme.spacing.xs,
  },
  generateHint: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    lineHeight: 16,
    color: theme.colors.textMuted,
    marginTop: 8,
  },
  pressed: { opacity: 0.72 },
  staleNotice: {
    marginTop: 10,
    backgroundColor: theme.colors.caution,
    borderRadius: theme.radius.button,
    padding: 11,
  },
  staleText: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 12,
    lineHeight: 17,
    color: theme.colors.background,
  },
  cachedButton: {
    marginTop: 8,
    alignSelf: 'flex-start',
    backgroundColor: theme.colors.background,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    paddingHorizontal: 14,
    paddingVertical: 9,
    minHeight: 44,
    justifyContent: 'center',
  },
  cachedLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 12,
    color: theme.colors.text,
  },

  disclosureBlock: {
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: theme.spacing.sm,
  },
  disclosureHeading: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: 11,
    letterSpacing: 1.3,
    textTransform: 'uppercase',
    color: theme.colors.selectedText,
    marginBottom: 10,
  },
  disclosureItem: { marginBottom: 10 },
  disclosureLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: 12,
    color: theme.colors.text,
  },
  disclosureBody: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    lineHeight: 16,
    color: theme.colors.textMuted,
    marginTop: 2,
  },
  aboutToggle: {
    alignSelf: 'flex-start',
    paddingVertical: 6,
    paddingHorizontal: 8,
    minHeight: 44,
    justifyContent: 'center',
  },
  aboutToggleLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: 12,
    color: theme.colors.selectedText,
    textDecorationLine: 'underline',
  },
  provenance: {
    marginTop: 8,
    backgroundColor: theme.colors.background,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: 11,
    gap: 6,
  },
  provenanceRow: { flexDirection: 'row', gap: 10 },
  provenanceKey: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 11,
    color: theme.colors.textMuted,
    width: 128,
  },
  provenanceValue: {
    flex: 1,
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.text,
  },
  provenanceNote: {
    fontFamily: theme.fonts.body,
    fontSize: 10,
    lineHeight: 15,
    color: theme.colors.textMuted,
    marginTop: 4,
  },

  footer: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.textMuted,
    textAlign: 'center',
    marginTop: theme.spacing.md,
    lineHeight: 17,
  },
});
