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
import MapView, { Marker, Overlay, Polyline } from 'react-native-maps';

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
  toLatLng,
  waypointSubtitle,
  waypointTitle,
  API_BASE_URL,
  type CategoriesResponse,
  type ExposureResponse,
  type InfraFeature,
  type Locality,
  type OverlayEntry,
  type SurgePreset,
  type TrackResponse,
} from '../api';
import { describeAdvisoryError, isAdvisoryStale } from '../advisoryFlow';
import { SAMPLE_ADVISORY } from '../sampleAdvisory';
import { theme } from '../theme';
import { AdvisoryContent, type AdvisoryOutcome } from './AdvisoryContent';
import { PathIcon, TargetIcon } from './ControlIcon';
import { FirstRunCard } from './FirstRunCard';
import { IntensityControl } from './IntensityControl';
import { LocalityPicker } from './LocalityPicker';
import { MapLegend } from './MapLegend';
import { PrimaryButton } from './PrimaryButton';
import { ReadoutPanel } from './ReadoutPanel';
import { SectionHeading } from './SectionHeading';
import {
  SAGAR_REGION,
  OVERLAY_BEARING,
  TRACK_FIT_DURATION_MS,
  TRACK_FIT_PADDING,
  assetPinColours,
  compromisedRoadDashPattern,
  compromisedRoadStyle,
  trackLineStyle,
  trackPinColour,
} from './mapStyles';

