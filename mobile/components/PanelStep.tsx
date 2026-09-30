import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

/**
 * One numbered step in the panel: a navy badge with a blue number, a title, and
 * whatever the step is.
 *
 * **The badge is `selectedFill` and the number is `selectedText`.** The badge
 * fill is the design's and is not decoration: `selectedFill` (#011132) is
 * *darker* than the page (#090909), so the badge reads as a recessed well
 * rather than a sticker. Three badges down the left edge give the panel a spine
 * and make the three steps countable at a glance — which is the point, because
 * the app's pitch is literally "three things, in this order".
 *
 * **The numeral was `primary` until 2026-10-01, and that was unreadable.**
 * `#0d52c3` on `#011132` is a contrast ratio of **2.67:1**, well under the
 * 4.5:1 WCAG AA floor for text at this size, so the numbers were dark blue on
 * near-black and effectively invisible. `selectedText` (#5f9dea) on the same
 * fill is **6.65:1** — and it is the pairing the theme already specifies for
 * *any* selected surface, including `StrengthChips`' selected chip. So this is
 * the existing design system applied consistently, not a new colour and not a
 * new token.
 *
 * `primary` still carries the accent it is for: the Generate button, and the
 * selected chip's border. A 2.67:1 pairing cannot carry a numeral.
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
    // 6.65:1 on this badge's `selectedFill`. `primary` here was 2.67:1 — see the
    // component's doc comment.
    color: theme.colors.selectedText,
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
