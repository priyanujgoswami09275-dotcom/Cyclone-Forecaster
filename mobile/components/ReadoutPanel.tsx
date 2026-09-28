import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { ExposureRow } from './ExposureRow';
import type { ExposureResponse, OverlayEntry } from '../api';

/**
 * The numbers panel: what the model says, next to the picture it drew.
 *
 * This component carries most of the app's honesty burden, so the ordering of
 * its claims is deliberate. Three separate things are on screen and they must
 * not blur into each other:
 *
 *   1. **The model's figures** — surge, land flooded. Real numbers, from the
 *      full-resolution computation.
 *   2. **The picture** — a display raster, downsampled to ~150 m and quantised
 *      into four depth classes. Labelled as such, every time, because a viewer
 *      cannot tell a raster from a measurement by looking at it.
 *   3. **The model's limitation** — a screening estimate scaled from one
 *      observed event. Carried from the backend verbatim rather than
 *      paraphrased, so it cannot drift from the model it describes.
 *
 * The area figure is deliberately shown from `final_land_area_km2` and never
 * measured off the image. At category 6 the two differ by 1,002 km² — the
 * picture is not the measurement, and the label is what stops the app implying
 * that it is.
 */

/** Thousands separators without Intl — Hermes' locale support is patchy. */
function group(value: number, decimals: number): string {
  const fixed = Math.abs(value).toFixed(decimals);
  const [whole, frac] = fixed.split('.');
  const sign = value < 0 ? '-' : '';
  return sign + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (frac ? `.${frac}` : '');
}

export interface ReadoutPanelProps {
  /** The selected raster, or null while the index loads or failed. */
  overlay: OverlayEntry | null;
  /** Exposure for the committed band, or null while loading or failed. */
  exposure: ExposureResponse | null;
  loading: boolean;
  /** The band's own `note` from `/categories`, when it carries one. */
  bandNote?: string;
  /**
   * True when the preset's own wind differs from the borrowed band's. Triggers
   * the disclosure that the exposure counts are not the preset's.
   */
  borrowedBandLabel?: string;
  /** Verbatim from the backend; never paraphrased here. */
  limitation: string;
}

