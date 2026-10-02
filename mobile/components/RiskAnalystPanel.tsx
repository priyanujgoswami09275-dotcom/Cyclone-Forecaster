import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import type { ApiError } from '../api';
import type { EvidenceKind, RiskAnalystResponse, RiskSeverity } from '../cycloneModel';
import { mlEstimateLabel } from '../scenarioCompare.ts';

/**
 * The AI Risk Analyst's output, rendered as an operational document.
 *
 * **Content only**, for the same reason as `ScenarioComparePanel`: the native
 * screen wraps it in `AdvisoryModal`, the Web screen renders it inline, and a
 * `Modal` here would make the component native-only.
 *
 * **Every finding carries its evidence kind, as a chip.** Not as a footnote
 * and not as prose: the four kinds are the difference between a figure this
 * service computed, a historical fact, a model's estimate, and general
 * knowledge — and a reader who cannot tell them apart will read the most
 * flattering one into every row. The chip is the admission, and it is
 * rendered rather than described because a rendered chip cannot be left out
 * on the run where it mattered.
 *
 * **The ML estimate is labelled by `mlEstimateLabel`**, so a baseline-losing
 * model reads "median baseline (not a prediction)" and never "predicted".
 */
export interface RiskAnalystPanelProps {
  result: RiskAnalystResponse | null;
  loading: boolean;
  error: ApiError | null;
}

/** Evidence kind → what the chip says. */
const EVIDENCE_LABELS: Record<EvidenceKind, string> = {
  computed: 'computed',
  historical: 'historical',
  model_estimate: 'model estimate',
  general_knowledge: 'general knowledge',
};

/** Severity → colour. The dark palette supplies two severity tokens. */
const SEVERITY_COLOURS: Record<RiskSeverity, string> = {
  CRITICAL: theme.colors.danger,
  HIGH: theme.colors.danger,
  MEDIUM: theme.colors.caution,
  LOW: theme.colors.border,
};

export function RiskAnalystPanel({ result, loading, error }: RiskAnalystPanelProps) {
  if (loading) {
    return (
      <View style={styles.row}>
        <ActivityIndicator size="small" color={theme.colors.textMuted} />
        <Text style={styles.muted}>Analysing the computed figures…</Text>
      </View>
    );
  }

  if (error) {
    return <Text style={styles.error}>{error.message}</Text>;
  }

  if (!result) return null;

  const { analysis, peak_estimate } = result;

  return (
    <View>
      <Text style={styles.summary}>{analysis.summary}</Text>

      <View style={styles.findings}>
        {analysis.findings.map((finding, i) => (
          <View key={i} style={styles.finding}>
            <View style={styles.findingHeader}>
              <Text
                style={[styles.severity, { color: SEVERITY_COLOURS[finding.severity] }]}
              >
                {finding.severity}
              </Text>
              <Text style={styles.evidenceChip}>{EVIDENCE_LABELS[finding.evidence_kind]}</Text>
            </View>
            <Text style={styles.findingText}>{finding.finding}</Text>
            <Text style={styles.evidenceText}>{finding.evidence}</Text>
          </View>
        ))}
      </View>

      <View style={styles.mlRow}>
        <Text style={styles.mlLabel}>Storm peak intensity</Text>
        <Text style={styles.mlValue}>
          {`${peak_estimate.estimate_kt.toFixed(0)} kt — ${mlEstimateLabel(peak_estimate)}`}
        </Text>
      </View>

      <Text style={styles.disclaimer}>{analysis.disclaimer}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  summary: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    marginBottom: theme.spacing.sm,
  },
  findings: {
    marginBottom: theme.spacing.sm,
  },
  finding: {
    marginBottom: theme.spacing.sm,
    paddingBottom: theme.spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
  },
  findingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: theme.spacing.xs / 2,
  },
  severity: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    marginRight: theme.spacing.xs,
  },
  evidenceChip: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.chip,
    paddingHorizontal: theme.spacing.xs,
    paddingVertical: 1,
  },
  findingText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  evidenceText: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
  mlRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: theme.spacing.sm,
  },
  mlLabel: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  mlValue: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    textAlign: 'right',
    flex: 1,
    marginLeft: theme.spacing.xs,
  },
  disclaimer: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.caution,
    marginTop: theme.spacing.xs,
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
