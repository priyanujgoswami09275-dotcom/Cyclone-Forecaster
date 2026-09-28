import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import type { Locality } from '../api';

/**
 * Origin picker — a horizontal chip row over `/localities`.
 *
 * A chip row rather than a `<Picker>` for one dependency-free reason:
 * `@react-native-picker/picker` is a native module that is not installed, and
 * adding one to pick from 45 short strings is not worth it. The row scrolls,
 * so the list length is not a constraint either way.
 *
 * The list is sorted with the default first. `/localities` returns them in
 * id order (alphabetical by slug), which puts Anantapur at the top — a
 * northern-edge village that is a perfectly valid origin and a poor default
 * for a demo about Sagar Island. Sagar is pinned to the front so the common
 * case is one tap, and the rest keep the backend's order so the picker and
 * the API agree about ordering.
 */
export interface LocalityPickerProps {
  localities: Locality[];
  selectedId: string;
  onSelect: (id: string) => void;
  disabled?: boolean;
}

const DEFAULT_ORIGIN = 'sagar';

export function LocalityPicker({
  localities,
  selectedId,
  onSelect,
  disabled,
}: LocalityPickerProps) {
  if (!localities.length) return null;

  const ordered = [
    ...localities.filter((l) => l.id === DEFAULT_ORIGIN),
    ...localities.filter((l) => l.id !== DEFAULT_ORIGIN),
  ];

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Evacuation origin</Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
      >
        {ordered.map((locality) => {
          const selected = locality.id === selectedId;
          return (
            <Pressable
              key={locality.id}
              onPress={() => onSelect(locality.id)}
              disabled={disabled}
              accessibilityRole="button"
              accessibilityState={{ selected, disabled: !!disabled }}
              style={({ pressed }) => [
                styles.chip,
                selected ? styles.chipSelected : styles.chipUnselected,
                pressed && !disabled ? styles.chipPressed : null,
                disabled ? styles.chipDisabled : null,
              ]}
            >
              <Text
                style={[styles.chipLabel, selected ? styles.chipLabelSelected : null]}
                numberOfLines={1}
              >
                {locality.name}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
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
  heading: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    marginBottom: theme.spacing.xs,
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
  chipDisabled: {
    opacity: 0.5,
  },
  chipLabel: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  chipLabelSelected: {
    color: theme.colors.card,
  },
});
