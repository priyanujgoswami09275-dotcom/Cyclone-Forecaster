import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text } from 'react-native';

import { theme } from '../theme';

/**
 * The app's single main action — Design.md: "`primary` fill, white text,
 * 12px radius". Design.md deliberately gives the app exactly one accent
 * colour, reserved for this button.
 */
export interface PrimaryButtonProps {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  /** Shows a spinner and blocks presses — used while the advisory is generating. */
  busy?: boolean;
}

export function PrimaryButton({ label, onPress, disabled, busy }: PrimaryButtonProps) {
  const inactive = disabled || busy;

  return (
    <Pressable
      onPress={onPress}
      disabled={inactive}
      accessibilityRole="button"
      accessibilityState={{ disabled: !!inactive, busy: !!busy }}
      style={({ pressed }) => [
        styles.button,
        pressed && !inactive ? styles.buttonPressed : null,
        inactive ? styles.buttonDisabled : null,
      ]}
    >
      {busy ? <ActivityIndicator color={theme.colors.card} /> : <Text style={styles.label}>{label}</Text>}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: theme.colors.primary,
    borderRadius: theme.radius.button,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: theme.spacing.xs,
    paddingHorizontal: theme.spacing.sm,
  },
  buttonPressed: {
    // Design.md defines no pressed variant for primary. Kept as a flat
    // opacity change rather than inventing a new colour token.
    opacity: 0.8,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  label: {
    fontFamily: theme.fonts.bodySemibold,
    // Design.md's "white text" -> `card` is the same #ffffff. Mapping
    // recorded in MEMORY.md "Flagged for review".
    color: theme.colors.card,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
});
