import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';

import type { AdvisoryResponse, CachedAdvisory } from '../api';
import {
  cachedBannerText,
  cachedSampleOffer,
  describeAdvisoryError,
  sheltersAreDemoData,
  smsLengthStatus,
  type AdvisoryFailure,
} from '../advisoryFlow';
import { theme } from '../theme';
import { AdvisoryModal } from './AdvisoryModal';
import { GhostButton } from './GhostButton';
import { PriorityChip, type PriorityLevel } from './PriorityChip';

/**
 * The advisory's body, composed into the existing `AdvisoryModal` shell.
 *
 * Two states, and they are genuinely different screens rather than one screen
 * with an error banner: a successful advisory is a document to read, and a
 * failure is a reason the document does not exist. The failure copy comes
 * from `describeAdvisoryError`, which branches on `ApiErrorKind`.
 *
 * The modal is reused for both rather than a separate error surface, so the
 * failure has room for the things it needs to say: the honesty-check
 * violations by name, and — for a capacity block — a live countdown.
 */

export type AdvisoryOutcome =
  | { status: 'loading' }
  | { status: 'ready'; response: AdvisoryResponse; capturedAt: string | null }
  | { status: 'failed'; failure: AdvisoryFailure; retryAfterSeconds: number | null };

export interface AdvisoryContentProps {
  outcome: AdvisoryOutcome | null;
  /** `/allocation`'s `shelter_status`, or null while it is still loading. */
  shelterStatus: Record<string, unknown> | null;
  /** The bundled capture, or null when none has been made. Decides the offer. */
  cachedSample: CachedAdvisory | null;
  /** Re-send the request. Only ever wired to an explicit press. */
  onRetry: () => void;
  /** Show the bundled capture instead. Only rendered when one exists. */
  onLoadCached: () => void;
  onClose: () => void;
}

export function AdvisoryContent({
  outcome,
  shelterStatus,
  cachedSample,
  onRetry,
  onLoadCached,
  onClose,
}: AdvisoryContentProps) {
  const open = outcome !== null;
  const title =
    outcome !== null && outcome.status === 'ready'
      ? `Advisory · ${outcome.response.generated_for.imd_category}`
      : 'District Advisory';

  return (
    <AdvisoryModal visible={open} onClose={onClose} title={title}>
      {outcome === null ? null : outcome.status === 'loading' ? (
        <LoadingBody />
      ) : outcome.status === 'failed' ? (
        <FailureBody
          failure={outcome.failure}
          retryAfterSeconds={outcome.retryAfterSeconds}
          onRetry={onRetry}
          cachedSample={cachedSample}
          onLoadCached={onLoadCached}
        />
      ) : (
        <AdvisoryBody
          response={outcome.response}
          shelterStatus={shelterStatus}
          capturedAt={outcome.capturedAt}
        />
      )}
    </AdvisoryModal>
  );
}

function LoadingBody() {
  return (
    <View style={styles.block}>
      {/* No seconds-based estimate here. The backend's worst case is 60-90s
          (6 Gemini calls across two capacity ladders, 12s of backoff between
          them), so any number short of that would be a promise it cannot
          keep — and the real elapsed time is already visible in the button's
          own spinner on the screen behind this sheet. */}
      <Text style={styles.paragraph}>
        Generating. This spends a Gemini call and can take a minute or two — the
        request is retried with backoff if the model is busy.
      </Text>
    </View>
  );
}

