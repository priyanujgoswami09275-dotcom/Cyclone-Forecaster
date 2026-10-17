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
 * Since 2026-10-17 the flood engine subtracts the committed OpenStreetMap
 * Bangladesh polygon (cell-centre rule) from every reported mask, so the
 * figures leave Bangladeshi land out. The sentence says the two things a
 * reader needs: what was left out, and the honest limit of that cut — the
 * OSM border is a line drawn where rivers and coastlines move, so a cell
 * near it can fall either side. It deliberately carries no numbers; the
 * measured effect of the subtraction lives in CLAUDE.md's gotchas as
 * documentation, where it can be re-measured without a copy change.
 *
 * Rendered by the native About sheet's flood-limitation block and by the
 * web Impact tab next to the flooded-area figure — pinned verbatim by
 * `tests/disclosures.test.mjs` and `tests/aboutSheet.test.mjs`.
 */
export const BORDER_DISCLOSURE =
  "Flooded land inside Bangladesh is left out, using OpenStreetMap's " +
  'border. The border is approximate at the coast and along rivers, so ' +
  'figures near it can be slightly off.';
