import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { EMPTY_EXPOSURE_LINE, buildTiles, type TileInput } from '../exposureTiles';

/**
 * Step 2 of the panel: three tiles — Hospitals, Substations, Roads cut.
 *
 * **A tile, not a list row, and that is the point of the redesign.** The old
 * `ReadoutPanel` spent its vertical space on a heading, three figures, a
 * multi-line raster note and then three rows whose numbers were the same three
 * numbers — so the answer to "what gets hit" was below the fold on a phone.
 * Three tiles put all three counts above the fold at once, which is the one
 * thing a judge takes away from the screen.
 *
 * The honesty work did not shrink, it moved:
 *
 *   - **Loading shows `—`, not `0`.** A dash is "not known"; a zero is a
 *     measurement. During the fetch the previous chip's numbers must be gone,
 *     and rendering them would put category 6's counts under a chip labelled
 *     "Remal". The spinner beside the row says which it is.
 *   - **Zero shows `0` *and* the empty-state line.** Zero is the honest
 *     reading at categories 0-3 and at the preset's borrowed band, where the
 *     flood genuinely reaches no asset. The line is the specified copy.
 *   - **The unit line differs for roads.** A hospital is *submerged*; a road is
 *     *cut off by water* it need not be standing in. Same number, different
 *     claim, so the captions are not interchangeable.
 */
export interface ExposureTilesProps {
  counts: TileInput;
  loading: boolean;
}

export function ExposureTiles({ counts, loading }: ExposureTilesProps) {
  const tiles = buildTiles(counts);
  const anyCount = tiles.some((t) => t.count !== null && t.count > 0);
  const showEmpty = !loading && !anyCount;

  return (
    <View>
      <View style={styles.row}>
        {tiles.map((tile, i) => {
          const known = tile.count !== null;
          return (
            // The last tile drops its right margin, or the row carries a
            // trailing gutter that makes the three look left-shifted.
            <View key={tile.id} style={[styles.tile, i === tiles.length - 1 ? styles.tileLast : null]}>
              <Text style={styles.value}>{known ? tile.count : '—'}</Text>
              <Text style={styles.label}>{tile.label}</Text>
              {/*
                The unit line is suppressed at zero, where "0 submerged" is
                true but reads as a measurement of something that was checked
                and found absent. The empty-state line below carries the claim
                instead, and it says "modelled".
              */}
              {known && tile.count !== 0 ? (
                <Text style={styles.unit}>{tile.unit}</Text>
              ) : null}
            </View>
          );
        })}
      </View>

      {loading ? (
        <View style={styles.statusRow}>
          <ActivityIndicator size="small" color={theme.colors.textMuted} />
          <Text style={styles.statusText}>Checking exposure…</Text>
        </View>
      ) : null}

      {showEmpty ? <Text style={styles.empty}>{EMPTY_EXPOSURE_LINE}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
  },
  tile: {
    // Three across with `space-between` rather than `flex: 1` on each: the
    // numbers are 1-3 digits and a tile that stretches to fill would put a
    // single "1" in the middle of a very wide box. Equal `flex` with a small
    // gap keeps them reading as a row of three without the numbers drifting
    // apart.
    flex: 1,
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.border,
    paddingVertical: theme.spacing.xs,
    paddingHorizontal: theme.spacing.xs / 2,
    marginRight: theme.spacing.xs / 2,
  },
  tileLast: {
    marginRight: 0,
  },
  value: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.heading,
    color: theme.colors.text,
  },
  label: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginTop: 2,
  },
  unit: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: 1,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: theme.spacing.xs,
  },
  statusText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginLeft: theme.spacing.xs / 2,
  },
  empty: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs,
  },
});
