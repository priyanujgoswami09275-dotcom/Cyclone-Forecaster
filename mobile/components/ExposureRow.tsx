import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

/**
 * Exposure list row — Design.md: "`card` background, small colored dot —
 * `danger` if affected, `textMuted` if not".
 *
 * One row per hospital / substation / road. The data shape is owned by the
 * exposure endpoint (Module C); this component only renders what it is
 * given, so it stays valid whatever that response ends up looking like.
 */
export interface ExposureRowProps {
  /** Asset name, e.g. a hospital or road name from OSM. */
  name: string;
  /** Secondary line — locality, asset class, or why it is exposed. */
  detail?: string;
  /** Drives the dot colour: affected -> danger, unaffected -> textMuted. */
  affected: boolean;
}

export function ExposureRow({ name, detail, affected }: ExposureRowProps) {
  return (
    <View style={styles.row}>
      <View
        style={[styles.dot, { backgroundColor: affected ? theme.colors.danger : theme.colors.textMuted }]}
      />
      <View style={styles.textColumn}>
        <Text style={styles.name}>{name}</Text>
        {detail ? <Text style={styles.detail}>{detail}</Text> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: theme.spacing.xs,
    marginBottom: theme.spacing.xs,
  },
  dot: {
    // Design.md says "small colored dot" without a size; spacing.xs is the
    // base 8px unit, reused here rather than introducing a new value.
    width: theme.spacing.xs,
    height: theme.spacing.xs,
    borderRadius: theme.radius.chip,
    marginRight: theme.spacing.xs,
  },
  textColumn: {
    flex: 1,
  },
  name: {
    fontFamily: theme.fonts.bodySemibold,
    color: theme.colors.text,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
  detail: {
    fontFamily: theme.fonts.body,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
});