export function ReadoutPanel({
  overlay,
  exposure,
  loading,
  bandNote,
  borrowedBandLabel,
  limitation,
}: ReadoutPanelProps) {
  const hospitals = exposure?.hospitals.count ?? 0;
  const substations = exposure?.substations.count ?? 0;
  const roads = exposure?.roads_cut_off.count ?? 0;
  const exposed = hospitals + substations + roads;
  const areaKm2 = overlay?.final_land_area_km2 ?? 0;

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>{overlay?.label ?? 'Loading intensity…'}</Text>

      <View style={styles.figureRow}>
        <Figure
          label="Surge"
          value={overlay ? `${group(overlay.surge_m, 2)} m` : '—'}
        />
        <Figure
          label="Land flooded"
          value={overlay ? `${group(areaKm2, areaKm2 < 10 ? 1 : 0)} km²` : '—'}
        />
        <Figure
          label="Wind"
          value={overlay ? `${overlay.wind_kmph.toFixed(0)} kmph` : '—'}
        />
      </View>

      {/* (2) the picture, labelled as a picture. */}
      <View style={styles.rasterNote}>
        <Text style={styles.rasterNoteTitle}>Drawn area is a display raster</Text>
        <Text style={styles.rasterNoteBody}>
          The blue layer is a {overlay ? `${overlay.width_px} px` : '1000 px'} pre-rendered
          image, downsampled and quantised into {overlay ? overlay.depth_classes_m.length : 4}{' '}
          depth classes. The {overlay ? group(areaKm2, areaKm2 < 10 ? 1 : 0) : '0'} km² above is
          the model’s own figure — not measured from the picture.
        </Text>
      </View>

      {loading ? (
        <View style={styles.loadingRow}>
          <ActivityIndicator color={theme.colors.primary} size="small" />
          <Text style={styles.loadingText}>Loading exposure…</Text>
        </View>
      ) : (
        <View style={styles.exposureBlock}>
          <Text style={styles.sectionLabel}>Exposure</Text>
          <ExposureRow
            name="Hospitals"
            detail={hospitals ? `${hospitals} submerged` : 'none submerged'}
            affected={hospitals > 0}
          />
          <ExposureRow
            name="Substations"
            detail={substations ? `${substations} submerged` : 'none submerged'}
            affected={substations > 0}
          />
          <ExposureRow
            name="Roads cut off"
            detail={roads ? `${roads} intersecting the flood` : 'none cut off'}
            affected={roads > 0}
          />
        </View>
      )}

      {/*
        The empty state. The exact wording below is the product's, and the
        two qualifiers under it exist because the bare sentence is not the
        whole truth in either of the two ways it actually fires:

          * categories 0-3 — no water at all, because the surge is under the
            DEM's 1 m vertical resolution. The band's own `note` says so.
          * category 4 — 359 km² genuinely flood, but no hospital, substation
            or road in the study area falls inside it. Saying "no exposure"
            without that qualifier would look like a broken map, because the
            blue layer is plainly visible above it.
      */}
      {!loading && exposed === 0 ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyTitle}>No modelled exposure at this intensity</Text>
          {bandNote ? <Text style={styles.emptyBody}>{bandNote}.</Text> : null}
          {!bandNote && areaKm2 > 0 ? (
            <Text style={styles.emptyBody}>
              Water is modelled over {group(areaKm2, areaKm2 < 10 ? 1 : 0)} km² here, but no
              hospital, substation or road in the study area falls inside it.
            </Text>
          ) : null}
          {!bandNote && areaKm2 === 0 ? (
            <Text style={styles.emptyBody}>
              The surge at this intensity is below the 30 m DEM’s 1 m vertical resolution, so
              the flood model returns no inundation.
            </Text>
          ) : null}
          <Text style={styles.emptyBody}>
            Evacuation advisories are disabled because there is nothing to evacuate.
          </Text>
        </View>
      ) : null}

      {borrowedBandLabel ? (
        <Text style={styles.borrowNote}>
          These exposure counts are for the nearest IMD band ({borrowedBandLabel}), not for the
          preset’s own wind. The flood layer above is the preset’s.
        </Text>
      ) : null}

      {/* (3) the limitation, verbatim. */}
      <Text style={styles.limitation}>{limitation}</Text>
    </View>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.figure}>
      <Text style={styles.figureValue}>{value}</Text>
      <Text style={styles.figureLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: theme.spacing.sm,
  },
  heading: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.heading,
    color: theme.colors.text,
  },
  figureRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: theme.spacing.sm,
    marginBottom: theme.spacing.xs,
  },
  figure: {
    flex: 1,
  },
  figureValue: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  figureLabel: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  rasterNote: {
    borderLeftWidth: 3,
    borderLeftColor: theme.colors.water,
    paddingLeft: theme.spacing.xs,
    marginBottom: theme.spacing.xs,
  },
  rasterNoteTitle: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.water,
  },
  rasterNoteBody: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: 2,
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: theme.spacing.xs,
  },
  loadingText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginLeft: theme.spacing.xs,
  },
  exposureBlock: {
    marginTop: theme.spacing.xs,
  },
  sectionLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginBottom: theme.spacing.xs / 2,
    textTransform: 'uppercase',
  },
  emptyState: {
    marginTop: theme.spacing.sm,
    padding: theme.spacing.xs,
    borderRadius: theme.radius.button,
    backgroundColor: theme.colors.background,
    borderWidth: 1,
    borderColor: theme.colors.border,
  },
  emptyTitle: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  emptyBody: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
  borrowNote: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.caution,
    marginTop: theme.spacing.xs,
  },
  limitation: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs,
    fontStyle: 'italic',
  },
});