/**
 * Stage 2 — the map screen. Two screens total in this app; this is the first,
 * and the advisory modal is the second.
 *
 * What this screen deliberately does NOT do
 * -----------------------------------------
 * It does not fetch `/surge-zone`. That endpoint is live and correct, and at
 * category 6 it returns 532 polygons, 40,698 rings and 180,038 vertices —
 * 7.0 MB raw, 936 KB gzipped. `react-native-maps` will stutter drawing that,
 * and re-fetching it per slider step is a connection problem before it is a
 * rendering one. The flood is drawn instead from the pre-rendered PNG in
 * `data/overlays/`, placed with its own geographic bounds: 126,552 bytes at
 * category 6, handed to the map engine as a texture sample and never parsed.
 *
 * The exposure counts beside it come from `/exposure`, which is the real
 * full-resolution computation and knows nothing about the raster. That split
 * is the whole point — the picture is a shortcut, the numbers are not.
 *
 * No auth and no location permission, per the Module E brief. The backend is
 * public and unauthenticated for the demo, and the opening region is fixed to
 * Sagar Island, so nothing here can prompt for a permission dialog.
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

  const mapRef = useRef<MapView | null>(null);
  /**
   * Whether the map is currently showing the whole track, and so whether the
   * ghost control reads "Show storm path" or "Zoom to Sagar".
   *
   * This is the map's *own* state, tracked on purpose: a user who pans away
   * from the fitted view by hand leaves the label describing the last button
   * press rather than what is on screen. That is a small lie, and the fix —
   * inferring position from the camera — is not available here, because
   * `onRegionChangeComplete` fires for the programmatic fit too and would
   * immediately reset the label it is meant to keep. The label describes the
   * control's last action, which is what a toggle is.
   */
  const [fittingTrack, setFittingTrack] = useState(false);

  /**
   * Whether the three-step first-open card is still showing.
   *
   * **Session-scoped, not persisted.** `@react-native-async-storage/async-storage`
   * is not installed and this pass adds no dependency, so "first open" means
   * first open of this app session: dismissing the card and cold-starting again
   * brings it back. For a judged demo that is the *better* behaviour — a judge
   * who relaunches gets the explanation again rather than a bare map they may
   * not know how to drive — and for a real user it is wrong. Logged in
   * MEMORY.md rather than left to be discovered as a bug.
   */
  const [showFirstRun, setShowFirstRun] = useState(true);

  // Committed selection. `categoryIndex` is the value the map and the exposure
  // request agree on; `draftIndex` is only the thumb position, so a drag
  // updates the label without firing a request per frame.
  //
  // Default 5, which is a finding rather than a preference. Categories 0-4
  // expose **no infrastructure at all** — measured, not assumed: 0 hospitals,
  // 0 substations, 0 roads at each, and categories 0-3 do not flood a single
  // hectare either. Opening at a lower band therefore shows an empty map, an
  // empty readout and a disabled Generate Advisory, which reads as a broken
  // app rather than as a resolution limit.
  //
  // 5 is chosen over 6 deliberately. 6 is the top of the scale — 2,680 km²,
  // 251 roads cut off — and opening on it would lead every viewer with the
  // most extreme scenario the model can express. 5 is the *floor* that works:
  // the least dramatic defensible opening, with the escalation still visible by
  // dragging up and the actual case study one tap away on the preset button.
  const [categoryIndex, setCategoryIndex] = useState(5);
  const [draftIndex, setDraftIndex] = useState(5);
  const [presetId, setPresetId] = useState<string | null>(null);
  const [originId, setOriginId] = useState('sagar');

  const [exposure, setExposure] = useState<ExposureResponse | null>(null);
  const [exposureLoading, setExposureLoading] = useState(false);
  const [exposureError, setExposureError] = useState<ApiError | null>(null);

  // --- advisory ----------------------------------------------------------
  // The only state in this app that spends Gemini quota, and the only one
  // behind an explicit press.
  const [advisory, setAdvisory] = useState<AdvisoryOutcome | null>(null);
  const [advisoryBusy, setAdvisoryBusy] = useState(false);

  /**
   * `/allocation`'s `shelter_status`, for the disclosure notice.
   *
   * Fetched once, separately from boot, and **not** in boot's `Promise.all`:
   * this block is a supplementary disclosure, and a failure to obtain it must
   * not stop the map from opening. It fails toward disclosure — see
   * `sheltersAreDemoData`, where a null status means "show the warning".
   */
  const [shelterStatus, setShelterStatus] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let cancelled = false;
    // Category 0 is the cheapest valid input and the flag does not vary with
    // it: `shelter_dataset_status()` reads a static file.
    getAllocation(0, 'sagar')
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

  // --- boot: the four endpoints the screen cannot start without -----------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Parallel, not sequential: four cold requests in series is four
        // round trips before anything paints, and none depends on another.
        //
        // `/track` is in the same `all` rather than caught separately on
        // purpose. It is a historical record, not a live layer, and if the
        // committed file is missing the endpoint says exactly that ("re-fetch
        // it with fetch_ibtracs") — a boot error naming the file is a better
        // failure than a map that silently draws a real cyclone's track with
        // a hole in it.
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

  const activeCategory = categories[categoryIndex] ?? null;
  const activePreset = preset && presetId === preset.id ? preset : null;

  /**
   * The raster to draw. The preset has its own — it was rendered at exactly
   * 115 kmph / 1.2 m, which equals no band's midpoint, which is the entire
   * reason it needs a separate overlay.
   */
  const overlay: OverlayEntry | null = useMemo(() => {
    if (activePreset) return overlayIndex.find((o) => o.id === activePreset.id) ?? null;
    return overlayIndex.find((o) => o.id === `cat${categoryIndex}`) ?? null;
  }, [overlayIndex, categoryIndex, activePreset]);

  /**
   * The absolute URL to draw, or null when there is nothing worth drawing.
   *
   * Null in three distinct cases, and the component treats all three the same
   * way by not rendering at all:
   *
   *   - **no overlay** — the index is still loading, or this category has no
   *     entry;
   *   - **no URL** — `EXPO_PUBLIC_API_URL` is unset, so there is nothing
   *     absolute to fetch. The banner below the map already says so, which is
   *     the right place for a build-time misconfiguration to be reported;
   *   - **no water** — `flooded_pixels === 0`, which is the real state of
   *     categories 0-3. Those are legitimate images, and they are entirely
   *     transparent.
   *
   * The last case is the one this existed to fix. `<Overlay>` was rendered
   * unconditionally with `uri: overlay ? overlayImageUrl(overlay) ?? '' : ''`,
   * so on a category with no flood it was handed an empty string and a set of
   * bounds — an image fetch that can only fail, for a layer whose every pixel
   * is alpha 0. Not rendering is both cheaper and truthful: there is no flood
   * at this intensity, so there is no flood layer.
   */
  const overlayUri: string | null = useMemo(() => {
    if (overlay === null) return null;
    return shouldDrawOverlay(overlay) ? overlayImageUrl(overlay) : null;
  }, [overlay]);

  // --- exposure: one request per committed band --------------------------
  useEffect(() => {
    if (!categories.length) return undefined;
    let cancelled = false;
    setExposureLoading(true);
    setExposureError(null);
    getExposure(categoryIndex)
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
  }, [categoryIndex, categories.length]);

  const onCommit = useCallback((next: number) => {
    setCategoryIndex(next);
    setDraftIndex(next);
    // Moving the thumb off the preset returns to plain category mode, rather
    // than leaving a preset active for an intensity it was not rendered at.
    setPresetId(null);
  }, []);

  const onPressPreset = useCallback(() => {
    if (!preset || !categories.length) return;
    // The preset is not an IMD band, so every API-driven figure has to borrow
    // the nearest one. 115 kmph is 12 kmph from category 3's 103 and 20 from
    // category 4's 135, so it borrows category 3 — and the readout says so.
    const nearest = nearestCategory(categories, preset.wind_kmph);
    setCategoryIndex(nearest);
    setDraftIndex(nearest);
    setPresetId(preset.id);
  }, [preset, categories]);

  const exposedCount = exposure
    ? exposure.hospitals.count + exposure.substations.count + exposure.roads_cut_off.count
    : 0;

  /**
   * Decision 2: fit the map to the whole track, or come back to Sagar.
   *
   * Not the boot region. The flood overlay and every exposure number live on
   * Sagar Island, and a boot that zoomed out to the full track would show the
   * viewer a mostly-empty Bay of Bengal with a coastline-sized flood extent and
   * no readable assets. Sagar is the subject; the full track is a second look.
   *
   * The button is only rendered once `track` has loaded, because fitting to
   * nothing is a no-op the user cannot undo (it would look like a dead
   * control). Boot already fails loudly if the track is missing, so there is no
   * separate "no track" state to render here.
   */
  const onPressFit = useCallback(() => {
    const map = mapRef.current;
    if (!map || !track) return;
    if (fittingTrack) {
      map.animateToRegion(SAGAR_REGION, TRACK_FIT_DURATION_MS);
      setFittingTrack(false);
      return;
    }
    // `fitToCoordinates` rather than a computed `animateRegion`: it accounts
    // for the device's own aspect ratio, so the same edge padding frames the
    // track on a phone and on a tablet.
    //
    // Fitted over the finite coordinates only. `/track` coerces every position
    // with `float()` and does not range-check it, and `json.loads` accepts a
    // bare `NaN` — so a corrupt or hand-edited file can put a non-finite
    // latitude into `path`, and handing one to `fitToCoordinates` asks the
    // native map for a region it cannot compute. Filtering here costs a
    // comparison and keeps a bad row from blanking the map. The same values
    // are still drawn as a `<Polyline>` above; this only governs the camera.
    const coordinates = track.path.filter(
      (c) => Number.isFinite(c.latitude) && Number.isFinite(c.longitude),
    );
    if (coordinates.length === 0) return;
    map.fitToCoordinates(coordinates, {
      edgePadding: TRACK_FIT_PADDING,
      animated: true,
    });
    setFittingTrack(true);
  }, [fittingTrack, track]);

  const onGenerateAdvisory = useCallback(() => {
    // The in-flight lock. Not a spinner decoration: without it a second press
    // sends a second POST, and the backend's capacity ladder will happily spend
    // up to six more Gemini calls on it — three for a first draft and three
    // for a correction pass. A double-tap would be the most expensive possible
    // way to use this button.
    if (advisoryBusy) return;
    setAdvisoryBusy(true);
    setAdvisory({ status: 'loading' });

    postAdvisory(categoryIndex, originId)
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
  }, [advisoryBusy, categoryIndex, originId]);

  /**
   * Swap a failed live advisory for the bundled capture.
   *
   * The capture is real output from a real call, stored — but it is an answer
   * to whatever settings were on screen *when it was taken*, not to the ones
   * on screen now. The stale guard still runs over it, so a capture loaded at
   * a different intensity or origin is flagged rather than passed off as
   * current, and the modal's banner says when it was captured.
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

  const limitation = activePreset
    ? // The preset's limitation is the same model, and the same string.
      boot.categories.limitation
    : activeCategory?.limitation ?? boot.categories.limitation;

  return (
    <View style={styles.screen}>
      <View style={styles.mapWrap}>
        <MapView
          ref={mapRef}
          style={styles.map}
          initialRegion={SAGAR_REGION}
          // The flood is a single north-up raster and `<Overlay>` takes a
          // static `bearing` rather than tracking the map, so a rotated map
          // would leave the water at the wrong angle to the coast. Pinning
          // this off is a correctness decision, not a simplification.
          rotateEnabled={false}
          pitchEnabled={false}
          toolbarEnabled={false}
          showsUserLocation={false}
          showsMyLocationButton={false}
        >
          {/*
            `overlayUri` is the whole condition. When it is null there is no
            flood to draw, and rendering `<Overlay>` would mean handing the map
            a bounds tuple for a texture that does not exist — which is how
            this used to reach an empty `uri` on categories 0-3. See
            `overlayUri` above.
          */}
          {overlayUri !== null && overlay !== null ? (
            <Overlay
              image={{ uri: overlayUri }}
              /*
               * `bounds` is a two-corner tuple — **right-top, then left-bottom**
               * — and each corner is `[latitude, longitude]`, i.e. the opposite
               * order to the GeoJSON the rest of this app speaks. Passing an
               * object, or a [lon, lat] pair, silently places the image wrong
               * or not at all. See `overlayBounds` below.
               */
              bounds={overlayBounds(overlay)}
              bearing={OVERLAY_BEARING}
              tappable={false}
            />
          ) : null}

          {/* The cyclone's own path, under everything the model computed.
              Real observed history, fetched once at boot and never refetched
              when the slider moves — it does not vary with the intensity. */}
          {track ? (
            <Polyline
              coordinates={track.path}
              strokeWidth={3}
              {...trackLineStyle}
            />
          ) : null}

          {/* One pin per best-track fix; tapping it opens the native callout
              with the UTC time and the wind. `tracksViewChanges` is left at
              its default here (unlike AssetMarker) because these are 19 pins
              that carry a callout, and the callout is the point of them. */}
          {track?.waypoints.map((waypoint) => (
            <Marker
              key={`wp-${waypoint.sequence}`}
              coordinate={{ latitude: waypoint.latitude, longitude: waypoint.longitude }}
              pinColor={trackPinColour}
              title={waypointTitle(waypoint)}
              description={waypointSubtitle(waypoint)}
            />
          ))}

          {/* Roads first, so markers draw on top of them. 251 polylines at
              category 6 is the heaviest thing on this map; the count beside
              them always matches what is drawn, because capping the layer
              would make the picture disagree with the number. */}
          {exposure?.roads_cut_off.features.flatMap((road, i) =>
            roadPaths(road).map((path, part) => (
              <Polyline
                key={`road-${i}-${part}-${road.properties.name}`}
                coordinates={path}
                strokeWidth={2}
                lineDashPattern={compromisedRoadDashPattern}
                {...compromisedRoadStyle}
              />
            )),
          )}

          {exposure?.hospitals.features.map((feature, i) => (
            <AssetMarker
              key={`hosp-${i}-${feature.properties.name}`}
              feature={feature}
              pinColour={assetPinColours.hospital}
            />
          ))}
          {exposure?.substations.features.map((feature, i) => (
            <AssetMarker
              key={`sub-${i}-${feature.properties.name}`}
              feature={feature}
              pinColour={assetPinColours.substation}
            />
          ))}
        </MapView>

        {overlay !== null && overlayImageUrl(overlay) === null ? (
          <View style={styles.mapBanner}>
            <Text style={styles.mapBannerText}>
              No flood layer: EXPO_PUBLIC_API_URL is not set, so the overlay PNG has no
              absolute URL to load.
            </Text>
          </View>
        ) : null}

        {/*
          The legend, then the first-open card, then the storm-path control —
          three `position: absolute` siblings claiming the map's edges:
          legend bottom-left, first-run top, control bottom-right. They never
          overlap, and all three are inside this wrapper rather than siblings of
          the map, so the MapView's viewport is unchanged.

          The banner above is the one thing that can collide, and it can: it is
          also top-anchored and full-width. The banner only renders on a
          build-time misconfiguration (no `EXPO_PUBLIC_API_URL`), and in that
          state the map is showing no flood at all — so the first-open card on
          top of it is a cosmetic overlap in an already-broken state, and
          hiding the card behind a build error would be the worse trade.
        */}
        <MapLegend />

        {showFirstRun ? <FirstRunCard onDismiss={() => setShowFirstRun(false)} /> : null}

        {/* Ghost control, per decision 2. Bottom-right of the map, not the
            top: the banner above is full-width and its height varies with the
            text, so anything anchored to the top would be covered by it
            whenever both are on screen. The readout panel is a *sibling* of
            this wrapper, not an overlay, so the map's own bottom edge is free.

            "Full track" and "Back to Sagar" were both replaced on 2026-09-30.
            Neither old name described the action: "Full track" says what the
            view *contains* rather than what pressing it *does*, and "Back to
            Sagar" reads as navigation away from a place the viewer was never
            at — the control returns to the opening region, and "Zoom to Sagar"
            says exactly that. Each label now sits beside an icon for the same
            reason: the control is the only one on this screen, so it has to
            identify itself rather than rely on the reader having read the
            README. */}
        {track ? (
          <Pressable
            onPress={onPressFit}
            accessibilityRole="button"
            accessibilityLabel={
              fittingTrack ? 'Zoom the map to Sagar Island' : 'Show the full storm path'
            }
            style={({ pressed }) => [
              styles.fitControl,
              pressed ? styles.fitControlPressed : null,
            ]}
          >
            {fittingTrack ? <TargetIcon /> : <PathIcon />}
            <Text style={styles.fitControlLabel}>
              {fittingTrack ? 'Zoom to Sagar' : 'Show storm path'}
            </Text>
          </Pressable>
        ) : null}
      </View>

      <ScrollView
        style={styles.panel}
        contentContainerStyle={styles.panelContent}
        showsVerticalScrollIndicator={false}
      >
        {/*
          Provenance, on screen. The track's numbers are real observed data, so
          the two things a reader could be misled about are which dataset they
          came from and whether they are a prediction — the caption answers
          both in one line, in the same muted caption voice the model's own
          limitation uses.
        */}
        {track ? (
          <Text style={styles.trackCaption}>
            {track.name} {track.season} · {track.waypoint_count} best-track fixes · {track.source}.
            A record of what happened — not a forecast, and not a prediction for any other
            storm.
          </Text>
        ) : null}

        <SectionHeading label="Storm" />

        <IntensityControl
          count={categories.length}
          value={categoryIndex}
          draftValue={draftIndex}
          onDraftChange={setDraftIndex}
          onCommit={onCommit}
          bandLabel={activePreset ? activePreset.label : activeCategory?.imd_category ?? ''}
          windKmph={activePreset ? activePreset.wind_kmph : activeCategory?.wind_kmph ?? 0}
          preset={preset ? { id: preset.id, label: 'Remal (as observed)' } : null}
          presetActive={!!activePreset}
          onPressPreset={onPressPreset}
        />

        <SectionHeading label="Impact" />

        <ReadoutPanel
          overlay={overlay}
          exposure={exposure}
          loading={exposureLoading}
          bandNote={activePreset ? undefined : activeCategory?.note}
          borrowedBandLabel={
            activePreset && activeCategory ? activeCategory.imd_category : undefined
          }
          limitation={limitation}
        />

        {exposureError ? (
          <Text style={styles.errorText}>
            Exposure could not be loaded: {exposureError.message}
          </Text>
        ) : null}

        <SectionHeading label="Where are you" />

        <LocalityPicker
          localities={localities}
          selectedId={originId}
          onSelect={setOriginId}
        />
      </ScrollView>

      {/*
        Generate Advisory, pinned outside the ScrollView.

        It was the last child of the panel, which meant it was on screen only
        when the reader had scrolled to the bottom — and the panel's own
        contents are taller than its 52% cap, so at boot the button the whole
        app exists to press was below the fold. Worse, it was the *only* thing
        that moved when the reader scrolled, so the one fixed action in the app
        was the one that scrolled away.

        Moving it out of the ScrollView and giving it its own footer pins it to
        the bottom of the screen at every scroll position. The cost is real and
        is the reason the panel's `maxHeight` was reduced: the footer now
        permanently occupies the bottom ~64dp, so the panel is capped at 46%
        rather than 52% to keep the map — the thing the button summarises — from
        being squeezed to a strip. The footer also draws a top border, because
        content scrolls underneath it and without the rule the cutoff is
        invisible.

        The button stays disabled at 5 of the 7 slider positions and for the
        preset, because 5 of 7 bands expose no infrastructure at all. That is
        the honest behaviour, and it is also a demo problem worth saying out
        loud rather than hiding behind a disabled button: the core loop is
        currently only reachable at categories 5 and 6.
      */}
      <View style={styles.footer}>
        {/*
          The stale guard. The advisory on screen is written for one
          `${category}:${origin}` pair; if the reader has since moved the slider
          or the picker, the prose is describing a scenario they are no longer
          looking at. The advisory is still shown — it is real output about a
          real modelled storm — but never presented as the answer to the current
          settings.

          It lives *inside* the footer, immediately above the button, and that
          is a change of position as well as of implementation. It was
          `position: absolute` at the bottom of the screen, which the footer
          now occupies — left there it would have rendered on top of the
          button it is warning about. Directly above the button is also the
          place it belongs: it is a statement about the advisory, and the
          button is how you replace it.
        */}
        {advisory?.status === 'ready' && isAdvisoryStale(advisory.response, categoryIndex, originId) ? (
          <View style={styles.staleNotice}>
            <Text style={styles.staleText}>
              Generated for {advisory.response.generated_for.imd_category} at{' '}
              {advisory.response.generated_for.origin.name} — the settings have changed since.
              Close and generate again for the current scenario.
            </Text>
          </View>
        ) : null}

        <PrimaryButton
          label="Generate Advisory"
          onPress={onGenerateAdvisory}
          disabled={exposedCount === 0 || exposureLoading}
          busy={advisoryBusy}
        />
        {exposedCount === 0 ? (
          <Text style={styles.disabledHint}>
            Disabled: no modelled exposure at this intensity, so there is nothing to
            evacuate.
          </Text>
        ) : null}
      </View>

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
 * The `<Overlay>` bounds tuple: `[[north, east], [south, west]]`.
 *
 * Two transpositions meet here and neither throws. `<Overlay>` wants a
 * two-corner tuple, right-top first, and each corner is `[latitude,
 * longitude]` — the reverse of the GeoJSON the rest of this app speaks, and
 * the reverse of `<Marker>`'s named `{latitude, longitude}` fields. Passing
 * `{north, south, east, west}` (the intuitive shape, and the one every
 * tutorial uses) is a type error here rather than a silent one, which is the
 * only reason this is easy to get right at all.
 *
 * Falls back to the opening region when the index has not loaded, so the
 * component tree is valid during boot rather than conditionally null.
 */
