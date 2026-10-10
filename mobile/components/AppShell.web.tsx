/**
 * AppShell — the compact layout's bottom sheet: a grabber, four tabs, and
 * whatever the parent puts in them. Nothing else.
 *
 * ## Scope, and what deliberately is not here
 *
 * This component is the sheet and the tab bar. It renders no content of its
 * own, owns no data, fetches nothing, and never acts on a tab tap beyond
 * reporting it — tabs navigate, they don't act, which is why `onSelectTab`
 * is a callback rather than a switch. The parent owns `activeTab` because
 * parent-level flows (the strength chips, the advisory) read it; the detent
 * is internal state because nothing outside the sheet needs to drive it.
 *
 * ## Why a `.web.tsx` file
 *
 * The native screen keeps its own layout this round, so this shell exists
 * only for the Web compact layout; the `.web` suffix keeps it out of the
 * native bundle exactly the way `MapScreen.web.tsx` does, and
 * `tests/webBundleSafety.test.mjs` guards that boundary for the rest of the
 * graph.
 *
 * ## The detent cycle, and why there is no drag
 *
 * The grabber **tap-cycles** peek → half → full → peek through the pure
 * `nextSheetDetent`, so the cycle is pinned by the same node tests that pin
 * the tab order. There is no drag gesture on purpose: the install rules
 * forbid casual dependency additions, a hand-rolled pan on React Native Web
 * is brittle, and the content `ScrollView` already gives the natural
 * expand-to-read feel. A drag can be a later enhancement, not a prerequisite.
 *
 * ## Monochrome over a bright map
 *
 * The sheet is near-black `card` with a hairline top border, sitting over
 * the pale basemap — the bar stays monochrome while the map below is the
 * brightest thing on screen, per `color.md`'s guidance on controls over
 * colorful content. Selection is carried three ways, only one of which is
 * colour: the selected tab is semibold, `text`-coloured, and underlined;
 * the idle tabs are `textMuted` (10.13:1 on `card`).
 *
 * ## Placement contract
 *
 * The sheet is **in flow**, at its detent height: the parent's column is
 * the top bar, the map (`flex: 1`), then this sheet — so the map ends
 * exactly where the sheet begins and the map's own bottom-edge chrome (the
 * basemap disclosure strip, the legend, the S/T/L buttons) stays visible.
 * An absolute overlay would put all of that behind the sheet, and a hidden
 * disclosure is not an option. Heights come from `sheetHeightPx` so the
 * fractions stay in `webViewModel.ts`, tested.
 */

import React, { useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';

import { theme } from '../theme';
import {
  DEFAULT_SHEET_DETENT,
  nextSheetDetent,
  sheetHeightPx,
  SHELL_TABS,
  SHELL_TAB_LABELS,
  type SheetDetent,
  type ShellTabId,
} from '../webViewModel';

export interface AppShellProps {
  /** The selected tab. Owned by the parent — the shell only reports taps. */
  activeTab: ShellTabId;
  /** Called when a tab is tapped. The shell adds no side effects of its own. */
  onSelectTab: (tab: ShellTabId) => void;
  /**
   * The four tab bodies, keyed by tab id. Only the active tab's body is
   * mounted; the other three stay unmounted React elements, which keeps a
   * tab's content from rendering (and its effects from firing) while hidden.
   */
  children: Record<ShellTabId, React.ReactNode>;
}

export function AppShell({ activeTab, onSelectTab, children }: AppShellProps) {
  const { height: viewportHeight } = useWindowDimensions();
  /** The sheet's own posture — opened at half, per `DEFAULT_SHEET_DETENT`. */
  const [detent, setDetent] = useState<SheetDetent>(DEFAULT_SHEET_DETENT);

  return (
    <View style={[styles.sheet, { height: sheetHeightPx(detent, viewportHeight) }]}>
      <Pressable
        onPress={() => setDetent((current) => nextSheetDetent(current))}
        accessibilityRole="button"
        accessibilityLabel={`Sheet at ${detent} height. Tap to resize.`}
        style={styles.grabberZone}
      >
        <View style={styles.grabber} />
      </Pressable>

      <View style={styles.tabBar}>
        {SHELL_TABS.map((id) => {
          const selected = id === activeTab;
          return (
            <Pressable
              key={id}
              onPress={() => onSelectTab(id)}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              style={({ pressed }: { pressed: boolean }) => [
                styles.tab,
                pressed && styles.pressed,
              ]}
            >
              <Text
                style={[
                  styles.tabLabel,
                  selected ? styles.tabLabelSelected : styles.tabLabelIdle,
                ]}
              >
                {SHELL_TAB_LABELS[id]}
              </Text>
              {selected ? <View style={styles.tabUnderline} /> : null}
            </Pressable>
          );
        })}
      </View>

      {/*
        The active tab's body alone. `keyboardShouldPersistTaps` keeps the
        locality search's suggestion list tappable while the keyboard is up —
        the Route tab's first control is a text input.
      */}
      <ScrollView
        style={styles.content}
        contentContainerStyle={styles.contentPadding}
        keyboardShouldPersistTaps="handled"
      >
        {children[activeTab]}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  /**
   * In flow, at its detent height — the parent stacks the top bar, the map
   * (`flex: 1`) and this sheet, so the map ends exactly where the sheet
   * begins and the map's own bottom-edge chrome stays visible rather than
   * sitting behind an overlay. The height still arrives per detent from the
   * style array in the JSX, never from here — the fractions belong to
   * `webViewModel.ts` where the tests can reach them.
   */
  sheet: {
    backgroundColor: theme.colors.card,
    borderTopLeftRadius: theme.radius.card,
    borderTopRightRadius: theme.radius.card,
    borderTopWidth: 1,
    borderTopColor: theme.colors.border,
    ...theme.shadow.card,
  },
  /** The 44 px target floor wraps a 4 px visual pill. */
  grabberZone: {
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'flex-end',
    paddingBottom: 10,
  },
  grabber: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.colors.textMuted,
  },
  tabBar: {
    flexDirection: 'row',
    paddingHorizontal: theme.spacing.xs,
  },
  /** Four equal tabs across a ~390 px viewport; the 44 px floor each. */
  tab: {
    flex: 1,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tabLabel: {
    fontSize: 12,
  },
  tabLabelIdle: {
    fontFamily: theme.fonts.bodyMedium,
    color: theme.colors.textMuted,
  },
  tabLabelSelected: {
    fontFamily: theme.fonts.bodySemibold,
    color: theme.colors.text,
  },
  tabUnderline: {
    width: 24,
    height: 2,
    borderRadius: 1,
    backgroundColor: theme.colors.text,
    marginTop: 3,
  },
  pressed: { opacity: 0.72 },
  content: { flex: 1 },
  contentPadding: {
    paddingHorizontal: theme.spacing.sm,
    paddingTop: theme.spacing.xs,
    paddingBottom: theme.spacing.md,
  },
});
