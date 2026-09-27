import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  useFonts,
} from '@expo-google-fonts/inter';
import { RobotoSlab_700Bold } from '@expo-google-fonts/roboto-slab';

import { theme } from './theme';

/**
 * App root. Owns exactly one responsibility for the design system: the font
 * gate. Per Design.md ("Font setup (Expo)"), nothing renders until the four
 * families named in `theme.fonts` have resolved — otherwise the first paint
 * falls back to the system font and every later frame re-lays-out.
 *
 * The real map screen replaces the placeholder below (Task.md, Module E).
 * This is not a screen; it is the themed shell those screens mount into.
 */
export default function App() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    RobotoSlab_700Bold,
  });

  // A font load failure is surfaced rather than swallowed. Gating on
  // `fontsLoaded` alone would leave a permanently blank app, which is the
  // worst possible failure mode in a live demo.
  if (fontError) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>Fonts failed to load: {fontError.message}</Text>
      </View>
    );
  }

  if (!fontsLoaded) {
    return (
      <View style={styles.container}>
        <ActivityIndicator color={theme.colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Cyclone Impact Forecaster</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
  heading: {
    fontFamily: theme.fonts.heading,
    color: theme.colors.text,
    // fontSize: omitted on purpose — see MEMORY.md "Flagged for review".
  },
  errorText: {
    color: theme.colors.danger,
    textAlign: 'center',
    paddingHorizontal: theme.spacing.sm,
  },
});
