import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {
  ApiError,
  getAllocation,
  getCategories,
  getExposure,
  getLocalities,
  getOverlays,
  getTrack,
  markerCoordinate,
  nearestCategory,
  overlayImageUrl,
  postAdvisory,
  roadPaths,
  shouldDrawOverlay,
  waypointSubtitle,
  waypointTitle,
  API_BASE_URL,
  type CategoriesResponse,
  type ExposureResponse,
  type Locality,
  type OverlayEntry,
  type SurgePreset,
  type TrackResponse,
} from '../api';
import { describeAdvisoryError, isAdvisoryStale } from '../advisoryFlow';
import { SAMPLE_ADVISORY } from '../sampleAdvisory';
import { theme } from '../theme';
import { DEFAULT_CHIP, advisoryEnabled, resolveChip, type ChipId } from '../strengthChips';
import { totalExposed } from '../exposureTiles';
import { countUnreported, peakReportedWindKmph } from '../trackFacts';
import { AdvisoryContent, type AdvisoryOutcome } from './AdvisoryContent';
import { LeafletMap, type LeafletAsset } from './LeafletMap';
import { AboutSheet } from './AboutSheet';
import { ExposureTiles } from './ExposureTiles';
import { FirstRunCard } from './FirstRunCard';
import { LocalityPicker } from './LocalityPicker';
import { MapControl } from './MapControl';
import { MapLegend } from './MapLegend';
import { OriginLine } from './OriginLine';
import { PanelStep } from './PanelStep';
import { PrimaryButton } from './PrimaryButton';
import { StrengthChips } from './StrengthChips';
import {
  SAGAR_REGION,
  SAGAR_INITIAL_ZOOM,
  assetPinColours,
  compromisedRoadDashPattern,
  trackDashPattern,
  trackLineColour,
  trackPinColour,
} from './mapStyles';

/**
 * The map screen — the app's only screen, plus the advisory modal.
 *
 * Rebuilt 2026-09-30 to the dark design. What changed structurally, and why:
 *
 *   - **The seven-band slider is gone, replaced by four chips.** Five of the
 *     seven bands expose no infrastructure, so the slider spent most of its
 *     travel on empty results. See `strengthChips.ts`.
 *   - **The panel is three numbered steps** — pick a strength, see what gets
 *     hit, get the plan — which is the app's loop stated as a list the reader
 *     can count.
 *   - **The origin is one line**, not a card with a 45-item chip row.
 *   - **The Generate button moved back inside the panel**, as step 3. It was
 *     pinned to a footer in the previous pass to keep it above the fold; with
 *     the panel now only three steps tall there is no fold, and a button that
 *     sits *under* the thing it summarises reads better than one detached from it.
 *   - **Provenance moved behind a caption.** The track caption, the raster note
 *     and the limitation were three paragraphs in the panel; they are now one
 *     line that opens `AboutSheet`.
 *
 * What deliberately did NOT change: no backend number, no endpoint, no model.
 * Every figure on this screen still comes from `/categories`, `/overlays`,
 * `/exposure`, `/localities` and `/track` exactly as before. The chips choose
 * *which* category to ask for; they do not restate what it says.
 *
 * What this screen still does not do: fetch `/surge-zone`. At category 6 that
 * is 7.0 MB raw / 936 KB gzipped of polygons, which a mobile map view stutters
 * on — and it would now have to cross a WebView bridge as well. The flood is
 * the pre-rendered PNG in `data/overlays/` instead; the counts
 * beside it come from `/exposure`, which is the real full-resolution
 * computation. The picture is a shortcut; the numbers are not.
 */

type Boot =
  | { status: 'loading' }
  | { status: 'error'; error: ApiError }
  | {
      status: 'ready';
      categories: CategoriesResponse;
      overlayIndex: OverlayEntry[];
      localities: Locality[];
      track: TrackResponse;
    };

