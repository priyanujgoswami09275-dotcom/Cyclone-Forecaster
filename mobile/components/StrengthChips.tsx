import React from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { STRENGTH_CHIPS, figuresLine, type ChipId } from '../strengthChips';
import type { OverlayEntry } from '../api';

/**
 * Step 1 of the panel: the four storm strengths, as a chip row.
 *
 * This replaces the seven-band slider. The reasoning is in `strengthChips.ts`
 * and is worth repeating here because it is a *demo* decision the reader will
 * feel: five of the seven bands expose no infrastructure at all, so the slider
 * spent four fifths of its travel on positions that render an empty map. Four
 * chips is every position where something happens.
 *
 * **The chips are a horizontal `ScrollView`, not a wrapping row.** "Super
 * cyclonic" is the longest label and the row is wider than a phone at 4 chips
 * with these paddings; a wrap would reflow the row's height between selections
 * and shove the figures line below it, which is the one line on this screen
 * that must not move when you change strength.
 *
 * The figures line is the screen's most-honest sentence and the reason
 * `figuresLine` lives in the tested module rather than here: it prints "≥" only
 * when the backend says the wind is a band floor, and it says "(model)" because
 * the km² comes from the computation and not from the picture above it.
 */
export interface StrengthChipsProps {
  selected: ChipId;
  onSelect: (id: ChipId) => void;
  /** The selected chip's overlay, or null while the index loads. */
  overlay: OverlayEntry | null;
  /** `wind_is_band_midpoint` for the selected chip's category. */
  windIsBandMidpoint: boolean | undefined;
  /** The IMD band name to disclose when the preset borrows a band's figures. */
  borrowedCategory: string | null;
}

export function StrengthChips({
  selected,
  onSelect,
  overlay,
  windIsBandMidpoint,
  borrowedCategory,
}: StrengthChipsProps) {
  const line = figuresLine(overlay, windIsBandMidpoint);

  return (
    <View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
        accessibilityRole="radiogroup"
      >
        {STRENGTH_CHIPS.map((chip) => {
          const active = chip.id === selected;
          return (
            <Pressable
              key={chip.id}
              onPress={() => onSelect(chip.id)}
              accessibilityRole="radio"
              accessibilityState={{ selected: active }}
              // The band name is announced rather than the abbreviation, so a
              // screen reader says "Extremely Severe Cyclonic Storm" where the
              // chip says "Extreme".
              accessibilityLabel={chip.label}
              style={({ pressed }) => [
                styles.chip,
                active ? styles.chipActive : styles.chipIdle,
                pressed ? styles.chipPressed : null,
              ]}
            >
              <Text style={[styles.chipLabel, active ? styles.chipLabelActive : null]}>
                {chip.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {/*
        The loading state is a spinner in the figures line's own space, not a
        blank. A blank line under four chips reads as "no data for this storm",
        which is a different claim from "not fetched yet" — and the first is
        false for two of the four chips while the index is in flight.
      */}
      {line === '' ? (
        <View style={styles.figuresRow}>
          <ActivityIndicator size="small" color={theme.colors.textMuted} />
          <Text style={styles.figures}>Loading figures…</Text>
        </View>
      ) : (
        <Text style={styles.figures}>{line}</Text>
      )}

      {/*
        The borrow disclosure. The preset is a real observed event at 115 kmph,
        which is not any band's midpoint, so every category-driven figure in
        this app has to borrow the nearest band. Saying so is the difference
        between "these are Remal's numbers" and "these are the nearest band's
        numbers, and Remal was between two of them".
      */}
      {borrowedCategory ? (
        <Text style={styles.borrowNote}>
          Counts below are for the nearest IMD band ({borrowedCategory}), not for Remal's own
          115 km/h. The flood layer is Remal's.
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  chip: {
    borderRadius: theme.radius.chip,
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: theme.spacing.xs / 2,
    marginRight: theme.spacing.xs,
    borderWidth: 1,
  },
  chipIdle: {
    // Dark theme: an unselected chip is a `card` block outlined in `border`,
    // not a `primary` fill. The selected state below then reads as *filled*
    // rather than merely tinted.
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
  },
  chipActive: {
    // `selectedFill` (#011132) — darker than the page, per the design — with a
    // `primary` border so the selection still reads as the app's accent.
    backgroundColor: theme.colors.selectedFill,
    borderColor: theme.colors.primary,
  },
  chipPressed: {
    opacity: 0.7,
  },
  chipLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  chipLabelActive: {
    fontFamily: theme.fonts.bodySemibold,
    color: theme.colors.selectedText,
  },
  figuresRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  figures: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginTop: theme.spacing.xs,
  },
  borrowNote: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.caution,
    marginTop: theme.spacing.xs / 2,
  },
});
