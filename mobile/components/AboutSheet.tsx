import React from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type ViewStyle,
} from 'react-native';

import { theme } from '../theme';

/**
 * "About this estimate" — the sheet behind the caption at the foot of the panel.
 *
 * Three things live here and they are three different kinds of claim, which is
 * why they are separated rather than run together into one paragraph:
 *
 *   1. **The limitation**, carried verbatim from the backend's `limitation`
 *      field. Never paraphrased here: a paraphrase is a place for the two to
 *      drift, and this sentence is the model's own description of itself.
 *   2. **The raster note** — that the blue layer is a display image quantised
 *      into four depth classes, and that the km² figure is the model's, not
 *      measured off the picture. This is the disclosure that used to sit in
 *      `ReadoutPanel` above the exposure list; the redesign moved it here
 *      because it qualifies a *number*, and the tiles now show the counts
 *      without it.
 *   3. **The track source**, and specifically the wind-unit discrepancy.
 *
 * **The JTWC note is the important one, and it is a real disagreement in the
 * source data rather than a caveat we invented.** The committed track's wind
 * column is `usa_wind_kt` — the IBTrACS *USA* column, which is JTWC's
 * estimate, and JTWC reports a **1-minute** mean wind where IMD reports a
 * **3-minute** one. Those are not the same quantity: for the same storm at the
 * same moment the 1-minute figure is routinely the higher of the two. Remal's
 * peak in the committed track is 54 kt ≈ 100 km/h, against IMD's published
 * landfall figure of 110–120 km/h. A reader who put the track's peak next to
 * the app's own `≥222 km/h` chip would otherwise conclude the app's model
 * exceeds anything observed, which is not a comparison the two numbers support.
 *
 * So the sheet says it in the app's own voice: these are different averaging
 * periods, not a contradiction, and IMD's figure is the one to quote for
 * Remal itself. `peakTrackWindKmph` is computed from the payload rather than
 * typed in, so the sentence cannot go stale if the track file is re-fetched.
 */

/** IMD's published landfall wind for Remal, the figure the 1-minute one is not. */
export const IMD_LANDFALL_KMPH = '110–120 km/h';

export interface AboutSheetProps {
  visible: boolean;
  onClose: () => void;
  /** Verbatim from `/categories` — the model's own limitation. */
  limitation: string;
  /** Raster width in px, for the disclosure's precision. */
  overlayWidthPx: number | null;
  /** How many depth classes the raster is quantised into. */
  depthClasses: number | null;
  /** The model's flooded area, formatted, or null if unknown. */
  areaKm2: string | null;
  /** The track's `source` string, e.g. "IBTrACS v04r00". */
  trackSource: string | null;
  /** The track's name and season, e.g. "REMAL 2024". */
  trackLabel: string | null;
  /** Peak reported wind in the track, in km/h, or null if none was reported. */
  peakTrackWindKmph: number | null;
  /** How many fixes carry no reported wind — not the same as calm. */
  unreportedWindCount: number;
}

