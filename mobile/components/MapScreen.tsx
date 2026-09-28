import React, { useCallback, useEffect, useMemo, useState } from 'react';
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
  getCategories,
  getExposure,
  getLocalities,
  getOverlays,
  markerCoordinate,
  nearestCategory,
  overlayImageUrl,
  roadPaths,
  toLatLng,
  API_BASE_URL,
  type CategoriesResponse,
  type ExposureResponse,
  type InfraFeature,
  type Locality,
  type OverlayEntry,
  type SurgePreset,
} from '../api';
import { theme } from '../theme';
import { IntensityControl } from './IntensityControl';
import { LocalityPicker } from './LocalityPicker';
import { PrimaryButton } from './PrimaryButton';
import { ReadoutPanel } from './ReadoutPanel';
import {
  SAGAR_REGION,
  OVERLAY_BEARING,
  assetPinColours,
  compromisedRoadDashPattern,
  compromisedRoadStyle,
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
    };

export function MapScreen() {
  const [boot, setBoot] = useState<Boot>({ status: 'loading' });

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

  // --- boot: the three endpoints the screen cannot start without ----------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Parallel, not sequential: three cold requests in series is three
        // round trips before anything paints, and none depends on another.
        const [categories, overlays, localities] = await Promise.all([
          getCategories(),
          getOverlays(),
          getLocalities(),
        ]);
        if (cancelled) return;
        setBoot({ status: 'ready', categories, overlayIndex: overlays.overlays, localities: localities.localities });
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
          <Overlay
            image={{ uri: overlay ? overlayImageUrl(overlay) ?? '' : '' }}
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
      </View>

      <ScrollView
        style={styles.panel}
        contentContainerStyle={styles.panelContent}
        showsVerticalScrollIndicator={false}
      >
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

        <LocalityPicker
          localities={localities}
          selectedId={originId}
          onSelect={setOriginId}
        />

        {/*
          Generate Advisory stays disabled at 5 of the 7 slider positions and
          for the preset, because 5 of 7 bands expose no infrastructure at
          all. That is the honest behaviour, and it is also a demo problem
          worth saying out loud rather than hiding behind a disabled button:
          the core loop is currently only reachable at categories 5 and 6.
        */}
        <PrimaryButton
          label="Generate Advisory"
          onPress={() => {
            // Stage 3 owns the POST. Wired to a press and nothing else —
            // never to onValueChange (Rules.md: Gemini is behind an explicit
            // user action).
          }}
          disabled={exposedCount === 0 || exposureLoading}
        />
        {exposedCount === 0 ? (
          <Text style={styles.disabledHint}>
            Disabled: no modelled exposure at this intensity, so there is nothing to
            evacuate.
          </Text>
        ) : null}
      </ScrollView>
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
  panel: {
    maxHeight: '52%',
    backgroundColor: theme.colors.background,
    borderTopLeftRadius: theme.radius.card,
    borderTopRightRadius: theme.radius.card,
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
});
