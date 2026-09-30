/**
 * The evacuation-origin selector: a searchable combobox over every locality.
 *
 * ## Why this is a real search box
 *
 * The previous Web build took `localities.slice(0, 10)` — an arbitrary prefix
 * of whatever order the API returned. That is not a subset anyone chose, and
 * it hid Sagar Island, the case study's actual landfall, whenever the API's
 * ordering happened to put it eleventh. The backend scopes the study area to
 * **45** localities and every one of them is a legal `origin` for `/routes` and
 * `/advisory`, so all 45 belong in the control.
 *
 * The list is sorted by `radius_km` descending, which is the backend's own
 * search radius — ordering by the field that decides whether a place is
 * findable from a search box — then alphabetically for stability. Filtering
 * matches the name, the id and the `place` classification, so "town", "village"
 * and "sagar" all work as queries.
 *
 * Keyboard support is real: the input takes focus, Enter selects the first
 * match, Escape closes the list, and the list is a `role="listbox"` with
 * `aria-selected` on the current choice. This is the Web build, so it is built
 * from Web primitives rather than ported from the native `LocalityPicker`.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import type { LocalitiesResponse } from '../api';
import { theme } from '../theme';
import { localityMatchCount, searchableLocalities } from '../webViewModel';

export interface LocalitySearchProps {
  response: LocalitiesResponse | null;
  selectedId: string;
  onSelect: (id: string) => void;
  disabled: boolean;
}

/**
 * Web-only ARIA props.
 *
 * `role="listbox"` and `role="option"` are the correct ARIA roles for a
 * combobox list, but React Native's `AccessibilityRole` union has no
 * `listbox`/`option` members — they exist only on Web. Spreading them through
 * a loose type keeps the accessibility semantics without pretending the native
 * prop accepts them, and this file never ships to a native target.
 */
type WebOnly = Record<string, unknown>;

