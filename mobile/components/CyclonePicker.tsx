import React, { useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { liveStateLabel, type Cyclone, type LiveCycloneState } from '../cycloneModel';
import { liveBannerText } from '../scenarioCompare.ts';

/**
 * Cyclone picker — which storm the app is addressing, and whether "now" is
 * one of them.
 *
 * Two modes, and the distinction is the whole point:
 *
 *   - **Historical** — a storm from the IBTrACS catalogue. A track that
 *     happened, with real fixes and a real source.
 *   - **Live** — what the ATCF feed says right now. A probe of a live source,
 *     not a forecast, and **never** a historical stand-in: when the feed did
 *     not answer, the banner says so and names no storm.
 *
 * The mode is internal state. The parent owns the live fetch, because it owns
 * every other fetch and the picker has no business starting a request of its
 * own.
 *
 * **The cyclone list is a chip row, not a `<Picker>`** — the same reason
 * `LocalityPicker` gives: `@react-native-picker/picker` is a native module
 * that is not installed, and the row scrolls, so 610 cyclones is not a
 * constraint. The case study is pinned first so the default is one tap away.
 */
export interface CyclonePickerProps {
  cyclones: Cyclone[];
  selectedId: string;
  onSelect: (id: string) => void;
  /** The live state, or null while it has not been fetched. */
  live: LiveCycloneState | null;
  liveLoading: boolean;
}

type Mode = 'historical' | 'live';

export function CyclonePicker({
  cyclones,
  selectedId,
  onSelect,
  live,
  liveLoading,
}: CyclonePickerProps) {
  const [mode, setMode] = useState<Mode>('historical');

  if (!cyclones.length) return null;

  // The case study first, then the backend's order (newest first).
  const ordered = [
    ...cyclones.filter((c) => c.is_case_study),
    ...cyclones.filter((c) => !c.is_case_study),
  ];

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.heading}>Cyclone</Text>
        <View style={styles.toggle}>
          {(['historical', 'live'] as const).map((m) => {
            const active = mode === m;
            return (
              <Pressable
                key={m}
                onPress={() => setMode(m)}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                style={({ pressed }) => [
                  styles.toggleChip,
                  active ? styles.toggleChipActive : styles.toggleChipIdle,
                  pressed ? styles.chipPressed : null,
                ]}
              >
                <Text
                  style={[
                    styles.toggleLabel,
                    active ? styles.toggleLabelActive : null,
                  ]}
                >
                  {m === 'historical' ? 'Historical' : 'Live'}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </View>

      {mode === 'historical' ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.row}
        >
          {ordered.map((cyclone) => {
            const selected = cyclone.cyclone_id === selectedId;
            return (
              <Pressable
                key={cyclone.cyclone_id}
                onPress={() => onSelect(cyclone.cyclone_id)}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                style={({ pressed }) => [
                  styles.chip,
                  selected ? styles.chipSelected : styles.chipUnselected,
                  pressed ? styles.chipPressed : null,
                ]}
              >
                <Text
                  style={[styles.chipLabel, selected ? styles.chipLabelSelected : null]}
                  numberOfLines={1}
                >
                  {cyclone.name} {cyclone.season}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      ) : liveLoading ? (
        <View style={styles.liveRow}>
          <ActivityIndicator size="small" color={theme.colors.textMuted} />
          <Text style={styles.liveText}>Checking the live feed…</Text>
        </View>
      ) : live ? (
        <View>
          <Text style={styles.liveState}>{liveStateLabel(live)}</Text>
          <Text style={styles.liveText}>{liveBannerText(live)}</Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: theme.spacing.sm,
  },
  header: {
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
  toggle: {
    flexDirection: 'row',
  },
  toggleChip: {
    borderRadius: theme.radius.chip,
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: theme.spacing.xs / 2,
    marginLeft: theme.spacing.xs,
    borderWidth: 1,
  },
  toggleChipIdle: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
  },
  toggleChipActive: {
    backgroundColor: theme.colors.primary,
    borderColor: theme.colors.primary,
  },
  toggleLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  toggleLabelActive: {
    color: theme.colors.card,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  chip: {
    borderRadius: theme.radius.chip,
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: theme.spacing.xs / 2,
    marginRight: theme.spacing.xs,
    borderWidth: 1,
  },
  chipSelected: {
    backgroundColor: theme.colors.primary,
    borderColor: theme.colors.primary,
  },
  chipUnselected: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
  },
  chipPressed: {
    opacity: 0.8,
  },
  chipLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  chipLabelSelected: {
    color: theme.colors.card,
  },
  liveRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  liveState: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  liveText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
});
