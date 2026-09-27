import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

import { theme } from '../theme';

/**
 * Ghost button — Design.md: "ghost style — white fill, 1px `border`, `text`
 * color". Used for the SMS copy action and the modal's dismiss control, so
 * it stays visually subordinate to the one primary action.
 */
export interface GhostButtonProps {
  label: string;
  onPress: () => void;
  disabled?: boolean;
}

export function GhostButton({ label, onPress, disabled }: GhostButtonProps) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ disabled: !!disabled }}
      style={({ pressed }) => [
        styles.button,
        pressed ? styles.buttonPressed : null,
        disabled ? styles.buttonDisabled : null,
      ]}
    >
      <Text style={styles.label}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: theme.spacing.xs,
    paddingHorizontal: theme.spacing.sm,
  },
  buttonPressed: {
    // Same rationale as PrimaryButton: no pressed token exists in Design.md.
    opacity: 0.8,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  label: {
    fontFamily: theme.fonts.bodySemibold,
    color: theme.colors.text,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
});
