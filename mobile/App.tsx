import { ActivityIndicator, Platform, StyleSheet, Text, View } from 'react-native';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  useFonts,
} from '@expo-google-fonts/inter';

import { theme } from './theme';
import { MapScreen } from './components/MapScreen';

/**
 * App root. Owns exactly one responsibility for the design system: the font
 * gate. Per Design.md ("Font setup (Expo)"), nothing renders until the three
 * Inter faces named in `theme.fonts` have resolved — otherwise the first paint
 * falls back to the system font and every later frame re-lays-out.
 *
 * **Inter only, as of 2026-09-30.** `RobotoSlab_700Bold` and the
 * `@expo-google-fonts/roboto-slab` import were removed with the light theme:
 * the dark design calls for a single family, and the app's headings are a
 * weight distinction rather than a typeface one. Dropping the import also means
 * the slab face is no longer downloaded at boot.
 *
 * Stage 2 (the map screen) mounted here on 2026-09-28. It is the first of
 * the app's two screens; the advisory modal is the second and opens from the
 * "Generate Advisory" button, which Stage 3 wires to `POST /advisory`.
 */
/**
 * The faces `theme.fonts` names. Declared once so the load call below cannot
 * drift from the token table — a face named in `theme.ts` but absent here is a
 * silent fallback, which is exactly the bug this gate exists to prevent.
 */
const INTER_FACES = {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
} as const;

export default function App() {
  /**
   * **The same three faces load on every platform, including web.** Fixed
   * 2026-10-01.
   *
   * This used to be `useFonts(Platform.OS === 'web' ? {} : {...})`, which
   * loaded **nothing** on web. That was deliberate — the motivation was to
   * stop a font failure blocking web rendering — but it had a side effect
   * nobody measured: on web `expo-font` registers its `@font-face` rules *as a
   * side effect of loading a face*, so passing an empty object meant **zero
   * faces were ever registered**. Every `fontFamily: theme.fonts.body` in the
   * app was naming a family the browser had never heard of, and the browser
   * fell back to its default serif.
   *
   * Confirmed on the deployed app rather than inferred: `document.fonts`
   * listed **0 registered faces**, and the computed `font-family` of an
   * on-screen element was `"Times"`. The `@font-face` template was present in
   * the bundle — it was simply never called.
   *
   * So the fonts load everywhere, and the *gate* — the part that blocks
   * rendering — stays native-only below. That keeps the original requirement
   * intact: a font failure on web can never leave a judge looking at a blank
   * page, and the app renders in the system font instead.
   */
  const [fontsLoaded, fontError] = useFonts(INTER_FACES);

  // A font load failure is surfaced rather than swallowed. Gating on
  // `fontsLoaded` alone would leave a permanently blank app, which is the
  // worst possible failure mode in a live demo.
  //
  // **Both branches stay native-only.** On web we render regardless, because a
  // font that never arrives should degrade to the system font and nothing
  // worse. Blocking here is what the previous web build effectively did, by
  // loading nothing at all and then treating the fonts as fine.
  if (Platform.OS !== 'web' && fontError) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>Fonts failed to load: {fontError.message}</Text>
      </View>
    );
  }

  if (Platform.OS !== 'web' && !fontsLoaded) {
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
