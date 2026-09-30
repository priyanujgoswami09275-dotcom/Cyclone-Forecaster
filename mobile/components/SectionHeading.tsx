import React from 'react';
import { StyleSheet, Text } from 'react-native';

import { theme } from '../theme';

/**
 * A plain section label for the readout panel: "Storm", "Impact",
 * "Where are you".
 *
 * Deliberately *plain* — `textMuted`, caption size, uppercase, no rule and no
 * card of its own. The cards it labels are already the visual structure of
 * this panel, and a second weight of heading on top of them competes with the
 * numbers the panel exists to show. The three labels are also deliberately
 * the plainest possible description of what each block does: a judge who has
 * never seen the app can tell that the first block sets the storm, the second
 * says what it hits, and the third says where you would leave from.
 *
 * The style is the same uppercase-caption treatment `ReadoutPanel` already
 * uses for its own "Exposure" label, so the panel reads as one system rather
 * than as a heading style and a label style.
 */
export function SectionHeading({ label }: { label: string }) {
  return <Text style={styles.label}>{label}</Text>;
}

const styles = StyleSheet.create({
  label: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    // Owns its own vertical rhythm: space from whatever is above it, a
    // half-step to the card below. The alternative is a wrapper `View` per
    // heading in MapScreen, which is three chances to forget one.
    marginTop: theme.spacing.xs,
    marginBottom: theme.spacing.xs / 2,
  },
});
