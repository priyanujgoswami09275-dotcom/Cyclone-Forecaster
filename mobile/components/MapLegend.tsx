import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { LEGEND_ROWS, type LegendSwatchKind, type SwatchSource } from '../legend';
import { theme } from '../theme';
import {
  assetPinColours,
  compromisedRoadStyle,
  floodPolygonStyle,
  trackLineStyle,
} from './mapStyles';

/**
 * The map legend — the five things a reader can see on the map.
 *
 * **The rows come from `mobile/legend.ts`, and the colours are resolved here
 * and only here.** That is the arrangement that makes the legend incapable of
 * drifting from the map: `SwatchSource` in `legend.ts` names the constant a row
 * must be drawn in, and this file is the single place that turns a name into a
 * value — resolving each one to the same `mapStyles.ts` / `theme` export the
 * `<Polyline>` and `<Marker>` components are drawn from. Retune a token and
 * both the map and its key move together, because there is only one expression
 * of each colour in the app.
 *
 * The alternative — reading the colours inside `legend.ts` — is not available
 * anyway, and the reason is worth keeping: `theme.ts` and `mapStyles.ts` are
 * imported extensionless (as Metro requires) and Node's ESM resolver will not
 * follow that, so a module reaching for either could not be loaded by
 * `node --test`. The name/value split is what leaves the data testable.
 *
 * Every swatch is a shape that matches how the layer is really drawn: a filled
 * block for the raster (`floodPolygonStyle`'s own fill, stroke and opacity), a
 * pin for each point asset, a *dashed* bar for cut-off roads, and a solid bar
 * for the storm path.
 *
 * Positioned bottom-left, opposite the storm-path control at bottom-right, and
 * clear of the first-open card at top. All three are `position: absolute`
 * siblings inside the same map wrapper, and this is the only one that claims
 * the left edge.
 */
export function MapLegend() {
  return (
    <View style={styles.card}>
      <Text style={styles.title}>Legend</Text>
      {LEGEND_ROWS.map((row) => (
        <View key={row.id} style={styles.row}>
          <View style={styles.swatchSlot}>
            <Swatch kind={row.kind} fill={resolve(row.fillSource)} stroke={resolve(row.strokeSource)} />
          </View>
          <Text style={styles.rowLabel}>{row.label}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * Turn a `SwatchSource` into the colour it names.
 *
 * A `switch` over the literal names rather than a dynamic property walk,
 * because a dynamic walk (`obj[a.b].c`) would be shorter and would type-check
 * as `any` — which is exactly the wrong property for the one function whose
 * whole job is to be right about which constant a layer is drawn in. A typo in
 * a name falls through to `null` here, which the caller renders as a muted
 * swatch: visibly wrong, rather than silently transparent.
 */
function resolve(source: SwatchSource | undefined | null): string | null {
  switch (source) {
    case 'theme.flood':
      return theme.colors.flood;
    case 'theme.water':
      return theme.colors.water;
    case 'mapStyles.assetPinColours.hospital':
      return assetPinColours.hospital;
    case 'mapStyles.assetPinColours.substation':
      return assetPinColours.substation;
    case 'mapStyles.compromisedRoadStyle.strokeColor':
      return compromisedRoadStyle.strokeColor;
    case 'mapStyles.trackLineStyle.strokeColor':
      return trackLineStyle.strokeColor;
    default:
      return null;
  }
}

/**
 * One swatch, chosen by kind.
 *
 * A `switch` rather than a lookup because the four shapes are not
 * interchangeable — a pin and a dash drawn the same way would make the legend
 * lie about which layers are pins and which are lines.
 */
function Swatch({
  kind,
  fill,
  stroke,
}: {
  kind: LegendSwatchKind;
  fill: string | null;
  stroke: string | null;
}) {
  // Only the flood row is ever null in practice, since every other row names a
  // fill source. The fallbacks exist so an unresolved name is visible rather
  // than invisible.
  const fillColour = fill ?? theme.colors.textMuted;
  const strokeColour = stroke ?? theme.colors.textMuted;

  switch (kind) {
    case 'flood':
      return (
        <View
          style={[
            styles.floodSwatch,
            {
              backgroundColor: fillColour,
              borderColor: strokeColour,
              // The layer's own opacity, so the key is the same pale blue the
              // polygon is and not a more saturated one.
              opacity: floodPolygonStyle.fillOpacity,
            },
          ]}
        />
      );
    case 'pin':
      // The markers on the map are native pins, not dots, so a filled circle
      // of the same colour is the closest honest swatch without shipping a
      // bitmap.
      return <View style={[styles.pin, { backgroundColor: fillColour }]} />;
    case 'dash':
      // Three segments rather than a striped box: the dash is the one row where
      // the shape carries meaning, not just the colour. `lineDashPattern` is
      // not honoured by the Android renderer, so the map shows this solid there
      // — see `compromisedRoadDashPattern`.
      return (
        <View style={styles.dashRow}>
          {[0, 1, 2].map((i) => (
            <View key={i} style={[styles.dashSegment, { backgroundColor: fillColour }]} />
          ))}
        </View>
      );
    case 'path':
      return <View style={[styles.pathBar, { backgroundColor: fillColour }]} />;
    default:
      return null;
  }
}

const styles = StyleSheet.create({
  card: {
    position: 'absolute',
    left: theme.spacing.xs,
    bottom: theme.spacing.xs,
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    paddingVertical: theme.spacing.xs,
    paddingHorizontal: theme.spacing.xs,
    ...theme.shadow.card,
  },
  title: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: theme.spacing.xs / 2,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 2,
  },
  swatchSlot: {
    // Fixed width so all five labels left-align on the same edge, which is
    // what makes a legend scannable rather than ragged.
    width: theme.spacing.lg,
    alignItems: 'center',
    marginRight: theme.spacing.xs / 2,
  },
  rowLabel: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  floodSwatch: {
    // The raster is quantised into four depth classes, so a single block is the
    // honest simplification here rather than a gradient the map never draws.
    width: theme.spacing.md,
    height: theme.spacing.xs,
    borderWidth: 1,
  },
  pin: {
    width: theme.spacing.xs + 2,
    height: theme.spacing.xs + 2,
    borderRadius: theme.radius.chip,
  },
  dashRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dashSegment: {
    width: theme.spacing.xs - 2,
    height: 2,
    marginRight: 2,
  },
  pathBar: {
    width: theme.spacing.lg,
    height: 3,
  },
});
