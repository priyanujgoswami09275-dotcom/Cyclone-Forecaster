import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { PathIcon, TargetIcon } from './ControlIcon';

/**
 * The three floating buttons on the map's right edge.
 *
 * **Opaque white with `onWhite` text**, which is the one inversion in the dark
 * theme and is not negotiable: these sit *on top of* a pale basemap (`land` is
 * `#e6ece0`), and `text` — the app's own near-black — is the darkest thing in
 * the palette, so a dark chrome chip on a pale landmass is a control nobody can
 * see. The chrome is light; the app around it is dark.
 *
 * **Stacked vertically at the right, not scattered.** The map's other floating
 * element, the legend, holds the bottom-LEFT, so the right edge is free for
 * controls. Vertical order is deliberate and matches the reader's likely path:
 * the storm path is the one control that is also a *reveal* (it fits the map to
 * something not currently visible), so it is on top where the thumb lands
 * first; the recentre is the way back and sits at the bottom, nearest the
 * corner.
 *
 * **Icon-only, and every icon is drawn, not a font glyph.** `@expo/vector-icons`
 * is listed in `expo/bundledNativeModules.json` but is **not installed** in
 * Expo SDK 57.0.26 and does not resolve under any specifier — this pass is
 * forbidden from adding a dependency, so `ControlIcon.tsx` composes the two
 * shapes from plain `View`s. That is a real limitation: these are drawn marks,
 * not platform-native icons, and they will not match the platform's icon
 * language. Logged in MEMORY.md "Flagged for review". Adding
 * `@expo/vector-icons` is the fix, and it is a one-line install — but it is the
 * human's call, not this pass's.
 *
 * Every button carries an `accessibilityLabel` that says what it *does*,
 * because an icon with no label is invisible to a screen reader and the labels
 * are the only thing distinguishing the three.
 */
export interface MapControlProps {
  /** Show the full storm path and fit to it, or return to Sagar. */
  onPressTrack: () => void;
  /** True while the map is fitted to the whole track. */
  trackActive: boolean;
  /** Layers control. Present and inert — see the note below. */
  onPressLayers: () => void;
  /** Recentre on Sagar Island. */
  onPressRecentre: () => void;
}

export function MapControl({
  onPressTrack,
  trackActive,
  onPressLayers,
  onPressRecentre,
}: MapControlProps) {
  return (
    <View style={styles.stack}>
      <MapButton
        onPress={onPressTrack}
        label={trackActive ? 'Show the flood area at Sagar Island' : 'Show the full storm path'}
        pressedTint={trackActive}
      >
        <PathIcon />
      </MapButton>

      <MapButton onPress={onPressLayers} label="Map layers">
        {/*
          The layers glyph is three stacked bars — the standard "layers" mark.
          It is drawn here rather than in `ControlIcon.tsx` because the other
          two are used by more than one caller and this is not; keeping it local
          avoids growing a shared icon module for a single user.
        */}
        <View style={styles.layersIcon}>
          <View style={styles.layersBar} />
          <View style={styles.layersBar} />
          <View style={styles.layersBar} />
        </View>
      </MapButton>

      <MapButton onPress={onPressRecentre} label="Zoom to Sagar Island">
        <TargetIcon />
      </MapButton>
    </View>
  );
}

function MapButton({
  onPress,
  label,
  children,
  pressedTint,
}: {
  onPress: () => void;
  label: string;
  children: React.ReactNode;
  pressedTint?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      // 44dp is the smallest comfortable touch target; three of these plus
      // their gaps is ~156dp of the map's right edge.
      style={({ pressed }) => [
        styles.button,
        pressed ? styles.buttonPressed : null,
        pressedTint ? styles.buttonActive : null,
      ]}
    >
      {children}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  stack: {
    position: 'absolute',
    right: theme.spacing.xs,
    bottom: theme.spacing.xs,
    alignItems: 'center',
  },
  button: {
    width: theme.spacing.xl + theme.spacing.xs, // 48dp
    height: theme.spacing.xl + theme.spacing.xs,
    borderRadius: theme.radius.button,
    backgroundColor: theme.colors.white,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: theme.spacing.xs / 2,
    ...theme.shadow.card,
  },
  buttonPressed: {
    opacity: 0.7,
  },
  buttonActive: {
    // The storm-path button stays visibly "on" while the map is fitted to the
    // track, so the state is on the control rather than only in the map. A 2dp
    // `selectedText` ring rather than a fill change — a filled version would
    // stop being the white floating button the design specifies.
    borderWidth: 2,
    borderColor: theme.colors.selectedText,
  },
  layersIcon: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  layersBar: {
    width: theme.spacing.md - theme.spacing.xs, // 16dp
    height: 2,
    backgroundColor: theme.colors.onWhite,
    marginVertical: 1.5,
  },
});
