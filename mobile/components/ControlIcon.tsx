import React from 'react';
import { StyleSheet, View } from 'react-native';

import { theme } from '../theme';

/**
 * Two small icons for the map's controls, drawn from plain `View`s.
 *
 * **No icon font.** `@expo/vector-icons` is not in `package.json` and this
 * change adds no dependency, so a glyph is composed from boxes and a rotated
 * bar instead. That is a real constraint with a real cost — these are not
 * platform-native icons, they are two shapes that read correctly at 16dp — and
 * it is logged in MEMORY.md so nobody later reads them as an oversight and
 * "fixes" it by installing a font and re-tuning sizes that the tokens never
 * specified.
 *
 * Both draw in `theme.colors.text`, which is the colour their labels already
 * use, so an icon cannot disagree with the word next to it.
 */

/** A dogleg polyline — reads as a storm path. */
export function PathIcon() {
  return (
    <View style={styles.pathIcon}>
      {/* Two bars meeting at a corner: one rising, one falling. */}
      <View style={[styles.pathSegment, styles.pathSegmentRise]} />
      <View style={[styles.pathSegment, styles.pathSegmentFall]} />
    </View>
  );
}

/** A crosshair over a filled dot — reads as "centre on this". */
export function TargetIcon() {
  return (
    <View style={styles.targetIcon}>
      <View style={styles.targetRing} />
      <View style={styles.targetDot} />
    </View>
  );
}

const SEGMENT = 12;
const BAR = 2;

const styles = StyleSheet.create({
  pathIcon: {
    width: theme.spacing.md,
    height: theme.spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pathSegment: {
    position: 'absolute',
    width: SEGMENT,
    height: BAR,
    borderRadius: BAR,
    backgroundColor: theme.colors.text,
  },
  pathSegmentRise: {
    bottom: 5,
    left: 0,
    transform: [{ rotate: '-38deg' }],
  },
  pathSegmentFall: {
    top: 4,
    right: 0,
    transform: [{ rotate: '38deg' }],
  },
  targetIcon: {
    width: theme.spacing.md,
    height: theme.spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  targetRing: {
    width: SEGMENT + 2,
    height: SEGMENT + 2,
    borderRadius: (SEGMENT + 2) / 2,
    borderWidth: BAR,
    borderColor: theme.colors.text,
  },
  targetDot: {
    position: 'absolute',
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.colors.text,
  },
});
