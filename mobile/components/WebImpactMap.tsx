/**
 * The Web map — a projected SVG over the DEM-derived basemap.
 *
 * ## The one rule this component exists to enforce
 *
 * **`react-native-maps` must never be imported, transitively, from this
 * file's module graph.** It is a native module; in a browser bundle its
 * native components resolve to `codegenNativeComponent`, which does not exist
 * outside a React Native runtime and throws on first render. The previous
 * Web build crashed exactly that way — `codegenNativeComponent is not a
 * function` — and the whole screen was blank.
 *
 * That is why this is a separate `MapScreen.web.tsx` rather than a platform
 * branch inside `MapScreen.tsx`: a shared file would have to import the
 * native module for the native path, and one bad barrel export puts it back
 * in the Web graph. The boundary here is the `.web.tsx` suffix, which Metro
 * resolves in preference to `.tsx`.
 *
 * **`mapStyles.ts` is likewise not imported**, even though it only imports a
 * *type* from `react-native-maps`. A type-only import is erased at compile
 * time and is safe, but relying on that erasure for a module this
 * safety-critical is the wrong kind of clever: the colours are re-derived
 * from `theme.ts` below, and `tests/webBundleSafety.test.mjs` asserts the
 * absence of both module names from this file as source text.
 *
 * ## What is drawn, and where each layer's data comes from
 *
 * | Layer | Source | Real? |
 * |---|---|---|
 * | land/water base | `assets/basemap.png` from the committed DEM at 0 m | derived from committed data |
 * | flood extent | `GET /overlays` PNG for the selected chip | the model's own output |
 * | storm track | `GET /track` — 19 IBTrACS fixes | observed history |
 * | cut-off roads | `GET /exposure` → `roads_cut_off` | the intersection test |
 * | hospitals / substations | `GET /exposure` | same |
 * | origin pin | `GET /localities` | same |
 *
 * **Nothing is invented.** There is no basemap tile server, no coastline
 * fetched from a third party, and no marker that is not a coordinate the
 * backend returned. Where a layer has no features it is simply absent.
 */

import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
  type LayoutChangeEvent,
} from 'react-native';

import {
  markerCoordinate,
  shouldDrawOverlay,
  toLatLng,
  API_BASE_URL,
  type ExposureResponse,
  type LocalitiesResponse,
  type OverlayEntry,
  type RoadFeature,
  type TrackResponse,
} from '../api';
import { BASEMAP_META, BASEMAP_DISCLOSURE } from '../basemap';
import { BASEMAP_ASSET } from '../webBasemap';
import {
  boundsOf,
  imageRect,
  latLngPoints,
  makeProjection,
  openingBounds,
  pathPoints,
  viewBoxFor,
  type GeoBounds,
} from '../mapProjection';
import { theme } from '../theme';
import { LEGEND_ROWS, type SwatchSource } from '../legend';
import { isCompactViewport, mapPanelHeightPx } from '../webViewModel';

/**
 * Height reserved at the bottom of the map for the disclosure strip.
 *
 * The strip is `position: absolute; bottom: 0` across the full width, and the
 * legend was independently `bottom: 12` — so the legend's last rows rendered
 * *behind* the strip and "Storm path" was cut off. Positioning the legend
 * above this value fixes it, and naming the value here means the two cannot
 * be changed in one place only.
 */
const DISCLOSURE_STRIP_HEIGHT = 44;

/** The SVG user units the map draws in. Scaled by CSS to its container. */
/**
 * The drawing's user-unit width. The **height is derived per view** by
 * `viewBoxFor(bounds)`, never fixed.
 *
 * It used to be a fixed `VIEW_H = 760`, and that single number is why 84 of 91
 * markers were invisible. A plate-carrée projection maps the whole bbox onto
 * the whole viewBox, so the viewBox aspect has to equal the bbox's *ground*
 * aspect (`spanLon / (spanLat * cos 22.5°)` ≈ 1.62 here). At 1.32 it did not:
 * `preserveAspectRatio="xMidYMid meet"` letterboxed the drawing into a band
 * while the projection carried on past the frame, so most geometry landed
 * outside the visible area. Deriving the height removes the possibility of the
 * two disagreeing.
 */
