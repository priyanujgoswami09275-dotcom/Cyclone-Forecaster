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
 * **Two layouts, one component.** `stackedCards` (default off, the native
 * default) selects the original 6-column table. When the Web screen passes
 * `stackedCards`, each scenario renders as a labelled card — the IMD class
 * name and wind as the header, then labelled rows for surge, flooded land,
 * and the three separate exposure counts. No bare numbers, no raw ids, no
 * summed "137 exposed" — a reader sees exactly what each figure counts.
 *
 * **Every delta is labelled with both ends of its comparison.** A delta
 * without its two operands is a number a reader cannot check, and an
 * uncheckable number in a risk tool is worse than no number.
 *
 * **A zero flooded area reads as `0`, never as a gap** — see `exposureText`.
 */
export interface ScenarioComparePanelProps {
  rows: ComparisonEntry[];
  loading: boolean;
  error: ApiError | null;
  /** Render stacked labelled cards instead of the 6-column table. Web-only. */
  stackedCards?: boolean;
}

/** Total exposed assets, the figure the delta is taken on. */
function exposed(entry: ComparisonEntry): number {
  return entry.hospitals_exposed + entry.substations_exposed + entry.roads_cut_off;
}

/** The plain label for one scenario — IMD class name, or "Observed storm". */
function scenarioLabel(entry: ComparisonEntry): string {
  if (entry.scenario_kind === 'observed') return 'Observed storm';
  return entry.imd_category;
}

/** One stacked card: a labelled header and labelled figure rows. */
function StackedCard({ entry }: { entry: ComparisonEntry }) {
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle}>{scenarioLabel(entry)}</Text>
        <Text style={styles.cardWind}>{`${entry.wind_kmph.toFixed(0)} km/h`}</Text>
      </View>
      <View style={styles.cardBody}>
        <Text style={styles.cardRow}>
          <Text style={styles.cardLabel}>Storm surge: </Text>
          <Text style={styles.cardValue}>{`${entry.surge_m.toFixed(2)} m`}</Text>
        </Text>
        <Text style={styles.cardRow}>
          <Text style={styles.cardLabel}>Land flooded: </Text>
          <Text style={styles.cardValue}>
            {exposureText({ flooded_km2: entry.flood_area_km2 })}
          </Text>
        </Text>
        <Text style={styles.cardRow}>
          <Text style={styles.cardLabel}>Hospitals reached: </Text>
          <Text style={styles.cardValue}>{String(entry.hospitals_exposed)}</Text>
        </Text>
        <Text style={styles.cardRow}>
          <Text style={styles.cardLabel}>Substations reached: </Text>
          <Text style={styles.cardValue}>{String(entry.substations_exposed)}</Text>
        </Text>
        <Text style={styles.cardRow}>
          <Text style={styles.cardLabel}>Road segments reached: </Text>
          <Text style={styles.cardValue}>{String(entry.roads_cut_off)}</Text>
        </Text>
      </View>
    </View>
  );
}

export function ScenarioComparePanel({
  rows,
  loading,
  error,
  stackedCards = false,
}: ScenarioComparePanelProps) {
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
      {stackedCards ? (
        /*
          The Web layout: one labelled card per scenario, stacked vertically.
          At 390 px a 6-column table wraps numbers into adjacent columns and
          the raw scenario ids ("cat5", "observed") read as jargon; the cards
          carry the IMD class name, a labelled wind, and the three separate
          exposure counts — never a summed bare number.
        */
        <View style={styles.cards}>
          {rows.map((entry) => (
            <StackedCard key={entry.cyclone_id} entry={entry} />
          ))}
        </View>
      ) : (
        /*
          The original table layout (native default). Each row is one
          scenario; the cells are name, scenario_id, wind, surge, area,
          and the total exposed count.
        */
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
      )}

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
  cards: {
    gap: theme.spacing.sm,
  },
  card: {
    backgroundColor: theme.colors.background,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    overflow: 'hidden',
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: theme.spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
  },
  cardTitle: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    flex: 1,
  },
  cardWind: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.selectedText,
  },
  cardBody: {
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: theme.spacing.xs,
    gap: 4,
  },
  cardRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  cardLabel: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  cardValue: {
    fontFamily: theme.fonts.bodyMedium,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
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
