import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { LocalityPicker } from './LocalityPicker';
import type { Locality } from '../api';

/**
 * "From: Sagar · change" — the advisory's origin, in one line.
 *
 * **A disclosure, not a control.** Before this the origin was a whole card with
 * a heading and a 45-item horizontal chip row, sitting between the exposure
 * numbers and the button. It was the largest thing in the panel for a decision
 * most viewers make once, and it pushed the counts off the screen. Now it is one
 * line that opens the *same* picker — no new component, no new dependency, and
 * the full list is still one tap away.
 *
 * The name shown is the locality's own `name` from `/localities`, which for the
 * default is **"Sagar"** and not "Sagar Island". The brief asked for "Sagar
 * Island", and the discrepancy is deliberate rather than an oversight: the sheet
 * has to name the place the way the backend names it, because this same string
 * goes into the advisory prompt and into the `generated_for.origin.name` the
 * stale-guard compares against. Printing "Sagar Island" here and "Sagar" in the
 * modal would be two names for one place on one screen. Logged in MEMORY.md —
 * renaming it in `backend/locations.py` is the real fix and is a backend change
 * this pass does not make.
 */
export interface OriginLineProps {
  localities: Locality[];
  selectedId: string;
  onSelect: (id: string) => void;
}

export function OriginLine({ localities, selectedId, onSelect }: OriginLineProps) {
  const [open, setOpen] = useState(false);
  const selected = localities.find((l) => l.id === selectedId) ?? null;

  return (
    <View>
      <View style={styles.row}>
        <Text style={styles.label}>From: </Text>
        <Text style={styles.value}>{selected?.name ?? '…'}</Text>
        <Pressable
          onPress={() => setOpen((v) => !v)}
          accessibilityRole="button"
          accessibilityLabel={
            open ? 'Hide the list of places to evacuate from' : 'Change the place to evacuate from'
          }
          hitSlop={8}
          style={({ pressed }) => [styles.change, pressed ? styles.changePressed : null]}
        >
          <Text style={styles.changeLabel}>{open ? 'close' : 'change'}</Text>
        </Pressable>
      </View>

      {/*
        The picker stays mounted and collapses rather than unmounting. Two
        reasons, one of them substantive: the row keeps its height only while
        open, so toggling does not make the button above jump; and the picker
        itself is a horizontal `ScrollView` that would lose its scroll position
        on unmount, so a reader who scrolled to find a village and closed the
        list would lose their place.
      */}
      <View style={open ? styles.open : styles.closed}>
        <LocalityPicker
          localities={localities}
          selectedId={selectedId}
          onSelect={(id) => {
            onSelect(id);
            setOpen(false);
          }}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  label: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  value: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  change: {
    marginLeft: theme.spacing.xs / 2,
    paddingHorizontal: 2,
  },
  changePressed: {
    opacity: 0.6,
  },
  changeLabel: {
    // `selectedText`, the same blue a selected chip uses — "change" is an
    // action on the chip vocabulary, so it is coloured as one.
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.selectedText,
    textDecorationLine: 'underline',
  },
  open: {
    marginTop: theme.spacing.xs,
  },
  // `height: 0` + `overflow: hidden` rather than a conditional render, so the
  // picker's horizontal ScrollView keeps its scroll offset across toggles.
  closed: {
    height: 0,
    overflow: 'hidden',
  },
});