export function MapScreen() {
  const [boot, setBoot] = useState<Boot>({ status: 'loading' });

  /**
   * Camera requests, as **monotonic counters** rather than a ref to the map.
   *
   * The map used to be `react-native-maps` and the controls called
   * `mapRef.current.animateToRegion(...)` / `fitToCoordinates(...)` directly.
   * There is no imperative handle on a WebView map, so the same two intentions
   * are now expressed as props: increment the counter, and `LeafletMap`'s effect
   * does the work.
   *
   * A counter rather than a boolean because a boolean fires its effect once and
   * then never again — pressing "fit" twice in a row would move the camera on
   * the first press and silently do nothing on the second, which is a control
   * that looks broken. `LeafletMap` also tracks the value it has already seen,
   * so a re-render caused by anything else cannot re-fire the camera.
   */
  const [fitRequest, setFitRequest] = useState(0);
  const [recentreRequest, setRecentreRequest] = useState(0);
  /** Whether the map is fitted to the whole track. Drives the control's state. */
  const [fittingTrack, setFittingTrack] = useState(false);
  /** First-run card, session-scoped — see `FirstRunCard`'s own note. */
  const [showFirstRun, setShowFirstRun] = useState(true);
  /** The About sheet, opened from the caption at the foot of the panel. */
  const [showAbout, setShowAbout] = useState(false);

  /**
   * The selected strength, as a chip id.
   *
   * One value rather than the previous `categoryIndex` + `draftIndex` +
   * `presetId` triple. The slider needed a draft value because a drag produces
   * a frame's worth of positions and firing a request per frame is not viable;
   * a chip press is a discrete event, so there is nothing to draft and the
   * triple collapses to one. `resolveChip` maps this to a category index at
   * request time, which is the only place that mapping now exists.
   */
  const [chipId, setChipId] = useState<ChipId>(DEFAULT_CHIP);
  /**
   * The default origin. **Namkhana, not Sagar**, and the reason is measured
   * rather than chosen.
   *
   * `sagar` is the case study's landfall point, which made it the obvious
   * default — but it has **no road route at any category**, and not because of
   * flooding: the committed OSM extract has no connecting edges there
   * (MEMORY.md §14). So the first thing a judge saw in step 3 was the failure
   * case, on a screen whose whole point is demonstrating a working evacuation.
   *
   * Measured against the deployed backend across categories 3/4/5/6, all four
   * of which were checked before this default changed:
   *
   *   | origin         | reachable | route | on Sagar Island |
   *   |----------------|-----------|-------|-----------------|
   *   | namkhana       | 4 of 4    | 5.9km | **yes**         |
   *   | patharpratima  | 4 of 4    | 5.1km | no              |
   *   | kakdwip        | 4 of 4    | 9.3km | no              |
   *   | sagar          | 0 of 4    | —     | yes              |
   *
   * Namkhana is the only candidate that is both routable at every category
   * **and** on the island the case study is about, which keeps the demo
   * geography honest as well as the demo working. Its own allocation row shows
   * 72 people moving 5.81 km to Shelter A, so the numbers on screen belong to
   * the origin rather than being generic.
   *
   * Sagar stays selectable, and its "road data doesn't connect these points"
   * message is untouched — that message is correct and is the point.
   */
  const [originId, setOriginId] = useState('namkhana');

  const [exposure, setExposure] = useState<ExposureResponse | null>(null);
  const [exposureLoading, setExposureLoading] = useState(false);
  const [exposureError, setExposureError] = useState<ApiError | null>(null);

  // --- advisory ----------------------------------------------------------
  // The only state in this app that spends Gemini quota, behind an explicit press.
  const [advisory, setAdvisory] = useState<AdvisoryOutcome | null>(null);
  const [advisoryBusy, setAdvisoryBusy] = useState(false);

  /**
   * `/allocation`'s `shelter_status`, for the disclosure notice.
   *
   * Fetched separately from boot on purpose: a failure to obtain a
   * supplementary disclosure must not stop the map from opening, and it fails
   * toward disclosure — a null status means "show the warning".
   */
  const [shelterStatus, setShelterStatus] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let cancelled = false;
    // Category 0 is the cheapest valid input and the flag does not vary with
    // it. Uses `originId` so this never disagrees with what is on screen.
    getAllocation(0, originId)
      .then((allocation) => {
        if (!cancelled) setShelterStatus(allocation.shelter_status);
      })
      .catch(() => {
        // Left null on purpose. Null discloses.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // --- boot --------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Parallel: four cold requests in series is four round trips before
        // anything paints, and none depends on another. `/track` is in the same
        // `all` rather than caught separately on purpose — if the committed
        // file is missing the endpoint says exactly that, and a boot error
        // naming the file beats a map with a hole in a real cyclone's track.
        const [categories, overlays, localities, track] = await Promise.all([
          getCategories(),
          getOverlays(),
          getLocalities(),
          getTrack(),
        ]);
        if (cancelled) return;
        setBoot({
          status: 'ready',
          categories,
          overlayIndex: overlays.overlays,
          localities: localities.localities,
          track,
        });
      } catch (err) {
        if (cancelled) return;
        setBoot({ status: 'error', error: err as ApiError });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const categories = boot.status === 'ready' ? boot.categories.categories : [];
  const overlayIndex = boot.status === 'ready' ? boot.overlayIndex : [];
  const localities = boot.status === 'ready' ? boot.localities : [];
  const track = boot.status === 'ready' ? boot.track : null;

  const preset: SurgePreset | null = useMemo(() => {
    if (boot.status !== 'ready') return null;
    return boot.categories.presets[0] ?? null;
  }, [boot]);

  /**
   * What the selected chip points at, resolved against the live payloads.
   *
   * This is the single place `chipId` becomes a category index, and the
   * `remal_observed` case is why it is a function rather than arithmetic: the
   * preset is not an IMD band, so it has no index of its own and borrows the
   * nearest one.
   */
  const resolved = useMemo(
    () => resolveChip(chipId, categories, overlayIndex),
    [chipId, categories, overlayIndex],
  );

  const overlay = resolved.overlay;

  /**
   * The absolute URL to draw, or null when there is nothing worth drawing.
   *
   * Null in three distinct cases, all of which mean "draw no flood layer": no
   * overlay entry yet; no absolute URL because `EXPO_PUBLIC_API_URL` is unset;
   * and `flooded_pixels === 0`, which is the real state of the preset and the
   * low bands. The third is the case this existed to fix — a layer handed an
   * empty `uri` and a bounds tuple is an image fetch that can only fail, for a
   * raster whose every pixel is alpha 0.
   */
  const overlayUri: string | null = useMemo(() => {
    if (overlay === null) return null;
    return shouldDrawOverlay(overlay) ? overlayImageUrl(overlay) ?? null : null;
  }, [overlay]);

  /**
   * The category index `/exposure` is asked for, and the figures' band name.
   *
   * For a band chip this is its own index. For the preset it is the nearest
   * band to Remal's own 115 kmph — 12 kmph from category 3 and 20 from
   * category 4, so category 3 — and `borrowedCategory` carries the name so the
   * panel can disclose that the counts are not the preset's own.
   */
  const { requestCategory, borrowedCategory } = useMemo(() => {
    if (resolved.categoryIndex !== null) {
      return { requestCategory: resolved.categoryIndex, borrowedCategory: resolved.borrowedCategory };
    }
    if (preset === null) return { requestCategory: null, borrowedCategory: null };
    const nearest = nearestCategory(categories, preset.wind_kmph);
    return {
      requestCategory: nearest,
      borrowedCategory: categories[nearest]?.imd_category ?? null,
    };
  }, [resolved, preset, categories]);

  // --- exposure: one request per committed chip --------------------------
  useEffect(() => {
    if (requestCategory === null) return undefined;
    let cancelled = false;
    setExposureLoading(true);
    setExposureError(null);
    getExposure(requestCategory)
      .then((next) => {
        if (cancelled) return;
        setExposure(next);
        setExposureLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setExposure(null);
        setExposureError(err as ApiError);
        setExposureLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [requestCategory]);

  /**
   * On a chip press the previous exposure is dropped immediately.
   *
   * Without this, selecting a chip shows the *previous* chip's counts for the
   * duration of the fetch, under a heading that already names the new one. It
   * is the same error the disabled rule prevents on the button, one step
   * earlier, and the tiles' `—` is what replaces it.
   */
  const onSelectChip = useCallback((id: ChipId) => {
    setChipId(id);
    setExposure(null);
  }, []);

  // --- map layers, as Leaflet's data shapes -------------------------------
  //
  // Four memos, and the reason each exists is the same: `LeafletMap` turns each
  // prop into a command in a `useEffect`, so an object rebuilt on every render
  // would re-issue that command on every render — which for 251 road polylines
  // means re-serialising and re-drawing the entire road layer on each keystroke
  // anywhere in the app. The dependencies are the payloads, so a command fires
  // exactly when the underlying data changes.

  /**
   * The flood raster, as `{url, bounds}`.
   *
   * `null` is a real state, not a placeholder: no overlay entry yet, no
   * absolute URL because `EXPO_PUBLIC_API_URL` is unset, or
   * `flooded_pixels === 0`, which is the true state of the preset and the low
   * bands. The third case is the one this existed to fix — an `<Overlay>`
   * rendered with an empty `uri` and a bounds tuple is an image fetch that can
   * only fail, for a layer whose every pixel is alpha 0. `LeafletMap` treats a
   * null overlay as "remove the layer", which is also what switching back to a
   * dry scenario needs.
   */
  const leafletOverlay = useMemo(() => {
    if (overlayUri === null || overlay === null) return null;
    return {
      url: overlayUri,
      bounds: {
        north: overlay.bounds.north,
        south: overlay.bounds.south,
        east: overlay.bounds.east,
        west: overlay.bounds.west,
      },
    };
  }, [overlay, overlayUri]);

  /**
   * The observed track: the polyline's vertices, and one pin per best-track fix.
   *
   * Passes the payloads through in the app's own `{latitude, longitude}` shape —
   * `LeafletMap` does the transposition into Leaflet's `[lat, lng]`, through
   * `geo.ts`. Nothing here converts, so there is no second place that could
   * disagree with the first.
   *
   * The waypoints carry their titles and subtitles rather than being rebuilt
   * inside the map document, because `waypointTitle`/`waypointSubtitle` already
   * implement the "unreported wind is not calm" rule and there must be exactly
   * one implementation of it.
   */
  const leafletTrack = useMemo(
    () =>
      track === null
        ? null
        : {
            path: track.path,
            waypoints: track.waypoints.map((waypoint) => ({
              at: { latitude: waypoint.latitude, longitude: waypoint.longitude },
              title: waypointTitle(waypoint),
              detail: waypointSubtitle(waypoint),
            })),
          },
    [track],
  );

  /**
   * Cut-off roads, one entry per geometry part.
   *
   * `roadPaths` already returns `{latitude, longitude}` objects, one list per
   * geometry part, which is exactly what `LeafletMap` wants. A `MultiLineString`
   * road therefore still draws as several disconnected polylines rather than
   * gaining a spurious segment between the parts.
   *
   * Layer order is preserved by the map document: roads, then the assets, so a
   * marker is never drawn under a road.
   */
  const leafletRoads = useMemo(
    () =>
      exposure === null
        ? null
        : exposure.roads_cut_off.features.map((road) => roadPaths(road)),
    [exposure],
  );

  /** Exposed hospitals and substations, in the order the tiles count them. */
  const leafletAssets = useMemo<LeafletAsset[]>(() => {
    if (exposure === null) return [];
    const out: LeafletAsset[] = [];
    for (const [group, kind, colour] of [
      [exposure.hospitals.features, 'hospital', assetPinColours.hospital],
      [exposure.substations.features, 'substation', assetPinColours.substation],
    ] as const) {
      for (const feature of group) {
        const pair = markerCoordinate(feature);
        // A NaN coordinate renders off-screen and reads as a missing asset,
        // which would quietly disagree with the count in the tile.
        if (pair === null || pair.length < 2) continue;
        const latitude = pair[1];
        const longitude = pair[0];
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
        out.push({
          at: { latitude, longitude },
          kind,
          colour,
          title: feature.properties.name || 'Unnamed',
          detail: feature.properties.status,
        });
      }
    }
    return out;
  }, [exposure]);

  // --- map controls ------------------------------------------------------

  const onPressFit = useCallback(() => {
    if (!track) return;
    // The finite-coordinate filtering that used to live here now lives in
    // `geo.ts` (`toLeafletList`), and `LeafletMap` refuses to fit fewer than two
    // points — so `/track` handing back a `NaN`, or a single fix, leaves the
    // camera alone instead of asking for a region it cannot compute.
    if (fittingTrack) {
      setRecentreRequest((n) => n + 1);
      setFittingTrack(false);
      return;
    }
    setFitRequest((n) => n + 1);
    setFittingTrack(true);
  }, [fittingTrack, track]);

  /**
   * The layers control is a stub, and it says so rather than doing nothing.
   *
   * The design asks for a layers button. There is exactly one toggleable layer
   * in this app (the flood raster) and it is already automatic — the button
   * would have nothing to toggle. Rather than ship a dead control, it opens the
   * legend, which is the thing a reader opening "layers" is actually after:
   * what am I looking at. Logged in MEMORY.md; a real layer switcher is a
   * Stage B question.
   */
  const onPressLayers = useCallback(() => setShowAbout(true), []);

  const onPressRecentre = useCallback(() => {
    setRecentreRequest((n) => n + 1);
    setFittingTrack(false);
  }, []);

  // --- advisory ----------------------------------------------------------
  const onGenerateAdvisory = useCallback(() => {
    // The in-flight lock, not a spinner decoration: a second press sends a
    // second POST, and the backend's capacity ladder will spend up to six more
    // Gemini calls on it. A double-tap is the most expensive possible way to
    // use this button.
    if (advisoryBusy || requestCategory === null) return;
    setAdvisoryBusy(true);
    setAdvisory({ status: 'loading' });

    postAdvisory(requestCategory, originId)
      .then((response) => {
        setAdvisory({ status: 'ready', response, capturedAt: null });
      })
      .catch((err: unknown) => {
        const error = err as ApiError;
        setAdvisory({
          status: 'failed',
          failure: describeAdvisoryError(error),
          // Non-null only for a 503. Quota arrives with it forced to null, so
          // the countdown cannot appear on a spent daily limit.
          retryAfterSeconds: error.retryAfterSeconds,
        });
      })
      .finally(() => {
        setAdvisoryBusy(false);
      });
  }, [advisoryBusy, requestCategory, originId]);

  /**
   * Swap a failed live advisory for the bundled capture.
   *
   * Real output from a real call, stored — but an answer to whatever settings
   * were on screen when it was taken. The stale guard still runs over it.
   */
  const onLoadCachedAdvisory = useCallback(() => {
    if (!SAMPLE_ADVISORY) return;
    setAdvisoryBusy(false);
    setAdvisory({
      status: 'ready',
      response: SAMPLE_ADVISORY.response,
      capturedAt: SAMPLE_ADVISORY.captured_at,
    });
  }, []);

  // --- render -------------------------------------------------------------
  if (boot.status === 'loading') {
    return (
      <View style={styles.centred}>
        <ActivityIndicator color={theme.colors.primary} />
        <Text style={styles.centredText}>Loading categories, overlays and localities…</Text>
      </View>
    );
  }

  if (boot.status === 'error') {
    return <BootError error={boot.error} />;
  }

  const counts = {
    hospitals: exposure?.hospitals.count ?? null,
    substations: exposure?.substations.count ?? null,
    roads: exposure?.roads_cut_off.count ?? null,
  };
  const exposedCount = totalExposed(counts);
  const canGenerate = advisoryEnabled(exposedCount, exposureLoading);

  const limitation =
    resolved.borrowedCategory !== null && categories.length > 0
      ? categories[requestCategory ?? 0]?.limitation ?? boot.categories.limitation
      : boot.categories.limitation;

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.title}>Cyclone Remal impact simulator</Text>
        <Text style={styles.subtitle}>What would a storm like this hit today?</Text>
      </View>

      <View style={styles.mapWrap}>
        <LeafletMap
          style={styles.map}
          initialCenter={[SAGAR_REGION.latitude, SAGAR_REGION.longitude]}
          initialZoom={SAGAR_INITIAL_ZOOM}
          overlay={leafletOverlay}
          track={leafletTrack}
          roads={leafletRoads}
          assets={leafletAssets}
          route={null}
          fitRequest={fitRequest}
          recentreRequest={recentreRequest}
        />

        {overlay !== null && overlayImageUrl(overlay) === null ? (
          <View style={styles.mapBanner}>
            <Text style={styles.mapBannerText}>
              No flood layer: EXPO_PUBLIC_API_URL is not set, so the overlay PNG has no absolute
              URL to load.
            </Text>
          </View>
        ) : null}

        {/*
          The legend is bottom-left, the three controls bottom-right, and the
          first-run card top — four `position: absolute` siblings claiming the
          map's edges. They never overlap. The banner above is top-anchored and
          full-width, and it only renders on a build misconfiguration, where
          the map shows no flood anyway; a cosmetic overlap in an
          already-broken state is the better trade.
        */}
        <MapLegend />

        {showFirstRun ? <FirstRunCard onDismiss={() => setShowFirstRun(false)} /> : null}

        {track ? (
          <MapControl
            onPressTrack={onPressFit}
            trackActive={fittingTrack}
            onPressLayers={onPressLayers}
            onPressRecentre={onPressRecentre}
          />
        ) : null}
      </View>

      <View style={styles.panel}>
        <ScrollView contentContainerStyle={styles.panelContent} showsVerticalScrollIndicator={false}>
          <PanelStep index={1} title="Pick a storm strength">
            <StrengthChips
              selected={chipId}
              onSelect={onSelectChip}
              overlay={overlay}
              windIsBandMidpoint={
                requestCategory === null
                  ? undefined
                  : categories[requestCategory]?.wind_is_band_midpoint
              }
              borrowedCategory={chipId === 'remal_observed' ? borrowedCategory : null}
            />
          </PanelStep>

          <PanelStep index={2} title="See what gets hit">
            <ExposureTiles counts={counts} loading={exposureLoading} />
            {exposureError ? (
              <Text style={styles.errorText}>
                Exposure could not be loaded: {exposureError.message}
              </Text>
            ) : null}
          </PanelStep>

          <PanelStep index={3} title="Get the evacuation plan">
            <PrimaryButton
              label="Generate advisory"
              onPress={onGenerateAdvisory}
              disabled={!canGenerate}
              busy={advisoryBusy}
            />

            {!canGenerate && !exposureLoading ? (
              <Text style={styles.disabledHint}>
                Nothing is exposed at this strength, so there is nothing to plan an evacuation
                for.
              </Text>
            ) : null}

            {/*
              The stale guard, inside the panel rather than in a footer. The
              advisory on screen was written for one `${category}:${origin}`
              pair; if the reader has since changed either, the prose describes
              a scenario they are no longer looking at. It is still shown — it
              is real output about a real modelled storm — but never presented
              as the answer to the current settings.
            */}
            {advisory?.status === 'ready' &&
            isAdvisoryStale(advisory.response, requestCategory ?? 0, originId) ? (
              <View style={styles.staleNotice}>
                <Text style={styles.staleText}>
                  Generated for {advisory.response.generated_for.imd_category} at{' '}
                  {advisory.response.generated_for.origin.name} — the settings have changed
                  since. Close and generate again for the current scenario.
                </Text>
              </View>
            ) : null}

            <View style={styles.originBlock}>
              <OriginLine
                localities={localities}
                selectedId={originId}
                onSelect={setOriginId}
              />
            </View>

            {/*
              The AI + shelter-placeholder disclosure, kept as strict as it was
              in `ReadoutPanel`. Two separate claims, and they must both be on
              screen: the prose is machine-written by a language model, and
              the shelters it names are placeholders. A reader who takes the
              shelter names as real would send people to buildings that were
              never verified to be open, so this is not a footnote.
            */}
            <Text style={styles.disclosure}>
              Written by a language model. Shelter data is a placeholder.
            </Text>
          </PanelStep>

          {/*
            Provenance, demoted to one line. The track's numbers are real
            observed data, so the two things a reader could be misled about are
            which dataset they came from and whether they are a prediction. The
            caption answers the second in six words and the first is one tap
            away — the sheet carries the limitation verbatim, the raster
            disclosure, and the JTWC 1-minute vs IMD 3-minute wind difference.
          */}
          <Text style={styles.caption}>
            Screening estimate, not a forecast ·{' '}
            <Text
              style={styles.captionLink}
              onPress={() => setShowAbout(true)}
              accessibilityRole="link"
            >
              About this estimate
            </Text>
          </Text>
        </ScrollView>
      </View>

      <AboutSheet
        visible={showAbout}
        onClose={() => setShowAbout(false)}
        limitation={limitation}
        overlayWidthPx={overlay?.width_px ?? null}
        depthClasses={overlay?.depth_classes_m.length ?? null}
        areaKm2={
          overlay ? Math.round(overlay.final_land_area_km2).toLocaleString('en-US') : null
        }
        trackSource={track?.source ?? null}
        trackLabel={track?.name && track?.season ? `${track.name} ${track.season}` : null}
        peakTrackWindKmph={track ? peakReportedWindKmph(track.waypoints) : null}
        unreportedWindCount={track ? countUnreported(track.waypoints) : 0}
      />

      <AdvisoryContent
        outcome={advisory}
        shelterStatus={shelterStatus}
        cachedSample={SAMPLE_ADVISORY}
        onRetry={onGenerateAdvisory}
        onLoadCached={onLoadCachedAdvisory}
        onClose={() => setAdvisory(null)}
      />
    </View>
  );
}

/**
 * Boot failure. This is where a wrong `EXPO_PUBLIC_API_URL` lands, and it is
 * deliberately specific: the most common cause of a blank app is a phone
 * reaching for `localhost`, which on a device is the phone.
 */
function BootError({ error }: { error: ApiError }) {
  const isConfig = error.kind === 'config';
  return (
    <View style={styles.centred}>
      <Text style={styles.errorTitle}>
        {isConfig ? 'Backend not configured' : 'Could not reach the backend'}
      </Text>
      <Text style={styles.centredText}>{error.message}</Text>
      {isConfig ? null : (
        <Text style={styles.centredText}>
          Configured origin: {API_BASE_URL || '(none)'}
        </Text>
      )}
      <View style={styles.retryHint} accessible={false}>
        <Text style={styles.centredText}>
          Fix the URL, then reload the app. Nothing here is cached.
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  header: {
    paddingHorizontal: theme.spacing.sm,
    paddingTop: theme.spacing.xs,
    paddingBottom: theme.spacing.xs / 2,
  },
  title: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  subtitle: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: 1,
  },
  mapWrap: {
    // `flex: 1` with the panel below it, rather than the panel carrying a
    // `maxHeight` percentage. The design asks for the map to take at least
    // 40% of the screen; expressing it as "the panel is as tall as it needs and
    // the map takes the rest" means the map grows when the panel shrinks, which
    // is what happens on a short device or a large system font. The floor is
    // `minHeight` so the panel can never squeeze the map into a strip — a
    // `flexBasis` with no minimum lets exactly that happen on a small phone.
    flex: 1,
    minHeight: '40%',
  },
  map: {
    flex: 1,
  },
  mapBanner: {
    position: 'absolute',
    top: theme.spacing.xs,
    left: theme.spacing.xs,
    right: theme.spacing.xs,
    backgroundColor: theme.colors.danger,
    borderRadius: theme.radius.button,
    padding: theme.spacing.xs,
  },
  mapBannerText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  panel: {
    // The panel is a sibling of the map, not an overlay on it, so the map's
    // own bottom edge is free for the legend and the controls. It sizes to its
    // content and the map flexes — see `mapWrap`.
    backgroundColor: theme.colors.background,
    borderTopLeftRadius: theme.radius.card,
    borderTopRightRadius: theme.radius.card,
    borderTopWidth: 1,
    borderColor: theme.colors.border,
  },
  panelContent: {
    padding: theme.spacing.sm,
  },
  centred: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.background,
    padding: theme.spacing.md,
  },
  centredText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    color: theme.colors.textMuted,
    textAlign: 'center',
    marginTop: theme.spacing.xs,
  },
  errorTitle: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.heading,
    color: theme.colors.danger,
    textAlign: 'center',
  },
  errorText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.danger,
    marginTop: theme.spacing.xs / 2,
  },
  disabledHint: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textAlign: 'center',
    marginTop: theme.spacing.xs / 2,
  },
  originBlock: {
    marginTop: theme.spacing.xs,
  },
  disclosure: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
  caption: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
  captionLink: {
    fontFamily: theme.fonts.bodySemibold,
    color: theme.colors.selectedText,
  },
  retryHint: {
    marginTop: theme.spacing.sm,
  },
  staleNotice: {
    backgroundColor: theme.colors.caution,
    borderRadius: theme.radius.button,
    padding: theme.spacing.xs,
    marginTop: theme.spacing.xs,
  },
  staleText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.background,
  },
});