export function AboutSheet({
  visible,
  onClose,
  limitation,
  overlayWidthPx,
  depthClasses,
  areaKm2,
  trackSource,
  trackLabel,
  peakTrackWindKmph,
  unreportedWindCount,
}: AboutSheetProps) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      // Tapping the scrim closes. Android's hardware back button does the same
      // via `onRequestClose`, which `Modal` requires on Android anyway — a
      // sheet with no back handler traps the user on a phone.
      onRequestClose={onClose}
    >
      <Pressable style={styles.scrim} onPress={onClose} accessibilityLabel="Close" />
      <View style={styles.sheet}>
        <View style={styles.headerRow}>
          <Text style={styles.title}>About this estimate</Text>
          <Pressable
            onPress={onClose}
            accessibilityRole="button"
            accessibilityLabel="Close"
            hitSlop={12}
            style={({ pressed }) => [styles.close, pressed ? styles.closePressed : null]}
          >
            <Text style={styles.closeLabel}>✕</Text>
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
          <Block heading="What this number is">
            <Text style={styles.para}>{limitation}</Text>
          </Block>

          <Block heading="The blue layer is a picture, not the measurement">
            <Text style={styles.para}>
              The flood on the map is a {overlayWidthPx ? `${overlayWidthPx} px` : 'pre-rendered'}{' '}
              image, downsampled and quantised into {depthClasses ?? 4} depth classes.{' '}
              {areaKm2
                ? `The ${areaKm2} km² quoted on screen is the model’s own figure`
                : 'The area quoted on screen is the model’s own figure'}{' '}
              — it is not measured off the picture, and the two do not agree exactly.
            </Text>
          </Block>

          <Block heading="The storm path">
            <Text style={styles.para}>
              {trackLabel ?? 'This track'}
              {trackSource ? `, from ${trackSource}` : ''}. Positions are 3-hourly best-track
              fixes — a record of what happened, not a forecast and not a prediction for any
              other storm.
            </Text>
            <Text style={styles.para}>
              {/*
                The wind sentence is conditional on a peak actually being
                present. Five of the committed track's 19 fixes report no wind
                at all — the IBTrACS source writes a blank `USA_WIND` as 0.0 —
                so a sheet that said "peaks at 0 km/h" because the column was
                blank would be the exact error the endpoint's disclosure warns
                against. The count is passed in, not written here, so it tracks
                the payload rather than this comment.
              */}
              {peakTrackWindKmph === null
                ? 'No wind is reported in this track.'
                : `Its strongest reported fix is ${Math.round(peakTrackWindKmph)} km/h.`}{' '}
              That figure is a <Text style={styles.emphasis}>1-minute</Text> mean wind from JTWC
              (the IBTrACS <Text style={styles.mono}>USA</Text> column), not IMD's 3-minute mean,
              so it is not directly comparable with the {IMD_LANDFALL_KMPH} IMD publishes for
              Remal's landfall. Quote IMD's figure for Remal.
            </Text>
            {unreportedWindCount > 0 ? (
              <Text style={styles.para}>
                {unreportedWindCount} of these fixes report no wind at all, which is not the
                same as zero wind.
              </Text>
            ) : null}
          </Block>
        </ScrollView>
      </View>
    </Modal>
  );
}

function Block({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <View style={styles.block}>
      <Text style={styles.blockHeading}>{heading}</Text>
      {children}
    </View>
  );
}

/**
 * `position: absolute` on all four edges, as a plain object.
 *
 * `StyleSheet.absoluteFillObject` is the usual spelling, but this version of
 * react-native's types export only `absoluteFill`, and that one is a registered
 * style *id* — spreading it here would put the number `2` into the style
 * object. The four properties are the documented definition and cannot drift.
 */
const ABSOLUTE_FILL: ViewStyle = {
  position: 'absolute',
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
};

const styles = StyleSheet.create({
  scrim: {
    // Spread by hand rather than with `StyleSheet.absoluteFillObject`, which
    // this version of `react-native`'s types do not export (it has
    // `absoluteFill` only). Spreading is also safer inside `StyleSheet.create`,
    // where a registered style id would otherwise be spread as a number.
    ...ABSOLUTE_FILL,
    backgroundColor: 'rgba(0, 0, 0, 0.6)',
  },
  sheet: {
    // Bottom-anchored and capped, so a long sheet scrolls rather than running
    // off the top of the screen. `maxHeight: 80%` rather than a fixed height:
    // the three blocks differ in length with the payload, and a fixed sheet
    // would either clip or float with a void under it.
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    maxHeight: '80%',
    backgroundColor: theme.colors.card,
    borderTopLeftRadius: theme.radius.card,
    borderTopRightRadius: theme.radius.card,
    borderTopWidth: 1,
    borderColor: theme.colors.border,
    paddingHorizontal: theme.spacing.sm,
    paddingTop: theme.spacing.sm,
    paddingBottom: theme.spacing.lg,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: theme.spacing.xs,
  },
  title: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  close: {
    padding: 2,
  },
  closePressed: {
    opacity: 0.6,
  },
  closeLabel: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  body: {
    paddingBottom: theme.spacing.xs,
  },
  block: {
    marginBottom: theme.spacing.sm,
  },
  blockHeading: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: theme.spacing.xs / 2,
  },
  para: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginBottom: theme.spacing.xs / 2,
  },
  emphasis: {
    fontFamily: theme.fonts.bodySemibold,
  },
  mono: {
    fontFamily: theme.fonts.bodySemibold,
  },
});
