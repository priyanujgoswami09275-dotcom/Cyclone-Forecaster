import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import SliderNative, { type SliderProps } from '@react-native-community/slider';

import { theme } from '../theme';
import { GhostButton } from './GhostButton';

/**
 * `@react-native-community/slider@5.2.0` ships class-component typings
 * (`declare class Slider extends SliderBase`, where `SliderBase` is an
 * intersection of a native constructor and that class). React 19's
 * `JSX.ElementType` check cannot see through the intersection and rejects it
 * as "not a valid JSX element type".
 *
 * This is a defect in the library's published types, not a misuse of them,
 * and the fix upstream is a version bump rather than anything on our side. The
 * cast is therefore confined to this one import boundary and immediately
 * re-typed with the library's own `SliderProps` — so every prop below is
 * still fully checked, and `onValueChange`/`onSlidingComplete` are no longer
 * implicit `any`. Removing this line is a drop-in change if the package ever
 * ships function-component typings.
 */
const Slider = SliderNative as unknown as React.FC<SliderProps>;

/**
 * The intensity picker: a slider over the IMD bands, plus the case-study
 * preset beside it.
 *
 * Two decisions live here, and both are load-bearing for the demo.
 *
 * **The bands are never hardcoded.** `count` comes from `/categories`, so if
 * IMD republishes the table, or the backend adds a band, the slider grows with
 * it. The last session hardcoded 0-6 in a dozen places and a band change would
 * have silently desynced every one of them.
 *
 * **Fetching happens on `onSlidingComplete`, never on `onValueChange`.**
 * `onValueChange` fires continuously while a thumb is moving — dozens of times
 * across one drag — and each of those would be a request. Dragging the slider
 * is the most natural way to explore this product and also the easiest way to
 * fire 40 requests, so the two are deliberately separated: `onValueChange`
 * moves the *label* (instant, local, free) and `onSlidingComplete` commits
 * the *data* (one request, on release).
 *
 * Design.md: "track in `border`, filled portion + thumb in `primary`".
 */
export interface IntensityControlProps {
  /** Number of bands, from `/categories`. The slider is 0 .. count-1. */
  count: number;
  /** Committed band index. Drives the map and the exposure fetch. */
  value: number;
  /** Thumb position while dragging — may be ahead of `value` mid-drag. */
  draftValue: number;
  onDraftChange: (next: number) => void;
  /** Fires once, on release. */
  onCommit: (next: number) => void;
  /** Label of the current band, shown under the thumb. */
  bandLabel: string;
  /** Wind in km/h for the current selection. */
  windKmph: number;
  /** The preset button, or null when the backend offered none. */
  preset: { id: string; label: string } | null;
  presetActive: boolean;
  onPressPreset: () => void;
  disabled?: boolean;
}

export function IntensityControl({
  count,
  value,
  draftValue,
  onDraftChange,
  onCommit,
  bandLabel,
  windKmph,
  preset,
  presetActive,
  onPressPreset,
  disabled,
}: IntensityControlProps) {
  // An empty /categories response would otherwise give a 0..-1 slider that
  // throws inside the native view. Nothing to pick is a real state (the
  // endpoint can fail) and it renders as an inert control.
  const usable = count > 1 && !disabled;

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <View style={styles.headerText}>
          <Text style={styles.heading}>Storm intensity</Text>
          <Text style={styles.readout} numberOfLines={1}>
            {bandLabel} · {windKmph.toFixed(0)} kmph
          </Text>
        </View>
        {preset ? (
          <View style={styles.presetWrap}>
            <GhostButton
              label={presetActive ? '● ' + preset.label : preset.label}
              onPress={onPressPreset}
              disabled={disabled}
            />
          </View>
        ) : null}
      </View>

      <Slider
        style={styles.slider}
        minimumValue={0}
        maximumValue={Math.max(1, count - 1)}
        step={1}
        value={draftValue}
        onValueChange={(next) => onDraftChange(Math.round(next))}
        onSlidingComplete={(next) => onCommit(Math.round(next))}
        minimumTrackTintColor={theme.colors.primary}
        maximumTrackTintColor={theme.colors.border}
        thumbTintColor={theme.colors.primary}
        disabled={!usable}
        accessibilityLabel="Cyclone intensity, IMD category"
        accessibilityValue={{
          min: 0,
          max: Math.max(1, count - 1),
          now: draftValue,
          text: `${bandLabel}, ${windKmph.toFixed(0)} kilometres per hour`,
        }}
      />

      <Text style={styles.hint}>
        {presetActive
          ? 'Showing the preset’s own flood raster. Exposure counts are for the nearest IMD band — see the note below.'
          : 'Release to load. Dragging does not fetch.'}
      </Text>
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
    ...theme.shadow.card,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerText: {
    flex: 1,
    marginRight: theme.spacing.xs,
  },
  heading: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.heading,
    color: theme.colors.text,
  },
  readout: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.body,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
  presetWrap: {
    maxWidth: '48%',
  },
  slider: {
    width: '100%',
    height: theme.spacing.lg,
    marginTop: theme.spacing.xs,
  },
  hint: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
});
