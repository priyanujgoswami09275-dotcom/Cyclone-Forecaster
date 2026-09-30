import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

/**
 * The first-open card: three steps, in the order the app is meant to be used.
 *
 * **The three steps are the app's whole pitch, and they are the three things
 * a judge has to be shown.** The app is a what-if instrument, and nothing about
 * a map screen advertises that — a viewer who lands on it sees a raster and a
 * slider and no reason to touch either. So the card names the three gestures in
 * the order they make sense, and the third one is the action the pitch ends on.
 *
 * The wording is "Drag the slider to choose a cyclone strength" and not "choose
 * an intensity" because the slider is labelled in IMD bands (`Category 5`), and
 * a first-open card that used a different vocabulary than the control it
 * describes would send the reader to the slider not knowing what to look for.
 *
 * **Not persisted, and that is a deliberate limit rather than an oversight.**
 * `@react-native-async-storage/async-storage` is not installed and adding it is
 * a new dependency, which this pass does not do. So dismissal lasts for this
 * app session only: it comes back on every cold start. That is the right
 * trade for a demo (a judge who closes and reopens gets the explanation again)
 * and the wrong one for a real user, so it is logged in MEMORY.md rather than
 * left to be discovered.
 *
 * Tokens only — `card` fill, `border` outline, `primary` for the step numbers
 * because it is the one accent and the step numbers are the call to action.
 */
export interface FirstRunCardProps {
  onDismiss: () => void;
}

const STEPS = [
  'Drag the slider to choose a cyclone strength',
  'See what floods',
  'Tap Generate Advisory',
];

export function FirstRunCard({ onDismiss }: FirstRunCardProps) {
  return (
    <View style={styles.card} accessibilityRole="summary">
      <View style={styles.headerRow}>
        <Text style={styles.heading}>Try this</Text>
        <Pressable
          onPress={onDismiss}
          accessibilityRole="button"
          accessibilityLabel="Dismiss the three-step introduction"
          // Generous hit target for a 13px glyph: `hitSlop` rather than padding,
          // so the visual stays compact without the touch target shrinking.
          hitSlop={12}
          style={({ pressed }) => [styles.dismiss, pressed ? styles.dismissPressed : null]}
        >
          <Text style={styles.dismissLabel}>✕</Text>
        </Pressable>
      </View>

      {STEPS.map((step, index) => (
        <View key={step} style={styles.stepRow}>
          <View style={styles.badge}>
            <Text style={styles.badgeLabel}>{index + 1}</Text>
          </View>
          <Text style={styles.stepLabel}>{step}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    position: 'absolute',
    // Top-centre rather than bottom: the map's bottom edge already carries the
    // legend and the storm-path control, and the bottom-right is where the
    // flood-layer banner is anchored. Centre-top is the one region of the map
    // this screen has never used.
    top: theme.spacing.xs,
    left: theme.spacing.xs,
    right: theme.spacing.xs,
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
    marginBottom: theme.spacing.xs,
  },
  heading: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  dismiss: {
    // No visible chip: the ✕ is a caption glyph in `textMuted`, and a filled
    // button here would out-weigh the thing it dismisses.
    padding: 2,
  },
  dismissPressed: {
    opacity: 0.6,
  },
  dismissLabel: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  stepRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: theme.spacing.xs / 2,
  },
  badge: {
    width: theme.spacing.sm + theme.spacing.xs / 2,
    height: theme.spacing.sm + theme.spacing.xs / 2,
    borderRadius: theme.radius.chip,
    backgroundColor: theme.colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: theme.spacing.xs,
  },
  badgeLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.card,
  },
  stepLabel: {
    flex: 1,
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
});
