/**
 * Design tokens — copied verbatim from Design .md ("React Native theme
 * object"). Do not add, rename, or retune a value here without changing
 * Design.md first; this file is an implementation of that spec, not a
 * place to make design decisions.
 *
 * Anything a component needs that is NOT in this object is a gap in
 * Design.md. Per the session rule, such gaps get logged in MEMORY.md
 * under "Flagged for review" — they do not get a value invented here.
 */
export const theme = {
  colors: {
    background: '#faf5e8',
    card: '#ffffff',
    text: '#181d26',
    textMuted: '#525965',
    border: '#e0e2e6',
    primary: '#1b61c9',
    water: '#254fad',
    waterFill: '#c7e5f2',
    danger: '#aa2d00',
    dangerDark: '#912e1f',
    safe: '#0a2e0e',
    caution: '#fcb42a',
  },
  radius: { button: 12, card: 16, chip: 32 },
  spacing: { xs: 8, sm: 16, md: 24, lg: 32, xl: 40, xxl: 64 },
  shadow: {
    card: {
      shadowColor: '#0f306a',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.08,
      shadowRadius: 12,
      elevation: 3, // Android
    },
  },
  fonts: {
    heading: 'RobotoSlab_700Bold',
    body: 'Inter_400Regular',
    bodyMedium: 'Inter_500Medium',
    bodySemibold: 'Inter_600SemiBold',
  },
  // Added to resolve "Flagged for review" #1 — Design.md's typography table
  // gave ranges (heading 20-24px, body 15-16px, caption 12-13px) without
  // picking concrete values. These are the chosen points within each range;
  // update Design.md's table to match if these are revised.
  typography: {
    heading: 22,
    body: 16,
    emphasis: 16, // button labels — same size as body, bodySemibold weight
    caption: 13,
  },
};