function FailureBody({
  failure,
  retryAfterSeconds,
  onRetry,
  cachedSample,
  onLoadCached,
}: {
  failure: AdvisoryFailure;
  retryAfterSeconds: number | null;
  onRetry: () => void;
  cachedSample: CachedAdvisory | null;
  onLoadCached: () => void;
}) {
  const remaining = useCountdown(retryAfterSeconds);
  const offer = cachedSampleOffer(cachedSample);

  return (
    <View style={styles.block}>
      <Text style={styles.failureTitle}>{failure.title}</Text>
      <Text style={styles.paragraph}>{failure.body}</Text>

      {/*
        The countdown appears for capacity and nothing else. `retryAfterSeconds`
        is non-null only for a 503, and quota deliberately arrives with it
        forced to null — so this cannot start counting down to midnight.
      */}
      {remaining === null ? null : (
        <Text style={styles.countdown}>
          Retry in about {remaining}s
        </Text>
      )}

      {failure.violations.length === 0 ? null : (
        <View style={styles.violations}>
          <Text style={styles.violationsHeading}>What the check caught:</Text>
          {failure.violations.map((violation, i) => (
            <Text key={i} style={styles.violation}>
              • {violation}
            </Text>
          ))}
        </View>
      )}

      {/*
        `wait` and `reload` are deliberately not buttons. A quota failure has
        no button by design — see `describeAdvisoryError`.
      */}
      {failure.action.type === 'retry' ? (
        <GhostButton label={failure.action.label} onPress={onRetry} />
      ) : (
        <Text style={styles.actionNote}>{failure.action.label}</Text>
      )}

      {/*
        The cached fallback, and the only thing on this screen that shows the
        user anything when a failure offers no action of its own — a quota
        block is exactly that case, and without this the app is simply blank
        for the rest of the day.

        `cachedSampleOffer` decides; this only renders. `available` is false
        when no capture is bundled, and then the element does not exist at
        all rather than existing and failing.
      */}
      {offer.available ? (
        <View style={styles.cachedOffer}>
          <GhostButton label={offer.label} onPress={onLoadCached} />
          <Text style={styles.cachedOfferNote}>{offer.note}</Text>
        </View>
      ) : null}
    </View>
  );
}

function AdvisoryBody({
  response,
  shelterStatus,
  capturedAt,
}: {
  response: AdvisoryResponse;
  shelterStatus: Record<string, unknown> | null;
  /** Non-null when this is a stored capture rather than a live answer. */
  capturedAt: string | null;
}) {
  const { advisory, generated_for: forWhom } = response;
  const isDemo = sheltersAreDemoData(shelterStatus);

  return (
    <View style={styles.block}>
      {/*
        Above the provenance line, so it is the first thing read and cannot be
        scrolled past before the numbers. `capturedAt` is null for a live
        advisory and non-null for a capture, so the banner's presence is the
        signal — there is no way to render this body as a capture without it.
      */}
      {capturedAt === null ? null : (
        <View style={styles.cachedBanner}>
          <Text style={styles.cachedBannerText}>{cachedBannerText(capturedAt)}</Text>
        </View>
      )}

      <Text style={styles.provenance}>
        {forWhom.imd_category} · {forWhom.wind_kmph} kmph · written for{' '}
        {forWhom.origin.name}
      </Text>

      {/*
        The disclosure, placed above the advice rather than in a footer. It has
        to be read before the numbers below it, and a notice a reader can
        scroll past has not disclosed anything.
      */}
      {isDemo ? (
        <View style={styles.notice}>
          <Text style={styles.noticeHeading}>Shelter data is not verified</Text>
          <Text style={styles.noticeBody}>
            The shelters and capacities behind this plan are invented placeholders
            used to demonstrate the allocation algorithm, not real cyclone
            shelters. Do not use this to direct real evacuations.
          </Text>
        </View>
      ) : null}

      {/*
        Prominent, and above the summary: when the origin cannot be reached,
        the rest of the advisory is about a place the reader may not be able
        to leave. That fact changes how every line under it should be read.
      */}
      {forWhom.origin_reachable ? null : (
        <View style={styles.unreachable}>
          <Text style={styles.unreachableHeading}>No flood-free route from here</Text>
          <Text style={styles.unreachableBody}>{forWhom.origin_reason}</Text>
        </View>
      )}

      <Section heading="Summary">
        <Text style={styles.paragraph}>{advisory.executive_summary}</Text>
      </Section>

      <Section heading="Evacuation priorities">
        {advisory.evacuation_plan.map((item, i) => (
          <View key={`${item.locality_name}-${i}`} style={styles.planRow}>
            <PriorityChip level={item.priority_level as PriorityLevel} />
            <Text style={styles.planLocality}>{item.locality_name}</Text>
            <Text style={styles.planReasoning}>{item.reasoning}</Text>
          </View>
        ))}
      </Section>

      <Section heading="After landfall">
        <Text style={styles.paragraph}>{advisory.post_landfall_risks}</Text>
      </Section>

      <Section heading="Historical context">
        <Text style={styles.paragraph}>{advisory.historical_context}</Text>
      </Section>

      <SmsBlock draft={advisory.sms_dispatch_draft} />
    </View>
  );
}

/**
 * The dispatch draft, with its live length and a copy button.
 *
 * The button copies the draft **exactly** — no prefix, no suffix, no
 * "Generated by ..." trailer, nothing appended. The draft is written to be
 * pasted into an existing district dispatch system, where a line the app
 * added would be read out to the public as if the district had sent it.
 */
