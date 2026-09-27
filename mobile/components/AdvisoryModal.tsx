import React from 'react';
import { Modal, ScrollView, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { GhostButton } from './GhostButton';

/**
 * Advisory modal shell — Design.md: "`card` background, 16px radius, single
 * soft shadow".
 *
 * Deliberately content-agnostic: it takes children rather than a
 * `DistrictAdvisory`. The advisory's shape belongs to the API contract
 * (Module C) and the Gemini schema (Module D); binding this component to
 * either would make a presentational primitive depend on backend modules
 * that do not exist yet. Compose `PriorityChip` and `GhostButton` as
 * children instead.
 */
export interface AdvisoryModalProps {
  visible: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
}

export function AdvisoryModal({ visible, onClose, title = 'District Advisory', children }: AdvisoryModalProps) {
  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>{title}</Text>
            <GhostButton label="Close" onPress={onClose} />
          </View>
          <ScrollView
            style={styles.body}
            contentContainerStyle={styles.bodyContent}
            showsVerticalScrollIndicator={false}
          >
            {children}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: theme.colors.text,
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: theme.colors.card,
    borderTopLeftRadius: theme.radius.card,
    borderTopRightRadius: theme.radius.card,
    padding: theme.spacing.sm,
    maxHeight: '80%',
    // Design.md's "single soft shadow" — React Native's single-shadow model.
    ...theme.shadow.card,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: theme.spacing.xs,
  },
  title: {
    flex: 1,
    fontFamily: theme.fonts.heading,
    color: theme.colors.text,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
  body: {
    flexGrow: 0,
  },
  bodyContent: {
    paddingBottom: theme.spacing.sm,
  },
});
