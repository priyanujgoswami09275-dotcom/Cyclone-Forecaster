/**
 * ESLint flat config.
 *
 * Exactly one rule is enabled, as an error: `react-hooks/exhaustive-deps`.
 * That rule is the only automated check this codebase has for the class of
 * bug where a `useCallback`/`useEffect` closes over a value its dependency
 * array omits — the screen then quietly acts on stale state (e.g. an
 * advisory POSTed for a cyclone the user has already switched away from).
 *
 * No other rules are enabled and none are disabled. Anything this config
 * reports is a real finding to be read, not lint noise to be silenced.
 *
 * `typescript-eslint` is here only as the parser: the files are .ts/.tsx
 * and ESLint's default parser cannot read TypeScript syntax. No
 * typescript-eslint *rules* are turned on.
 */
import hooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

export default tseslint.config({
  files: ['**/*.{ts,tsx}'],
  languageOptions: { parser: tseslint.parser },
  plugins: { 'react-hooks': hooks },
  rules: {
    'react-hooks/exhaustive-deps': 'error',
  },
});
