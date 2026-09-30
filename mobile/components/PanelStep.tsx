import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

/**
 * One numbered step in the panel: a navy badge with a blue number, a title, and
 * whatever the step is.
 *
 * **The badge is `selectedFill` and the number is `primary`.** That split is the
 * design's, and it is not decoration: `selectedFill` (#011132) is *darker* than
 * the page (#090909), so the badge reads as a recessed well rather than a
 * sticker, and the number inside it is the only `primary` on the panel. Three
 * badges down the left edge give the panel a spine and make the three steps
 * countable at a glance — which is the point, because the app's pitch is
 * literally "three things, in this order".
 *
 * This is the same visual as `FirstRunCard`'s badges, which are `primary`-filled.
 * They are deliberately different: the first-run card is a transient overlay on
 * a pale map and needs to be seen at a glance, while these sit on a near-black
 * panel where a bright blue disc would be the loudest thing on screen and would
 * compete with the Generate button it is steering the reader toward.
 */
export interface PanelStepProps {
  /** 1-based. Rendered as given; the caller owns the count. */
  index: number;
  title: string;
  children?: React.ReactNode;
}

export function PanelStep({ index, title, children }: PanelStepProps) {
  return (
    <View style={styles.step}>
      <View style={styles.badge}>
        <Text style={styles.badgeLabel}>{index}</Text>
      </View>
      <View style={styles.body}>
        <Text style={styles.title}>{title}</Text>
        {children}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  step: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: theme.spacing.sm,
  },
  badge: {
    width: theme.spacing.xs + theme.spacing.xs / 2,
    height: theme.spacing.xs + theme.spacing.xs / 2,
    borderRadius: theme.radius.chip,
    backgroundColor: theme.colors.selectedFill,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: theme.spacing.xs,
  },
  badgeLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.primary,
  },
  body: {
    flex: 1,
  },
  title: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    // The badge is a circle on the first line of the title, so the title's
    // top is nudged down by a third of the caption line to centre them.
    marginTop: theme.spacing.xs / 4,
    marginBottom: theme.spacing.xs / 2,
  },
});
