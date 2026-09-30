/**
 * Design tokens — the dark theme supplied by the human on 2026-09-30, sampled
 * from their design. These **replace** the light palette that was here before;
 * the old values are recorded in git history and in Design.md's "Replaced"
 * section, not kept as a second set.
 *
 * Do not add, rename, or retune a value here without changing Design.md first;
 * this file is an implementation of that spec, not a place to make design
 * decisions.
 *
 * Anything a component needs that is NOT in this object is a gap in
 * Design.md. Per the session rule, such gaps get logged in MEMORY.md
 * under "Flagged for review" — they do not get a value invented here.
 *
 * **Everything on screen is dark, and the light values are deliberate.** This is
 * a night-time disaster-response tool: the map underneath is the brightest
 * thing on the device, so the chrome is built to sit *around* it rather than
 * compete with it. `land` and `water` are consequently the only light values in
 * the palette, and they exist to tint the basemap, not to fill a surface.
 */
export const theme = {
  colors: {
    /** Near-black. The app's page background. */
    background: '#090909',
    /** One step above background — cards, panels, the map's floating chrome. */
    card: '#0c0c0b',
    /** The primary text colour, a warm off-white. Not pure #fff. */
    text: '#eeedea',
    /** Secondary text and captions. */
    textMuted: '#bcbbaf',
    /** Hairlines and card outlines. Deliberately visible on #090909. */
    border: '#3d3d3c',
    /** Cobalt — the single accent: the primary button, badges, selection. */
    primary: '#0d52c3',
    /** The fill of a selected chip. Darker than `background`. */
    selectedFill: '#011132',
    /** The text of a selected chip. */
    selectedText: '#5f9dea',

    // --- map tints -------------------------------------------------------
    // These are not UI surfaces. They colour the basemap in `customMapStyle`,
    // and nothing else should use them.
    /** Basemap land. A pale, slightly green off-white. */
    land: '#e6ece0',
    /** Basemap water. Pale blue. */
    water: '#c9e0ec',
    /** The flood raster's own tint. */
    flood: '#84a7d3',

    /** Terracotta — damaged/submerged assets, cut-off roads, critical chips. */
    danger: '#a11d00',
    /** Marigold — substations, medium priority. Unchanged from the old theme. */
    caution: '#fcb42a',

    /**
     * Opaque white, for the map's floating buttons and the legend pill.
     *
     * **The one light surface in the app, and it is not a mistake.** These sit
     * *on top of* the pale basemap, and a dark chip on a `#e6ece0` landmass is
     * a near-invisible control. The map's own chrome is light while the app
     * around it is dark; that inversion is the point, and it is the reason
     * `text` (warm off-white) cannot be used on these two surfaces.
     */
    white: '#ffffff',
    /** Text and icon colour on `white` floating chrome. */
    onWhite: '#181d26',
  },
  radius: { button: 12, card: 16, chip: 32 },
  spacing: { xs: 8, sm: 16, md: 24, lg: 32, xl: 40, xxl: 64 },
  shadow: {
    /**
     * A dark drop shadow, because the surfaces are dark. The previous light
     * theme's shadow was a blue-tinted `#0f306a` on white, which is invisible
     * on `#0c0c0b` — a shadow's job here is to separate a raised surface from
     * the page, and on near-black that needs a dark edge plus elevation.
     */
    card: {
      shadowColor: '#000000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.5,
      shadowRadius: 12,
      elevation: 3, // Android
    },
  },
  fonts: {
    /**
     * **Inter only, and RobotoSlab is gone.**
     *
     * The heading slot deliberately points at Inter's semibold rather than at a
     * slab face. The design calls for a single family, and the way this app
     * uses headings — 16px uppercase captions, and 16px titles — is a weight
     * distinction, not a typeface distinction. A second family would buy no
     * hierarchy the weights do not already give, and would cost a second font
     * download for no gain.
     */
    heading: 'Inter_600SemiBold',
    body: 'Inter_400Regular',
    bodyMedium: 'Inter_500Medium',
    bodySemibold: 'Inter_600SemiBold',
  },
  typography: {
    heading: 22,
    body: 16,
    emphasis: 16,
    caption: 13,
  },
};