function overlayBounds(overlay: OverlayEntry | null): [[number, number], [number, number]] {
  if (overlay) {
    return [
      [overlay.bounds.north, overlay.bounds.east],
      [overlay.bounds.south, overlay.bounds.west],
    ];
  }
  return [
    [SAGAR_REGION.latitude + SAGAR_REGION.latitudeDelta / 2, SAGAR_REGION.longitude + SAGAR_REGION.longitudeDelta / 2],
    [SAGAR_REGION.latitude - SAGAR_REGION.latitudeDelta / 2, SAGAR_REGION.longitude - SAGAR_REGION.longitudeDelta / 2],
  ];
}

/**
 * One exposed point asset. The geometry is mixed (`Point`, `LineString`,
 * `Polygon`, `MultiPolygon` all occur in the live payload), so the coordinate
 * comes from `markerCoordinate()` rather than from indexing the geometry
 * directly — see the note on `InfraFeature` in `api.ts` for why that indexing
 * was wrong.
 */
function AssetMarker({
  feature,
  pinColour,
}: {
  feature: InfraFeature;
  pinColour: string;
}) {
  const coordinate = toLatLng(markerCoordinate(feature));
  // A NaN coordinate renders off-screen and reads as a missing asset, which
  // would quietly disagree with the count in the readout.
  if (!coordinate) return null;
  return (
    <Marker
      coordinate={coordinate}
      pinColor={pinColour}
      title={feature.properties.name || 'Unnamed'}
      description={feature.properties.status}
      // Android re-renders the pin bitmap on every parent update unless told
      // otherwise; with 34 markers on a screen that updates on every exposure
      // response, that is a visible stall.
      tracksViewChanges={false}
    />
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
      <Pressable onPress={() => {}} style={styles.retryHint} accessible={false}>
        <Text style={styles.centredText}>
          Fix the URL, then reload the app. Nothing here is cached.
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  mapWrap: {
    flex: 1,
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
    color: theme.colors.card,
  },
  fitControl: {
    position: 'absolute',
    bottom: theme.spacing.xs,
    right: theme.spacing.xs,
    // `center` rather than the previous default, because the control now holds
    // an icon and a label side by side and a top-aligned icon beside centred
    // text reads as two things that do not belong to each other.
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: theme.spacing.xs / 2,
    paddingHorizontal: theme.spacing.sm,
    borderRadius: theme.radius.chip,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    ...theme.shadow.card,
  },
  fitControlPressed: {
    // Design.md defines no pressed variant for a ghost control; opacity only,
    // as on PrimaryButton, rather than inventing a token.
    opacity: 0.7,
  },
  fitControlLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginLeft: theme.spacing.xs / 2,
  },
  panel: {
    // 46%, not the previous 52%: the footer below now permanently occupies the
    // bottom of the screen, and this cap is what keeps the map from being
    // squeezed into a strip by the panel plus the footer together.
    maxHeight: '46%',
    backgroundColor: theme.colors.background,
    borderTopLeftRadius: theme.radius.card,
    borderTopRightRadius: theme.radius.card,
  },
  footer: {
    // Outside the ScrollView so the button is pinned; the border because the
    // panel's content scrolls up underneath it and without a rule the cutoff
    // is invisible.
    backgroundColor: theme.colors.background,
    borderTopWidth: 1,
    borderTopColor: theme.colors.border,
    paddingHorizontal: theme.spacing.sm,
    paddingTop: theme.spacing.xs,
    paddingBottom: theme.spacing.sm,
  },
  panelContent: {
    padding: theme.spacing.sm,
    paddingTop: theme.spacing.xs,
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
    marginTop: theme.spacing.xs,
    // `marginBottom` because the "Where are you" heading now follows this text
    // directly, and the heading carries only its own `marginTop` of a single
    // step — not enough on its own after a multi-line error.
    marginBottom: theme.spacing.xs,
  },
  trackCaption: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginBottom: theme.spacing.xs,
  },
  disabledHint: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textAlign: 'center',
    marginTop: theme.spacing.xs / 2,
  },
  retryHint: {
    marginTop: theme.spacing.sm,
  },
  staleNotice: {
    // No longer absolutely positioned — it is a child of the footer now, so it
    // sits directly above the button it warns about instead of being painted
    // over it. `marginBottom` is what separates it from the button.
    backgroundColor: theme.colors.caution,
    borderRadius: theme.radius.button,
    padding: theme.spacing.xs,
    marginBottom: theme.spacing.xs,
  },
  staleText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
});
