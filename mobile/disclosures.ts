/**
 * App-copy disclosures shared by more than one surface.
 *
 * A pure module by contract: no react-native imports, no view code, no
 * payload shapes — just the strings that both the native About sheet and
 * the web Impact tab render. The sentence lives in ONE place and each
 * surface decides only where to show it, so the two cannot drift apart the
 * way a copy-pasted string would. `node --test` loads this file directly
 * (via `tests/disclosures.test.mjs`), which is the other reason it must
 * stay import-free.
 */

/**
 * The national-border disclosure.
 *
 * The flood engine runs over the DEM bbox, which reaches 89.2°E — into
 * Bangladesh — and nothing clips the extent at the border. The repo has no
 * boundary dataset to clip it with (verified 2026-10-16; see CLAUDE.md's
 * gotchas), so the sentence deliberately carries no number: the measured
 * shares behind it (roughly a fifth of the flooded land at every scenario,
 * plus a handful of counted roads and one hospital) were taken against a
 * provisional lon 89.0 proxy line and would go stale as a hardcoded figure.
 * The sentence is copy; the numbers are documentation.
 *
 * Rendered by the native About sheet's flood-limitation block and by the
 * web Impact tab next to the flooded-area figure — pinned verbatim by
 * `tests/disclosures.test.mjs` and `tests/aboutSheet.test.mjs`.
 */
export const BORDER_DISCLOSURE =
  'The flood extent is not clipped at the national border, so part of the ' +
  'flooded area and some listed facilities east of about 89 degrees E lie in ' +
  'Bangladesh.';
