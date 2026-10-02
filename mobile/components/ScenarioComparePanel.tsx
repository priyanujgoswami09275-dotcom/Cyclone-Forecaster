import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import type { ApiError } from '../api';
import type { ComparisonEntry } from '../cycloneModel';
import { comparisonDeltas, exposureText, comparisonFooterText } from '../scenarioCompare.ts';

/**
 * The comparison sheet — the same scenario applied to several cyclones, side by
 * side, with the delta between them.
 *
 * **Content only.** It renders a `View`, not a `Modal`, because the two
 * platforms present it differently and this component must be web-safe: the
 * native screen wraps it in `AdvisoryModal`, the Web screen renders it inline
 * beside the map. Binding it to `Modal` would make it native-only and the Web
 * screen could not use it — the same reason `AdvisoryPanel` renders inline
 * rather than in a modal.
 *
 * **Every row is labelled with both ends of its comparison.** A delta without
 * its two operands is a number a reader cannot check, and an uncheckable
 * number in a risk tool is worse than no number.
 *
 * **A zero flooded area reads as `0`, never as a gap** — see `exposureText`.
 */
export interface ScenarioComparePanelProps {
  rows: ComparisonEntry[];
  loading: boolean;
  error: ApiError | null;
}

/** Total exposed assets, the figure the delta is taken on. */
function exposed(entry: ComparisonEntry): number {
  return entry.hospitals_exposed + entry.substations_exposed + entry.roads_cut_off;
}

export function ScenarioComparePanel({ rows, loading, error }: ScenarioComparePanelProps) {
  if (loading) {
    return (
      <View style={styles.row}>
        <ActivityIndicator size="small" color={theme.colors.textMuted} />
        <Text style={styles.muted}>Computing both scenarios…</Text>
      </View>
    );
  }

  if (error) {
    return <Text style={styles.error}>{error.message}</Text>;
  }

  if (rows.length === 0) {
    return <Text style={styles.muted}>No cyclones selected.</Text>;
  }

  const deltas = comparisonDeltas(
    rows.map((r) => ({
      cyclone_id: r.cyclone_id,
      scenario_id: r.scenario_id,
      surge_m: r.surge_m,
      exposed: exposed(r),
    })),
  );

  return (
    <View>
      <View style={styles.table}>
        {rows.map((entry) => (
          <View key={entry.cyclone_id} style={styles.row}>
            <Text style={styles.cellName} numberOfLines={1}>
              {entry.name ?? entry.cyclone_id}
            </Text>
            <Text style={styles.cell}>{entry.scenario_id}</Text>
            <Text style={styles.cell}>{`${entry.wind_kmph.toFixed(0)} km/h`}</Text>
            <Text style={styles.cell}>{`${entry.surge_m.toFixed(2)} m`}</Text>
            <Text style={styles.cell}>{exposureText({ flooded_km2: entry.flood_area_km2 })}</Text>
            <Text style={styles.cell}>{String(exposed(entry))}</Text>
          </View>
        ))}
      </View>

      {deltas.length > 0 ? (
        <View style={styles.deltas}>
          {deltas.map((d) => (
            <Text key={d.label} style={styles.delta}>
              {d.label}: {d.surge_delta_m >= 0 ? '+' : ''}
              {d.surge_delta_m.toFixed(2)} m surge, {d.exposed_delta >= 0 ? '+' : ''}
              {d.exposed_delta} exposed
            </Text>
          ))}
        </View>
      ) : null}

      <Text style={styles.footer}>{comparisonFooterText()}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  table: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: theme.spacing.xs / 2,
    paddingHorizontal: theme.spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
  },
  cellName: {
    flex: 2,
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  cell: {
    flex: 1,
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textAlign: 'right',
  },
  deltas: {
    marginTop: theme.spacing.xs,
  },
  delta: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  footer: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.sm,
  },
  muted: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  error: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.danger,
  },
});
