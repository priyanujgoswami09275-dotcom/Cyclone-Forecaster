import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  useFonts,
} from '@expo-google-fonts/inter';
import { RobotoSlab_700Bold } from '@expo-google-fonts/roboto-slab';

import { theme } from './theme';
import { MapScreen } from './components/MapScreen';

/**
 * App root. Owns exactly one responsibility for the design system: the font
 * gate. Per Design.md ("Font setup (Expo)"), nothing renders until the four
 * families named in `theme.fonts` have resolved — otherwise the first paint
 * falls back to the system font and every later frame re-lays-out.
 *
 * Stage 2 (the map screen) mounted here on 2026-09-28. It is the first of
 * the app's two screens; the advisory modal is the second and opens from the
 * "Generate Advisory" button, which Stage 3 wires to `POST /advisory`.
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

  return <MapScreen />;
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
  errorText: {
    color: theme.colors.danger,
    textAlign: 'center',
    paddingHorizontal: theme.spacing.sm,
  },
});