function SmsBlock({ draft }: { draft: string }) {
  const [copied, setCopied] = useState(false);
  const status = smsLengthStatus(draft);

  useEffect(() => {
    if (!copied) return undefined;
    // Reset the confirmation. A permanent "Copied" is worse than none once
    // the user has long since pasted.
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const onCopy = useCallback(() => {
    // Fire and forget: the promise resolves true on both platforms, and a
    // failure here has no useful recovery the user could act on.
    void Clipboard.setStringAsync(draft);
    setCopied(true);
  }, [draft]);

  return (
    <Section heading="SMS dispatch draft">
      <View style={styles.smsBlock}>
        <Text style={styles.smsDraft}>{draft}</Text>
        <View style={styles.smsFooter}>
          <Text style={[styles.smsCount, status.over ? styles.smsCountOver : null]}>
            {status.label}
          </Text>
          <GhostButton label={copied ? 'Copied' : 'Copy'} onPress={onCopy} />
        </View>
        {status.over ? (
          <Text style={styles.smsWarning}>
            Over {status.limit} characters. Copy is still available — the draft
            is the model's text and you may want to send it as it stands.
          </Text>
        ) : null}
      </View>
    </Section>
  );
}

function Section({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionHeading}>{heading}</Text>
      {children}
    </View>
  );
}

/**
 * Ticks down from `seconds`, or returns null.
 *
 * Returns null — rather than a frozen zero — for a non-capacity failure, so
 * the countdown cannot outlive the thing it is counting for. Held in this
 * component rather than the parent so the timer stops when the sheet closes.
 */
function useCountdown(seconds: number | null): number | null {
  const [remaining, setRemaining] = useState<number | null>(seconds);

  useEffect(() => {
    setRemaining(seconds);
    if (seconds === null || seconds <= 0) return undefined;
    const timer = setInterval(() => {
      setRemaining((current) => {
        if (current === null) return null;
        if (current <= 1) {
          clearInterval(timer);
          return 0;
        }
        return current - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [seconds]);

  return remaining;
}

const styles = StyleSheet.create({
  block: {
    paddingBottom: theme.spacing.xs,
  },
  provenance: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginBottom: theme.spacing.xs,
  },
  section: {
    marginTop: theme.spacing.sm,
  },
  sectionHeading: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginBottom: theme.spacing.xs / 2,
  },
  paragraph: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  notice: {
    backgroundColor: theme.colors.caution,
    borderRadius: theme.radius.button,
    padding: theme.spacing.sm,
  },
  noticeHeading: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginBottom: theme.spacing.xs / 2,
  },
  noticeBody: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
  unreachable: {
    backgroundColor: theme.colors.danger,
    borderRadius: theme.radius.button,
    padding: theme.spacing.sm,
    marginTop: theme.spacing.xs,
  },
  unreachableHeading: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.card,
    marginBottom: theme.spacing.xs / 2,
  },
  unreachableBody: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.card,
  },
  planRow: {
    marginBottom: theme.spacing.xs,
  },
  planLocality: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.text,
    marginTop: theme.spacing.xs / 2,
  },
  planReasoning: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  smsBlock: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    padding: theme.spacing.sm,
  },
  smsDraft: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.body,
    color: theme.colors.text,
  },
  smsFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: theme.spacing.xs,
  },
  smsCount: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  smsCountOver: {
    color: theme.colors.danger,
    fontFamily: theme.fonts.bodySemibold,
  },
  smsWarning: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
  failureTitle: {
    fontFamily: theme.fonts.heading,
    fontSize: theme.typography.heading,
    color: theme.colors.danger,
    marginBottom: theme.spacing.xs,
  },
  countdown: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs,
  },
  violations: {
    marginTop: theme.spacing.sm,
    padding: theme.spacing.sm,
    borderRadius: theme.radius.button,
    backgroundColor: theme.colors.background,
  },
  violationsHeading: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
    marginBottom: theme.spacing.xs / 2,
  },
  violation: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  actionNote: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.sm,
  },
  cachedOffer: {
    marginTop: theme.spacing.sm,
  },
  cachedOfferNote: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
    marginTop: theme.spacing.xs / 2,
  },
  cachedBanner: {
    backgroundColor: theme.colors.caution,
    borderRadius: theme.radius.button,
    padding: theme.spacing.sm,
    marginBottom: theme.spacing.xs,
  },
  cachedBannerText: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.caption,
    color: theme.colors.text,
  },
});