const VIEW_W = 1000;

/** Asset-pin colours, matching `mapStyles.assetPinColours`. */
const PIN_COLOURS = {
  hospital: theme.colors.danger,
  substation: theme.colors.caution,
  origin: theme.colors.selectedText,
  shelter: theme.colors.textMuted,
} as const;

/**
 * Resolved legend swatches.
 *
 * The rows themselves come from `legend.ts`, which names each colour as a
 * field path rather than a value, precisely so no colour is ever retyped. This
 * switch is the one place that resolves a name to a value — and it is a
 * `Record<SwatchSource, string>` rather than a `switch`, so adding a name to
 * the union without giving it a colour is a compile error. The native app does
 * the same thing with a `switch` for the same reason.
 */
const SWATCHES: Record<SwatchSource, string> = {
  'theme.flood': theme.colors.flood,
  'theme.water': theme.colors.water,
  'mapStyles.assetPinColours.hospital': PIN_COLOURS.hospital,
  'mapStyles.assetPinColours.substation': PIN_COLOURS.substation,
  'mapStyles.compromisedRoadStyle.strokeColor': theme.colors.danger,
  'mapStyles.trackLineStyle.strokeColor': theme.colors.background,
};

export interface WebImpactMapProps {
  exposure: ExposureResponse | null;
  track: TrackResponse | null;
  overlay: OverlayEntry | null;
  localities: LocalitiesResponse | null;
  originId: string;
  shelter: { name: string; lon: number; lat: number } | null;
  /** Route polyline from `/routes`, as a lon/lat list. */
  routeCoordinates: ReadonlyArray<number[]>;
  routeReachable: boolean;
  loading: boolean;
  /** Disables pointer interaction while a request is in flight. */
  busy: boolean;
}

/**
 * Which geographic window the map is showing.
 *
 * Named `MapView` rather than `View` because this file imports React Native's
 * `View` for layout — the collision was a real compile error, not a style
 * preference. It is not the native `<MapView>`; it is the map's own camera
 * state, which on Web is a bounding box this component projects into.
 */
type MapCamera =
  | { kind: 'region' }
  | { kind: 'track'; bounds: GeoBounds };

/**
 * Web-only props React Native's types do not declare.
 *
 * `title` is a real HTML attribute and is what produces the browser's native
 * hover tooltip; `hovered` is what `react-native-web`'s `Pressable` passes to
 * the style callback on hover. Neither exists on iOS or Android, which is why
 * this type stays in the `.web.tsx` graph and is never shared with the native
 * screen.
 *
 * The index signature is safe because these are *additional* props on an
 * element that already satisfies its native contract — not a way to pass a
 * wrong value for a typed one.
 */
type WebOnly = Record<string, unknown>;

