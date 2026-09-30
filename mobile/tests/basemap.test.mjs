/**
 * The Web basemap: that the committed image is real, and that the metadata
 * the Web build ships still matches it.
 *
 * ## Why this is checked against the committed index rather than trusted
 *
 * `mobile/basemap.ts` hardcodes the basemap's bounds so the map has a usable
 * extent before any request resolves. That is a copy of
 * `data/basemap/basemap.json`, and two copies of one number is exactly the
 * setup that drifts. So the copy is checked against the original here — a
 * regenerated basemap whose extent moved fails a test instead of silently
 * misplacing the water.
 *
 * ## Why Pillow is the decoder
 *
 * The lesson this project already paid for (MEMORY.md §42): a decoder written
 * by the same reasoning as its encoder cannot disagree with it, and two files
 * written by the same mistake agreed with each other while both being wrong.
 * Pillow is a completely independent implementation of the PNG format, so it
 * can say this file is not a PNG at all — which is precisely what happened to
 * all eight flood overlays for two days while the repo's own suite passed.
 */

import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

import { BASEMAP_META, BASEMAP_DISCLOSURE } from '../basemap.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..', '..');
const INDEX = join(REPO, 'data/basemap/basemap.json');
const IMAGE = join(REPO, 'data/basemap/basemap.png');
const BUNDLED = join(REPO, 'mobile/assets/basemap.png');
const OVERLAYS_INDEX = join(REPO, 'data/overlays/overlays.json');
const THEME = join(REPO, 'mobile/theme.ts');

describe('The committed basemap exists and is a real PNG', () => {
  it('data/basemap/basemap.png is present', () => {
    assert.ok(existsSync(IMAGE), 'run: venv/bin/python -m backend.tools.render_basemap');
  });

  it('has the PNG magic bytes', () => {
    // A cheap structural check that needs no dependency. The full independent
    // decode (Pillow) is the Python-side test, `tests/test_basemap.py`.
    const bytes = readFileSync(IMAGE);
    assert.deepEqual(
      [...bytes.subarray(0, 8)],
      [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
    );
  });

  it('declares colour type 6 (RGBA) in its IHDR', () => {
    // The eight flood overlays shipped with an invalid colour type 9 for two
    // days and the repo's own suite passed, because the only decoder was the
    // encoder's own inverse. PNG defines 0, 2, 3, 4 and 6. There is no 9.
    const bytes = readFileSync(IMAGE);
    const width = bytes.readUInt32BE(16);
    const height = bytes.readUInt32BE(20);
    const bitDepth = bytes[24];
    const colourType = bytes[25];
    assert.equal(bitDepth, 8);
    assert.equal(colourType, 6, `colour type was ${colourType}, must be 6`);
    assert.equal(width, BASEMAP_META.width);
    assert.equal(height, BASEMAP_META.height);
  });
});

describe('The basemap metadata matches the committed index', () => {
  it('data/basemap/basemap.json is present', () => {
    assert.ok(existsSync(INDEX));
  });

  it('the dimensions match', () => {
    const index = JSON.parse(readFileSync(INDEX, 'utf8'));
    assert.equal(BASEMAP_META.width, index.width_px);
    assert.equal(BASEMAP_META.height, index.height_px);
  });

  it('the byte count matches', () => {
    const index = JSON.parse(readFileSync(INDEX, 'utf8'));
    assert.equal(BASEMAP_META.pngBytes, index.png_bytes);
  });

  it('the bounds match to within 1e-4 degrees (~11 m)', () => {
    const index = JSON.parse(readFileSync(INDEX, 'utf8'));
    for (const edge of ['west', 'south', 'east', 'north']) {
      assert.ok(
        Math.abs(BASEMAP_META.bounds[edge] - index.bounds[edge]) < 1e-4,
        `${edge}: ${BASEMAP_META.bounds[edge]} vs ${index.bounds[edge]}`,
      );
    }
  });

  it('the land fraction matches', () => {
    const index = JSON.parse(readFileSync(INDEX, 'utf8'));
    assert.ok(Math.abs(BASEMAP_META.landFraction - index.land_fraction) < 1e-6);
  });
});

describe('The basemap extent agrees with the flood overlays', () => {
  it('covers the same bbox as the flood rasters, to within 1e-4 degrees', () => {
    // They are derived from the same DEM, so they must register. A divergence
    // here would put the water somewhere the coastline is not.
    const overlays = JSON.parse(readFileSync(OVERLAYS_INDEX, 'utf8'));
    const cat6 = overlays.overlays.find((o) => o.id === 'cat6');
    assert.ok(cat6 !== undefined, 'cat6 overlay missing from the index');
    for (const edge of ['west', 'south', 'east', 'north']) {
      assert.ok(
        Math.abs(BASEMAP_META.bounds[edge] - cat6.bounds[edge]) < 1e-4,
        `${edge}: basemap ${BASEMAP_META.bounds[edge]} vs overlay ${cat6.bounds[edge]}`,
      );
    }
  });
});

describe('The basemap colours are the theme’s map tints', () => {
  it('the renderer read land and water from mobile/theme.ts', () => {
    const index = JSON.parse(readFileSync(INDEX, 'utf8'));
    const theme = readFileSync(THEME, 'utf8');

    const land = /land:\s*'(#[0-9a-fA-F]{6})'/.exec(theme);
    const water = /water:\s*'(#[0-9a-fA-F]{6})'/.exec(theme);
    assert.ok(land !== null, 'theme.ts has no land token');
    assert.ok(water !== null, 'theme.ts has no water token');

    // Case-insensitive, because theme.ts uses lower case and the renderer
    // writes upper — a formatting difference, not a value difference.
    assert.equal(index.land_colour.toLowerCase(), land[1].toLowerCase());
    assert.equal(index.water_colour.toLowerCase(), water[1].toLowerCase());
  });
});

describe('The basemap is bundled for the Web build', () => {
  it('mobile/assets/basemap.png exists', () => {
    // The Web build ships the basemap as a build asset so it is same-origin
    // and cannot 404 from a runtime path.
    assert.ok(
      existsSync(BUNDLED),
      'copy data/basemap/basemap.png to mobile/assets/basemap.png',
    );
  });

  it('the bundled copy is byte-identical to the committed one', () => {
    const a = readFileSync(IMAGE);
    const b = readFileSync(BUNDLED);
    assert.equal(a.length, b.length);
    assert.ok(a.equals(b), 'the bundled basemap has diverged from data/basemap/');
  });
});

describe('The basemap disclosure says the three things that matter', () => {
  it('says the coastline is derived, not surveyed', () => {
    assert.match(BASEMAP_DISCLOSURE, /0 m contour/);
    assert.match(BASEMAP_DISCLOSURE, /not a surveyed chart/);
  });

  it('says the image is coarse', () => {
    assert.match(BASEMAP_DISCLOSURE, /150 m per pixel/);
  });

  it('says no figure is measured off it', () => {
    assert.match(BASEMAP_DISCLOSURE, /No number in this app is measured off it/);
  });
});
