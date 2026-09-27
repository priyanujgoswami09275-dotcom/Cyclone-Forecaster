import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

/**
 * Priority chip — Design.md: "pill shape, background
 * `danger`/`dangerDark`/`caution`/`safe` respectively, white text".
 *
 * The chip is presentational and total: the four `DistrictAdvisory`
 * priority levels are the only legal inputs, so a typo is a compile error
 * rather than an unstyled chip at runtime.
 */
export type PriorityLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

const CHIP_BACKGROUND: Record<PriorityLevel, string> = {
  CRITICAL: theme.colors.danger,
  HIGH: theme.colors.dangerDark,
  MEDIUM: theme.colors.caution,
  LOW: theme.colors.safe,
};

interface PriorityChipProps {
  level: PriorityLevel;
  /** Optional count/detail rendered after the level, e.g. "HIGH · 3 blocks". */
  detail?: string;
}

export function PriorityChip({ level, detail }: PriorityChipProps) {
  return (
    <View style={[styles.chip, { backgroundColor: CHIP_BACKGROUND[level] }]}>
      <Text style={styles.label}>{level}</Text>
      {detail ? <Text style={styles.detail}>{detail}</Text> : null}
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
    fontFamily: theme.fonts.bodySemibold,
    // Design.md calls for white text. There is no `white` token; `card` is
    // the same #ffffff. Mapping recorded in MEMORY.md "Flagged for review".
    color: theme.colors.card,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
  detail: {
    fontFamily: theme.fonts.body,
    color: theme.colors.card,
    marginLeft: theme.spacing.xs / 2,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
});