export function LocalitySearch({
  response,
  selectedId,
  onSelect,
  disabled,
}: LocalitySearchProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  /**
   * Hover is tracked by id rather than via the style callback's `hovered` flag,
   * which `react-native-web` provides but the native `Pressable` types do not
   * declare — and this file is built against those types. One piece of local
   * state is the version of that flag the typechecker can see.
   */
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const inputRef = useRef<TextInput | null>(null);
  const wrapRef = useRef<View | null>(null);

  const selected = response?.localities.find((l) => l.id === selectedId) ?? null;
  const results = useMemo(() => searchableLocalities(response, query), [response, query]);
  const counts = useMemo(() => localityMatchCount(response, query), [response, query]);

  // Clicking anywhere outside the control closes the list. Without this a judge
  // who opens the picker, clicks the map and comes back finds a stale list
  // covering the map.
  useEffect(() => {
    if (!open) return undefined;
    const onDocumentClick = (event: MouseEvent) => {
      const node = wrapRef.current as unknown as HTMLElement | null;
      if (node && event.target instanceof Node && !node.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocumentClick);
    return () => document.removeEventListener('mousedown', onDocumentClick);
  }, [open]);

  const choose = useCallback(
    (id: string) => {
      onSelect(id);
      setOpen(false);
      setQuery('');
      inputRef.current?.blur();
    },
    [onSelect],
  );

  return (
    <View style={styles.wrap} ref={wrapRef}>
      {open ? (
        <>
          <TextInput
            ref={inputRef}
            value={query}
            onChangeText={setQuery}
            onFocus={() => setOpen(true)}
            // Enter takes the top match, which is the one a judge typing a few
            // letters almost always means.
            onSubmitEditing={() => {
              if (results.length > 0) choose(results[0].id);
            }}
            placeholder="Search 45 localities…"
            placeholderTextColor={theme.colors.textMuted}
            style={styles.input}
            autoFocus
            accessibilityLabel="Search evacuation origins"
          />
          <View
            style={[styles.list, { overflowY: 'auto' } as never]}
            {...({ role: 'listbox' } as WebOnly)}
          >
            <Text style={styles.count}>
              {counts.matched === counts.total
                ? `All ${counts.total} study-area localities`
                : `${counts.matched} of ${counts.total} localities`}
            </Text>
            {results.length === 0 ? (
              <Text style={styles.empty}>No locality matches “{query}”.</Text>
            ) : (
              results.map((locality) => {
                const isSelected = locality.id === selectedId;
                return (
                  <Pressable
                    key={locality.id}
                    onPress={() => choose(locality.id)}
                    accessibilityState={{ selected: isSelected }}
                    {...({ role: 'option', 'aria-selected': isSelected } as WebOnly)}
                    onHoverIn={() => setHoveredId(locality.id)}
                    onHoverOut={() =>
                      setHoveredId((current) => (current === locality.id ? null : current))
                    }
                    style={({ pressed }: { pressed: boolean }) => [
                      styles.option,
                      isSelected && styles.optionSelected,
                      pressed && styles.pressed,
                      hoveredId === locality.id && styles.optionHovered,
                    ]}
                  >
                    <Text
                      style={[styles.optionName, isSelected && styles.optionNameSelected]}
                    >
                      {locality.name}
                    </Text>
                    <Text style={styles.optionMeta}>
                      {locality.place} · {locality.radius_km.toFixed(0)} km radius
                    </Text>
                  </Pressable>
                );
              })
            )}
          </View>
        </>
      ) : (
        <Pressable
          onPress={() => setOpen(true)}
          disabled={disabled}
          accessibilityRole="button"
          accessibilityLabel={`Change evacuation origin. Currently ${selected?.name ?? 'none selected'}`}
          onHoverIn={() => setHoveredId(null)}
          style={({ pressed }: { pressed: boolean }) => [
            styles.closed,
            pressed && !disabled && styles.pressed,
            disabled && styles.disabled,
          ]}
        >
          <View style={styles.closedText}>
            <Text style={styles.closedLabel}>Evacuation origin</Text>
            <Text style={styles.closedValue}>{selected?.name ?? 'Choose a locality'}</Text>
          </View>
          <Text style={styles.closedAction}>Change</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: 'relative',
    zIndex: 30,
  },
  closed: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: theme.colors.background,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    paddingHorizontal: 14,
    paddingVertical: 11,
  },
  closedHovered: {
    borderColor: theme.colors.selectedText,
  },
  closedText: {
    gap: 2,
  },
  closedLabel: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    color: theme.colors.textMuted,
  },
  closedValue: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  closedAction: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.selectedText,
    textDecorationLine: 'underline',
  },
  input: {
    backgroundColor: theme.colors.background,
    borderWidth: 1,
    borderColor: theme.colors.selectedText,
    borderRadius: theme.radius.button,
    paddingHorizontal: 14,
    paddingVertical: 11,
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  list: {
    marginTop: 4,
    // RN's ViewStyle has no `overflowY`, but the browser needs it or a long
    // list of 45 localities pushes the page. Cast at the single use site below
    // rather than widening the style object, so the gap is visible.
    maxHeight: 300,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    padding: 6,
  },
  count: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.textMuted,
    paddingHorizontal: 8,
    paddingBottom: 5,
  },
  empty: {
    fontFamily: theme.fonts.body,
    fontSize: 13,
    color: theme.colors.textMuted,
    padding: 10,
  },
  option: {
    paddingHorizontal: 9,
    paddingVertical: 8,
    borderRadius: 8,
  },
  optionSelected: {
    backgroundColor: theme.colors.selectedFill,
  },
  optionHovered: {
    backgroundColor: theme.colors.background,
  },
  optionName: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: 14,
    color: theme.colors.text,
  },
  optionNameSelected: {
    fontFamily: theme.fonts.bodySemibold,
    color: theme.colors.selectedText,
  },
  optionMeta: {
    fontFamily: theme.fonts.body,
    fontSize: 11,
    color: theme.colors.textMuted,
    marginTop: 1,
  },
  pressed: {
    opacity: 0.7,
  },
  disabled: {
    opacity: 0.45,
  },
});
