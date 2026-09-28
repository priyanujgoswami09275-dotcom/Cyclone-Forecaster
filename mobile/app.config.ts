import type { ExpoConfig, ConfigContext } from 'expo/config';

/**
 * Dynamic app config. `app.json` stays the source of truth for everything
 * that is not a secret; this file only adds what must not be committed.
 *
 * Why this file exists
 * --------------------
 * `react-native-maps` uses the **Google Maps SDK** on Android, and that SDK
 * refuses to draw without an authorised API key. The usual instruction is to
 * put the key in `app.json` under `android.config.googleMaps.apiKey` — which
 * would put a real, billable Google credential in a tracked file. The project
 * rule is that no key is ever committed (`GEMINI_API_KEY` is a Render
 * dashboard secret; `.env` is gitignored and `.env.example` is the committed
 * template). This is the same rule applied to a second key, and the same
 * solution: read it from the environment, leave it undefined when unset.
 *
 * When `GOOGLE_MAPS_ANDROID_API_KEY` is unset, `android.config.googleMaps` is
 * simply absent and the config is byte-identical to `app.json` — so a
 * developer who never sets it gets today's behaviour, not a new failure.
 *
 * Scope and what is unverified
 * ----------------------------
 * In **Expo Go** on Android the key bundled with the Expo Go app is generally
 * used instead of yours, so development usually works without this set. It
 * matters for any **development or production build** (`eas build`), which
 * has no Expo Go to borrow a key from. This could not be verified on a device
 * — see MEMORY.md "Flagged for review" and the run instructions for item 3.
 */
export default ({ config }: ConfigContext): ExpoConfig => {
  const key = process.env.GOOGLE_MAPS_ANDROID_API_KEY?.trim();

  if (!key) return config as ExpoConfig;

  return {
    ...config,
    android: {
      ...config.android,
      config: {
        ...config.android?.config,
        googleMaps: { apiKey: key },
      },
    },
  } as ExpoConfig;
};