export function WebImpactMap({
  exposure,
  track,
  overlay,
  localities,
  originId,
  shelter,
  routeCoordinates,
  routeReachable,
  loading,
  busy,
}: WebImpactMapProps) {
  const [view, setView] = useState<MapCamera>({ kind: 'region' });
  const [hovered, setHovered] = useState<string | null>(null);
  const [showLegend, setShowLegend] = useState(true);
  const svgRef = useRef<SVGSVGElement | null>(null);

  /**
   * The panel's own width, measured rather than assumed. In the two-column
   * layout this view gets a flex share of the workspace, so no constant can
   * know it; `onLayout` reports the border-box width, and the drawing sits
   * inside a 1 px border, so 2 px come off before the aspect is matched.
   */
  const [panelWidth, setPanelWidth] = useState<number | null>(null);
  const { width: viewportWidth, height: viewportHeight } = useWindowDimensions();
  const compact = isCompactViewport(viewportWidth);
  const onPanelLayout = useCallback((event: LayoutChangeEvent) => {
    const { width } = event.nativeEvent.layout;
    setPanelWidth((current) => (current === width ? current : width));
  }, []);

  const bounds = view.kind === 'track' ? view.bounds : openingBounds();
  /**
   * The frame, shaped to the data. See `VIEW_W` above for why the height is
   * derived rather than fixed.
   */
  const frame = useMemo(() => viewBoxFor(bounds, VIEW_W), [bounds]);
  /**
   * The panel's height, matched to that frame by the pure `mapPanelHeightPx`
   * — **the fix for the black strip**. The `<svg>` fills the panel with
   * `preserveAspectRatio="xMidYMid meet"`, and `meet` letterboxes whenever
   * the panel's aspect differs from the frame's; the letterbox shows this
   * panel's near-black background. That is the strip in the deployed
   * screenshots — not a basemap that failed to fill its container — and it
   * appeared at every width because the panel's height was fixed (460 px)
   * while its width floated. Sizing the panel to the drawing instead makes
   * `meet` a no-op and the pale basemap runs edge to edge. Null only before
   * the first `onLayout`, where the base style still applies.
   */
  const panelHeightPx = useMemo(
    () =>
      panelWidth === null
        ? null
        : mapPanelHeightPx({
            panelWidthPx: Math.max(0, panelWidth - 2),
            frameAspect: frame.width / frame.height,
            viewportHeightPx: viewportHeight,
            compact,
          }),
    [panelWidth, frame, viewportHeight, compact],
  );
  const projection = useMemo(
    () => makeProjection(bounds, frame.width, frame.height),
    [bounds, frame],
  );

  /**
   * The basemap and the flood raster are placed through the *same*
   * projection as every vector layer, so they register exactly. Their image
   * bounds are the DEM bbox in both cases, which means at the opening view
   * they cover most of the frame and the projection is what makes them line
   * up with the coastline rather than a hand-tuned offset.
   */
  const basemapRect = useMemo(() => imageRect(BASEMAP_META.bounds, projection), [projection]);

  const overlayUrl =
    overlay !== null && shouldDrawOverlay(overlay) && API_BASE_URL
      ? `${API_BASE_URL}${overlay.image_url}`
      : null;
  const overlayRect = useMemo(
    () => (overlay !== null ? imageRect(overlay.bounds, projection) : null),
    [overlay, projection],
  );

  /** Flooded area in the frame, as the model's own figure — never measured. */
  const floodAreaKm2 = overlay?.final_land_area_km2 ?? exposure?.final_land_area_km2 ?? null;

  const trackPoints = useMemo(
    () => (track !== null ? latLngPoints(track.path, projection) : null),
    [track, projection],
  );

  /**
   * Roads. `/exposure` is 349 KB at category 6 with 251 features, and this
   * draws every one — the same rule the native screen follows, because a
   * capped layer would make the picture disagree with the count beside it.
   *
   * At 251 `<path>` elements with round caps the browser handles this
   * comfortably; it is the 180k-vertex *flood polygon* that was never
   * drawable, and that layer is a raster here instead.
   */
  const roads = useMemo(() => {
    const features = exposure?.roads_cut_off.features ?? [];
    return features.flatMap((feature: RoadFeature, index: number) => {
      const parts: ReadonlyArray<number[][]> =
        feature.geometry.type === 'LineString'
          ? [feature.geometry.coordinates]
          : feature.geometry.coordinates;
      return parts
        .map((part) => pathPoints(part, projection))
        .filter((points): points is string => points !== null)
        .map((points, part) => ({
          key: `road-${index}-${part}`,
          name: feature.properties.name || 'Unnamed road',
          points,
        }));
    });
  }, [exposure, projection]);

  const assets = useMemo(() => {
    const project = (
      features: ReadonlyArray<Parameters<typeof markerCoordinate>[0]>,
      colour: string,
      kind: 'hospital' | 'substation',
    ) =>
      features.flatMap((feature, index) => {
        const ll = toLatLng(markerCoordinate(feature));
        if (!ll) return [];
        const { x, y } = projection.project(ll);
        return [
          {
            key: `${kind}-${index}`,
            kind,
            name: feature.properties.name || 'Unnamed',
            status: feature.properties.status,
            x,
            y,
            colour,
          },
        ];
      });

    return [
      ...project(exposure?.hospitals.features ?? [], PIN_COLOURS.hospital, 'hospital'),
      ...project(exposure?.substations.features ?? [], PIN_COLOURS.substation, 'substation'),
    ];
  }, [exposure, projection]);

  const originPoint = useMemo(() => {
    const locality = localities?.localities.find((l) => l.id === originId);
    if (!locality) return null;
    const { x, y } = projection.project({ latitude: locality.lat, longitude: locality.lon });
    return { x, y, name: locality.name };
  }, [localities, originId, projection]);

  const shelterPoint = useMemo(() => {
    if (!shelter) return null;
    const { x, y } = projection.project({ latitude: shelter.lat, longitude: shelter.lon });
    return { x, y, name: shelter.name };
  }, [shelter, projection]);

  const routePoints = useMemo(
    () => (routeCoordinates.length > 0 ? pathPoints(routeCoordinates, projection) : null),
    [routeCoordinates, projection],
  );

  const canFitTrack = useMemo(() => {
    if (!track) return false;
    return boundsOf(track.path) !== null;
  }, [track]);

  const onPressFitTrack = () => {
    if (view.kind === 'track') {
      setView({ kind: 'region' });
      return;
    }
    const fitted = track ? boundsOf(track.path) : null;
    if (fitted) setView({ kind: 'track', bounds: fitted });
  };

  const onPressRecentre = () => setView({ kind: 'region' });

  return (
    <View
      style={[
        styles.wrap,
        // `+ 2` is the panel's own 1 px border, so the drawing's content box
        // is exactly aspect-matched and `meet` has nothing to letterbox.
        panelHeightPx !== null && {
          height: panelHeightPx + 2,
          minHeight: panelHeightPx + 2,
          flex: 0,
        },
      ]}
      onLayout={onPanelLayout}
    >
      {/*
        The SVG. `viewBox` is the user-unit space every coordinate above was
        projected into, and its shape is derived from the bbox so the drawing
        fills the frame rather than being letterboxed inside it.
      */}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${frame.width} ${frame.height}`}
        preserveAspectRatio="xMidYMid meet"
        style={styles.svg}
        role="img"
        aria-label={
          `Flood extent, storm track and exposed infrastructure over the ` +
          `Sundarbans delta near Sagar Island`
        }
      >
        {/*
          A clip so nothing draws outside the frame. The track fitted to its
          own bounds has coordinates at the very edge, and a polyline's
          round caps would otherwise bleed past the viewBox.
        */}
        <defs>
          <clipPath id="web-map-clip">
            <rect x="0" y="0" width={frame.width} height={frame.height} />
          </clipPath>
          {/*
            The flood is the model's own raster, painted #2563eb at four depth
            classes. It needs no filter to read against the pale basemap —
            the raster was authored for exactly this backdrop.
          */}
        </defs>

        <rect
          x="0"
          y="0"
          width={frame.width}
          height={frame.height}
          fill={theme.colors.background}
        />

        <g clipPath="url(#web-map-clip)">
          {/* --- base layer: land and water from the committed DEM --------- */}
          {basemapRect !== null ? (
            <image
              href={BASEMAP_ASSET}
              x={basemapRect.x}
              y={basemapRect.y}
              width={basemapRect.width}
              height={basemapRect.height}
              preserveAspectRatio="none"
            />
          ) : null}

          {/* --- the flood: the model's own raster for this chip ---------- */}
          {overlayUrl !== null && overlayRect !== null ? (
            <image
              href={overlayUrl}
              x={overlayRect.x}
              y={overlayRect.y}
              width={overlayRect.width}
              height={overlayRect.height}
              preserveAspectRatio="none"
            />
          ) : null}

          {/* --- roads first, so markers draw over them ------------------- */}
          <g>
            {roads.map((road) => (
              <path
                key={road.key}
                d={`M${road.points.replace(/ /g, 'L')}`}
                fill="none"
                stroke={theme.colors.danger}
                strokeWidth={1.6}
                strokeDasharray="5 3"
                strokeLinecap="round"
                opacity={0.85}
              />
            ))}
          </g>

          {/* --- the observed storm track ---------------------------------
              Dashed and near-black, per Design.md: the track is the one layer
              that crosses every other, and solid it would win each crossing.
              Dash also separates "what happened" from "what would happen" by
              texture as well as colour. `lineDashPattern` is not honoured by
              react-native-maps on Android; in SVG it always is, so the Web
              build is not subject to that platform limit.
          */}
          {trackPoints !== null ? (
            <polyline
              points={trackPoints}
              fill="none"
              stroke={theme.colors.background}
              strokeWidth={3}
              strokeDasharray="12 7"
              strokeLinecap="round"
            />
          ) : null}

          {/* Waypoint pins. Small and low-contrast on purpose: 19 of them are
              context, and the exposed-asset pins are the finding. */}
          {track?.waypoints.map((waypoint) => {
            const { x, y } = projection.project({
              latitude: waypoint.latitude,
              longitude: waypoint.longitude,
            });
            return (
              <g key={`wp-${waypoint.sequence}`}>
                <circle cx={x} cy={y} r={3.2} fill={theme.colors.background} />
                <title>
                  {`${waypoint.timestamp} · ` +
                    (waypoint.wind_reported && waypoint.wind_kmph !== null
                      ? `${waypoint.wind_kmph.toFixed(0)} km/h reported`
                      : 'Wind not reported for this fix')}
                </title>
              </g>
            );
          })}

          {/* --- the safe route, when there is one ----------------------- */}
          {routePoints !== null && routeReachable ? (
            <polyline
              points={routePoints}
              fill="none"
              stroke={PIN_COLOURS.origin}
              strokeWidth={3}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}

          {/* --- shelter ------------------------------------------------- */}
          {shelterPoint !== null ? (
            <g>
              <rect
                x={shelterPoint.x - 6}
                y={shelterPoint.y - 6}
                width={12}
                height={12}
                fill={theme.colors.background}
                stroke={PIN_COLOURS.shelter}
                strokeWidth={1.6}
                transform={`rotate(45 ${shelterPoint.x} ${shelterPoint.y})`}
              />
              <title>{shelterPoint.name}</title>
            </g>
          ) : null}

          {/* --- exposed assets ------------------------------------------
              `tracksViewChanges` is Android-specific and has no SVG analogue;
              the native screen sets it false to stop the pin bitmap
              re-rendering on every parent update, and here there is no native
              pin to re-render.
          */}
          {assets.map((asset) => (
            <g
              key={asset.key}
              onMouseEnter={() => setHovered(`${asset.key}:${asset.name}`)}
              onMouseLeave={() => setHovered(null)}
            >
              <circle cx={asset.x} cy={asset.y} r={9} fill="transparent" />
              <circle cx={asset.x} cy={asset.y} r={4.5} fill={asset.colour} />
              <title>{`${asset.name} — ${asset.status}`}</title>
            </g>
          ))}

          {/* --- the origin, drawn last so nothing covers it -------------- */}
          {originPoint !== null ? (
            <g>
              <circle
                cx={originPoint.x}
                cy={originPoint.y}
                r={11}
                fill="none"
                stroke={PIN_COLOURS.origin}
                strokeWidth={2}
              />
              <circle cx={originPoint.x} cy={originPoint.y} r={5} fill={PIN_COLOURS.origin} />
              <title>{`Evacuation origin: ${originPoint.name}`}</title>
            </g>
          ) : null}
        </g>
      </svg>

      {/* --- controls, bottom-right, mirroring MapControl ---------------- */}
      <View style={styles.controls}>
        <MapButton
          Icon={TargetIcon}
          active={view.kind === 'region'}
          label="Study region"
          title="Zoom to the study region"
          onPress={onPressRecentre}
        />
        <MapButton
          Icon={TrackIcon}
          active={view.kind === 'track'}
          label="Storm path"
          title={
            canFitTrack
              ? view.kind === 'track'
                ? 'Return to the study region'
                : 'Fit the whole storm track'
              : 'Track unavailable'
          }
          disabled={!canFitTrack}
          onPress={onPressFitTrack}
        />
        <MapButton
          Icon={LegendIcon}
          active={showLegend}
          label="Legend"
          title={showLegend ? 'Hide the legend' : 'Show the legend'}
          onPress={() => setShowLegend((on) => !on)}
        />
      </View>

      {/* --- the flood area, as the model's figure, on the map itself -----
          Placed here rather than only in the side panel so the number is
          attached to the picture it describes. Labelled "(model)" because the
          raster is a display artefact and the figure is the computation.
      */}
      <View style={styles.areaTag}>
        <Text style={styles.areaTagLabel}>FLOODED LAND</Text>
        <Text style={styles.areaTagValue}>
          {floodAreaKm2 === null
            ? '—'
            : floodAreaKm2 < 1
              ? `${floodAreaKm2.toFixed(2)} km²`
              : `${Math.round(floodAreaKm2).toLocaleString('en-US')} km²`}
        </Text>
        <Text style={styles.areaTagNote}>model</Text>
      </View>

      {showLegend ? (
        <View style={styles.legend}>
          <Text style={styles.legendTitle}>Legend</Text>
          {LEGEND_ROWS.map((row) => (
            <View key={row.id} style={styles.legendRow}>
              <LegendSwatch row={row} />
              <Text style={styles.legendLabel}>{row.label}</Text>
            </View>
          ))}
          <View style={styles.legendRow}>
            <View style={styles.legendOrigin} />
            <Text style={styles.legendLabel}>Your origin</Text>
          </View>
        </View>
      ) : null}

      {/* The hover readout. Native maps get this from the platform's callout;
          SVG has no equivalent, so a single shared readout serves every
          feature rather than 251 competing ones. */}
      {hovered !== null ? (
        <View style={styles.tooltip}>
          <Text style={styles.tooltipText}>{hovered.split(':').slice(1).join(':')}</Text>
        </View>
      ) : null}

      {loading || busy ? (
        <View style={styles.busyVeil}>
          <ActivityIndicator color={theme.colors.selectedText} />
          <Text style={styles.busyText}>Updating the model…</Text>
        </View>
      ) : null}

      {/*
        The basemap disclosure. It sits on the map because that is the only
        place a reader could be misled about the geography — the numbers in
        the panel do not come from here.
      */}
      <View style={styles.disclosure} pointerEvents="none">
        <Text style={styles.disclosureText}>{BASEMAP_DISCLOSURE}</Text>
      </View>
    </View>
  );
}

/**
 * One legend swatch, shaped by `kind`.
 *
 * `flood` is a filled box with a stroke because the flood layer is a fill with
 * an edge; `dash` is drawn as segments rather than a solid box because on this
 * platform the dash is honoured, so the shape carries the meaning; `path` is a
 * dashed bar for the track, which is dashed on Web. Each is drawn in the
 * resolved colour and nothing is retyped.
 */
function LegendSwatch({ row }: { row: (typeof LEGEND_ROWS)[number] }) {
  const colour = row.fillSource ? SWATCHES[row.fillSource] : theme.colors.flood;
  if (row.kind === 'flood') {
    return (
      <View
        style={[
          styles.swatch,
          { backgroundColor: colour, borderColor: SWATCHES['theme.water'] },
        ]}
      />
    );
  }
  if (row.kind === 'pin') {
    return <View style={[styles.swatchDot, { backgroundColor: colour }]} />;
  }
  return (
    <View style={styles.swatchDash}>
      <View style={[styles.swatchDashBar, { backgroundColor: colour }]} />
      <View style={[styles.swatchDashBar, { backgroundColor: colour }]} />
    </View>
  );
}

/**
 * A square map button carrying a real icon.
 *
 * **This drew the letters S / T / L until 2026-10-01.** They were placeholders
 * standing in for icons that were never drawn — `ControlIcon.tsx` on the native
 * side does the same thing with two `View`s, because `@expo/vector-icons` does
 * not resolve in this SDK (MEMORY.md §44). On Web there is no such limitation,
 * so these are **inline SVG paths drawn here** rather than a font: no new
 * dependency, and their colour is passed in from the button's own state so
 * the active and hover states cannot drift apart.
 *
 * `Icon` is the component to render rather than an element, so `MapButton`
 * can supply the colour it computed rather than the caller having to.
 *
 * `label` is the accessible name and the tooltip; it is not drawn, which is why
 * it no longer doubles as the visible glyph.
 */
function MapButton({
  Icon,
  label,
  title,
  onPress,
  active = false,
  disabled = false,
}: {
  Icon: React.ComponentType<MapIconProps>;
  label: string;
  title: string;
  onPress: () => void;
  active?: boolean;
  disabled?: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  /**
   * The icon's colour, taken from the same states that change the button's
   * background, so the glyph always contrasts with what is behind it.
   *
   * Passed as an explicit prop rather than relying on a `color` entry in a
   * `View` style: RN's `ViewStyle` type has no `color` key, and on the Web
   * build it would need a cast to `WebOnly`. An explicit prop is checked by
   * `tsc` and cannot silently fall out of step with the button's own states.
   */
  const iconColour = active || (hovered && !disabled)
    ? theme.colors.selectedText
    : theme.colors.onWhite;

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={label}
      // `title` and the hover handlers are browser/RNW props the native types
      // do not declare. See `WebOnly`.
      {...({ title, onHoverIn: () => setHovered(true), onHoverOut: () => setHovered(false) } as WebOnly)}
      // Hover is a real state on Web with no native analogue, which is why this
      // control is defined in the .web file rather than shared with `MapControl`.
      style={({ pressed }: { pressed: boolean }) => [
        styles.mapButton,
        active && styles.mapButtonActive,
        hovered && !disabled && styles.mapButtonHover,
        pressed && !disabled && styles.pressed,
        disabled && styles.mapButtonDisabled,
      ]}
    >
      <Icon colour={iconColour} />
    </Pressable>
  );
}

/** The props every inline map icon takes. */
interface MapIconProps {
  /** Stroke and fill colour. Named `colour` to avoid colliding with SVG's own
   *  `color` presentation attribute in the spread props below. */
  colour: string;
}

/**
 * Three inline SVG icons, drawn on a 24x24 grid.
 *
 * Inline rather than a font or an icon package: no dependency, no network
 * request at render time, and they scale with the button without a second
 * asset. Each takes its colour as a prop so it always contrasts with the
 * button's current background.
 */
function iconProps(colour: string) {
  return {
    width: 20,
    height: 20,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: colour,
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
}

/** Concentric target: the study region. */
function TargetIcon({ colour }: MapIconProps) {
  return (
    <svg {...iconProps(colour)} accessibility-hidden="true">
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="2.5" fill={colour} stroke="none" />
      <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3" />
    </svg>
  );
}

/** A dashed path with waypoints: the observed storm track. */
function TrackIcon({ colour }: MapIconProps) {
  return (
    <svg {...iconProps(colour)} accessibility-hidden="true">
      <path d="M3 18c3 0 3-5 6-5s3 5 6 5 3-8 6-8" strokeDasharray="3 2.5" />
      <circle cx="3" cy="18" r="1.8" fill={colour} stroke="none" />
      <circle cx="21" cy="10" r="1.8" fill={colour} stroke="none" />
    </svg>
  );
}

/** Stacked rows: the legend. */
function LegendIcon({ colour }: MapIconProps) {
  return (
    <svg {...iconProps(colour)} accessibility-hidden="true">
      <rect x="3" y="4" width="18" height="3.5" rx="1" />
      <rect x="3" y="10.2" width="18" height="3.5" rx="1" />
      <rect x="3" y="16.4" width="18" height="3.5" rx="1" />
    </svg>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flex: 1,
    minHeight: 460,
    backgroundColor: theme.colors.background,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    overflow: 'hidden',
  },
  svg: {
    width: '100%',
    height: '100%',
  },
  controls: {
    position: 'absolute',
    right: 14,
    // Also above the strip, for the same reason as the legend.
    bottom: DISCLOSURE_STRIP_HEIGHT + 8,
    gap: 8,
  },
  mapButton: {
    // 44 px: the touch-target floor. 40 px was under it on a phone, where
    // these are the only zoom controls the Web map has.
    width: 44,
    height: 44,
    borderRadius: theme.radius.button,
    backgroundColor: theme.colors.white,
    borderWidth: 1,
    borderColor: theme.colors.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  mapButtonActive: {
    borderColor: theme.colors.selectedText,
    borderWidth: 2,
  },
  mapButtonHover: {
    backgroundColor: theme.colors.selectedFill,
  },
  mapButtonDisabled: {
    opacity: 0.4,
  },
  pressed: {
    opacity: 0.7,
  },
  areaTag: {
    position: 'absolute',
    top: 12,
    left: 12,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    paddingHorizontal: 12,
    paddingVertical: 8,
    alignItems: 'flex-start',
  },
  areaTagLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 10,
    letterSpacing: 1.2,
    color: theme.colors.textMuted,
  },
  areaTagValue: {
    fontFamily: theme.fonts.heading,
    fontSize: 20,
    color: theme.colors.text,
    marginTop: 2,
  },
  areaTagNote: {
    fontFamily: theme.fonts.body,
    fontSize: 10,
    color: theme.colors.textMuted,
  },
  legend: {
    position: 'absolute',
    left: 12,
    // Above the disclosure strip. See DISCLOSURE_STRIP_HEIGHT.
    bottom: DISCLOSURE_STRIP_HEIGHT + 8,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    paddingHorizontal: 11,
    paddingVertical: 9,
    gap: 5,
    maxWidth: 190,
  },
  legendTitle: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: 10,
    letterSpacing: 1.1,
    color: theme.colors.textMuted,
    marginBottom: 1,
  },
  legendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  legendLabel: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.text,
  },
  swatch: {
    width: 14,
    height: 11,
    borderRadius: 2,
    borderWidth: 1,
  },
  swatchDot: {
    width: 9,
    height: 9,
    borderRadius: 5,
  },
  swatchDash: {
    flexDirection: 'row',
    gap: 3,
    alignItems: 'center',
  },
  swatchDashBar: {
    width: 6,
    height: 3,
    borderRadius: 2,
  },
  legendOrigin: {
    width: 11,
    height: 11,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: theme.colors.selectedText,
  },
  tooltip: {
    position: 'absolute',
    top: 12,
    right: 12,
    maxWidth: 240,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    paddingHorizontal: 10,
    paddingVertical: 7,
  },
  tooltipText: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.text,
  },
  busyVeil: {
    ...StyleSheet.absoluteFill,
    backgroundColor: 'rgba(9,9,9,0.62)',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
  },
  busyText: {
    fontFamily: theme.fonts.body,
    fontSize: 12,
    color: theme.colors.text,
  },
  disclosure: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(9,9,9,0.9)',
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  disclosureText: {
    fontFamily: theme.fonts.body,
    fontSize: 10,
    lineHeight: 14,
    color: theme.colors.textMuted,
  },
});
