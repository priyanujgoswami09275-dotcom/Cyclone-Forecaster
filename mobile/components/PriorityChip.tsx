import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

/**
 * Priority chip — the four `DistrictAdvisory` levels, as a severity ramp.
 *
 * **The ramp is two colours, not four, and the mapping changed with the dark
 * theme.** The old light palette carried `dangerDark` and `safe` precisely so
 * CRITICAL/HIGH/MEDIUM/LOW could each have their own fill. Neither token
 * survived into the dark palette — the dark design supplies one danger red and
 * one caution amber — so this maps what exists rather than inventing two more:
 *
 *   | Level    | Fill       | Why                                                        |
 *   |----------|------------|------------------------------------------------------------|
 *   | CRITICAL | `danger`   | The red means "act now" and nothing else may use it.        |
 *   | HIGH     | `danger`   | Same red, at `textMuted` weight on the label. See below.    |
 *   | MEDIUM   | `caution`  | Marigold, the amber the map already uses for a substation. |
 *   | LOW      | `border`   | Recedes. A near-black block with muted text.                |
 *
 * HIGH sharing CRITICAL's fill is a real loss of resolution and is deliberate:
 * **rank is carried by the label's weight, not only by the fill.** Two reds
 * that differ by opacity would be indistinguishable on a phone in daylight, so
 * the distinction is made in something the eye can actually resolve.
 *
 * This is logged in MEMORY.md "Flagged for review" — if the human wants four
 * distinct fills, that is two new tokens in `theme.ts` plus a Design.md row,
 * not a change to this file.
 *
 * The chip is presentational and total: the four levels are the only legal
 * inputs, so a typo is a compile error rather than an unstyled chip.
 */
export type PriorityLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

const CHIP_BACKGROUND: Record<PriorityLevel, string> = {
  CRITICAL: theme.colors.danger,
  HIGH: theme.colors.danger,
  MEDIUM: theme.colors.caution,
  LOW: theme.colors.border,
};

/** HIGH is de-emphasised by its label, not by a second red. */
const LABEL_WEIGHT: Record<PriorityLevel, 'bodySemibold' | 'bodyMedium'> = {
  CRITICAL: 'bodySemibold',
  HIGH: 'bodyMedium',
  MEDIUM: 'bodySemibold',
  LOW: 'bodyMedium',
};

/**
 * Label colour per level, and it is not one value.
 *
 * `danger` and `caution` are both light enough for near-black text, which is
 * what the old palette's flat `card` white-on-light convention gave for free.
 * `border` (#3d3d3c) is not — near-black on it is a contrast failure, so LOW
 * takes `text` instead. Per-level, because a single value here is a legibility
 * bug on exactly one of the four levels, which is the kind of bug nobody
 * reports because the other three look fine.
 */
const LABEL_COLOUR: Record<PriorityLevel, string> = {
  CRITICAL: theme.colors.background,
  HIGH: theme.colors.background,
  MEDIUM: theme.colors.background,
  LOW: theme.colors.text,
};

interface PriorityChipProps {
  level: PriorityLevel;
  /** Optional count/detail rendered after the level, e.g. "HIGH · 3 blocks". */
  detail?: string;
}

export function PriorityChip({ level, detail }: PriorityChipProps) {
  return (
    <View style={[styles.chip, { backgroundColor: CHIP_BACKGROUND[level] }]}>
      <Text
        style={[
          styles.label,
          { fontFamily: theme.fonts[LABEL_WEIGHT[level]], color: LABEL_COLOUR[level] },
        ]}
      >
        {level}
      </Text>
      {detail ? (
        <Text style={[styles.detail, { color: LABEL_COLOUR[level] }]}>{detail}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    borderRadius: theme.radius.chip,
    paddingHorizontal: theme.spacing.xs,
    paddingVertical: theme.spacing.xs / 2,
  },
  label: {
    // Font family and colour are per-level and applied inline from
    // LABEL_WEIGHT / LABEL_COLOUR; the two severities that share a fill differ
    // only there. No static fontSize — see MEMORY.md "Flagged for review".
  },
  detail: {
    fontFamily: theme.fonts.body,
    marginLeft: theme.spacing.xs / 2,
    // Colour is per-level, inline, for the same reason as the label.
  },
});
