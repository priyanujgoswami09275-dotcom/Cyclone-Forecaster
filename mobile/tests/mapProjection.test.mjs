/**
 * The Web map's projection, and the arithmetic that places real geography on
 * screen.
 *
 * Every case here is a way the map can be *silently* wrong. A transposed axis
 * puts Sagar Island in the Bay of Bengal; a y-flip puts the delta in the sea; a
 * zero-span division produces `NaN` coordinates that render as an empty map
 * with no error at all. None of these throw, so all of them have to be pinned
 * by a test.
 *
 * The assertions are made against **real coordinates from the project's own
 * committed data** — the DEM bbox, the Sagar Island locality, and the first
 * IBTrACS fix — rather than invented numbers, so a test passing means the real
 * data places correctly.
 */

import assert from 'node:assert/strict';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

import {
  boundsOf,
  groundAspect,
  viewBoxFor,
  LATITUDE_COSINE,
  imageRect,
  latLngPoints,
  makeProjection,
  openingBounds,
  pathPoints,
  SAGAR_CENTRE,
  OPENING_BOUNDS,
} from '../mapProjection.ts';
import { BASEMAP_META } from '../basemap.ts';

const HERE = dirname(fileURLToPath(import.meta.url));

describe('openingBounds', () => {
  // **This frame used to be centred on Sagar at 1.6 x 1.15 degrees, and that
  // hid every exposed asset.** Measured on the live `/exposure?category=6`
  // payload: hospitals lat 22.261-22.588, substations 22.265-22.592. Sagar is at
  // 21.65 N, so the old frame reached only 22.22 N and all 34 markers fell
  // outside it — while the DOM held exactly 12 and 22 circles in the right
  // colours. The tiles said "Hospitals 12" and the map showed none.

  it('is the DEM extent, so the basemap fills it exactly', () => {
    const b = openingBounds();
    assert.ok(Math.abs(b.west - 87.8) < 1e-6);
    assert.ok(Math.abs(b.east - 89.2) < 1e-6);
    assert.ok(Math.abs(b.south - 21.3) < 1e-6);
    assert.ok(Math.abs(b.north - 22.6016) < 1e-6);
  });

  it('contains the case-study landfall point', () => {
    const b = openingBounds();
    assert.ok(SAGAR_CENTRE.latitude > b.south && SAGAR_CENTRE.latitude < b.north);
    assert.ok(SAGAR_CENTRE.longitude > b.west && SAGAR_CENTRE.longitude < b.east);
  });

  it('reaches far enough north to contain the exposed assets', () => {
    // The exact northern extent of the 34 exposed assets at category 6. This is
    // the regression guard: a frame that stops below this hides everything.
    const b = openingBounds();
    const EXPOSED_ASSET_MIN_LAT = 22.261;
    assert.ok(
      b.north > EXPOSED_ASSET_MIN_LAT,
      `frame reaches ${b.north} N, assets start at ${EXPOSED_ASSET_MIN_LAT} N`,
    );
  });

  it('reaches far enough east to contain the exposed assets', () => {
    // 'Friendship Hospital Shyamnagar' at lon 89.0799 was the easternmost.
    const b = openingBounds();
    assert.ok(b.east > 89.0799, `frame reaches ${b.east} E, need 89.0799`);
  });

  it('is ordered north > south and east > west', () => {
    const b = openingBounds();
    assert.ok(b.north > b.south);
    assert.ok(b.east > b.west);
  });

  it('matches BASEMAP_META.bounds, which is the same extent by construction', () => {
    // Two copies of one number is how they drift; this is the check.
    const b = openingBounds();
    assert.ok(Math.abs(b.west - BASEMAP_META.bounds.west) < 1e-6);
    assert.ok(Math.abs(b.east - BASEMAP_META.bounds.east) < 1e-6);
    assert.ok(Math.abs(b.south - BASEMAP_META.bounds.south) < 1e-6);
    assert.ok(Math.abs(b.north - BASEMAP_META.bounds.north) < 1e-6);
  });
});

describe('makeProjection', () => {
  const bounds = { west: 87.8, south: 21.3, east: 89.2, north: 22.6 };
  const p = makeProjection(bounds, 1000, 760);

  it('puts the north-west corner at the top-left', () => {
    const nw = p.project({ latitude: bounds.north, longitude: bounds.west });
    assert.ok(Math.abs(nw.x) < 0.001, `x was ${nw.x}`);
    assert.ok(Math.abs(nw.y) < 0.001, `y was ${nw.y}`);
  });

  it('puts the south-east corner at the bottom-right', () => {
    const se = p.project({ latitude: bounds.south, longitude: bounds.east });
    assert.ok(Math.abs(se.x - 1000) < 0.001, `x was ${se.x}`);
    assert.ok(Math.abs(se.y - 760) < 0.001, `y was ${se.y}`);
  });

  it('maps mid-latitude to mid-height', () => {
    const mid = p.project({ latitude: 21.95, longitude: 88.5 });
    assert.ok(Math.abs(mid.x - 500) < 0.001);
    assert.ok(Math.abs(mid.y - 380) < 0.001);
  });

  it('increases y as latitude falls — north is up, not down', () => {
    // The one flip that matters. Getting it backwards draws the Bay of Bengal
    // where the delta is.
    const north = p.project({ latitude: 22.5, longitude: 88.5 });
    const south = p.project({ latitude: 21.4, longitude: 88.5 });
    assert.ok(north.y < south.y, 'northern latitudes must have a smaller y');
  });

  it('increases x as longitude rises', () => {
    const west = p.project({ latitude: 21.95, longitude: 87.9 });
    const east = p.project({ latitude: 21.95, longitude: 89.1 });
    assert.ok(west.x < east.x);
  });

  it('projects the real Sagar Island coordinate into the frame', () => {
    const p2 = makeProjection(openingBounds(), 1000, 760);
    const { x, y } = p2.project(SAGAR_CENTRE);
    assert.ok(x > 0 && x < 1000, `Sagar x ${x} out of frame`);
    assert.ok(y > 0 && y < 760, `Sagar y ${y} out of frame`);
  });

  it('survives a zero-span box without producing NaN', () => {
    const degenerate = makeProjection(
      { west: 88, south: 21, east: 88, north: 21 },
      100,
      100,
    );
    const { x, y } = degenerate.project({ latitude: 21, longitude: 88 });
    assert.ok(Number.isFinite(x) && Number.isFinite(y));
  });
});

describe('boundsOf', () => {
  it('fits the real IBTrACS span, 18.8N to 24.2N', () => {
    // The committed track runs 18.75-24.2 N. A fitted box must contain both.
    const track = [
      { latitude: 18.75, longitude: 89.4 },
      { latitude: 24.2, longitude: 88.4 },
      { latitude: 21.0, longitude: 90.4 },
    ];
    const bounds = boundsOf(track);
    assert.ok(bounds !== null);
    assert.ok(bounds.south < 18.75);
    assert.ok(bounds.north > 24.2);
    assert.ok(bounds.west < 88.4);
    assert.ok(bounds.east > 90.4);
  });

  it('returns null for an empty list rather than centring on (0,0)', () => {
    assert.equal(boundsOf([]), null);
  });

  it('drops non-finite points instead of poisoning the box', () => {
    // `/track` coerces with float() and does not range-check, and json.loads
    // accepts a bare NaN, so this is reachable from a corrupt data file.
    // Only the one finite point may contribute — a NaN reaching the padding
    // arithmetic would make the whole box NaN, which renders as a blank map.
    const bounds = boundsOf([
      { latitude: NaN, longitude: 88 },
      { latitude: 22, longitude: Number.POSITIVE_INFINITY },
      { latitude: 21.6, longitude: 88.05 },
    ]);
    assert.ok(bounds !== null);
    // One point means a degenerate span, so the padding's 0.02 deg floor is
    // what sets the width — assert against that rather than against 0.
    assert.ok(Math.abs(bounds.west - (88.05 - 0.02)) < 1e-9, `west ${bounds.west}`);
    assert.ok(Math.abs(bounds.east - (88.05 + 0.02)) < 1e-9, `east ${bounds.east}`);
    assert.ok(Math.abs(bounds.south - (21.6 - 0.02)) < 1e-9, `south ${bounds.south}`);
    assert.ok(Math.abs(bounds.north - (21.6 + 0.02)) < 1e-9, `north ${bounds.north}`);
  });

  it('returns null when every point is non-finite', () => {
    assert.equal(
      boundsOf([
        { latitude: NaN, longitude: NaN },
        { latitude: Infinity, longitude: -Infinity },
      ]),
      null,
    );
  });

  it('pads a single point so the box is usable', () => {
    const bounds = boundsOf([{ latitude: 21.6476, longitude: 88.0568 }]);
    assert.ok(bounds !== null);
    assert.ok(bounds.north > bounds.south);
    assert.ok(bounds.east > bounds.west);
  });
});

describe('pathPoints', () => {
  const p = makeProjection({ west: 87.8, south: 21.3, east: 89.2, north: 22.6 }, 1000, 760);

  it('renders a two-vertex line', () => {
    const points = pathPoints(
      [
        [88.0, 21.5],
        [88.1, 21.6],
      ],
      p,
    );
    assert.ok(points !== null);
    assert.equal(points.split(' ').length, 2);
  });

  it('returns null for a single vertex rather than a stray dot', () => {
    assert.equal(pathPoints([[88.0, 21.5]], p), null);
  });

  it('returns null for an empty path', () => {
    assert.equal(pathPoints([], p), null);
  });

  it('drops non-finite vertices mid-path and keeps the rest', () => {
    const points = pathPoints(
      [
        [88.0, 21.5],
        [NaN, 21.55],
        [88.1, 21.6],
      ],
      p,
    );
    assert.ok(points !== null);
    assert.equal(points.split(' ').length, 2);
    assert.ok(!points.includes('NaN'));
  });

  it('never emits a NaN coordinate', () => {
    const points = pathPoints(
      [
        [88.0, 21.5],
        ['x', 21.55],
        [88.1, 21.6],
      ],
      p,
    );
    assert.ok(points !== null);
    assert.ok(!points.includes('NaN'));
  });

  it('drops short pairs', () => {
    const points = pathPoints(
      [
        [88.0],
        [88.0, 21.5],
        [88.1, 21.6],
      ],
      p,
    );
    assert.ok(points !== null);
    assert.equal(points.split(' ').length, 2);
  });
});

describe('latLngPoints', () => {
  const p = makeProjection({ west: 87.8, south: 21.3, east: 89.2, north: 22.6 }, 1000, 760);

  it('renders the real 19-fix track', () => {
    const track = [
      { latitude: 18.8, longitude: 89.4 },
      { latitude: 19.4, longitude: 89.2 },
      { latitude: 20.1, longitude: 89.0 },
      { latitude: 20.8, longitude: 88.9 },
      { latitude: 21.4, longitude: 88.7 },
      { latitude: 21.9, longitude: 88.5 },
      { latitude: 22.3, longitude: 88.4 },
      { latitude: 22.9, longitude: 88.6 },
      { latitude: 23.5, longitude: 88.9 },
      { latitude: 24.2, longitude: 89.3 },
    ];
    const points = latLngPoints(track, p);
    assert.ok(points !== null);
    assert.equal(points.split(' ').length, 10);
  });

  it('returns null for one point', () => {
    assert.equal(latLngPoints([{ latitude: 20, longitude: 89 }], p), null);
  });

  it('filters non-finite fixes', () => {
    const points = latLngPoints(
      [
        { latitude: 18.8, longitude: 89.4 },
        { latitude: NaN, longitude: 89.2 },
        { latitude: 20.1, longitude: 89.0 },
      ],
      p,
    );
    assert.ok(points !== null);
    assert.ok(!points.includes('NaN'));
    assert.equal(points.split(' ').length, 2);
  });
});

describe('groundAspect and viewBoxFor (the invisible-marker fix)', () => {
  const opening = openingBounds();

  it('uses the cosine of latitude, not the raw degree ratio', () => {
    // The naive spanLon/spanLat is 1.391. The ground ratio is larger, because
    // a degree of longitude is ~92% of a degree of latitude here. Using the
    // naive one was the bug.
    const naive = (opening.east - opening.west) / (opening.north - opening.south);
    const ground = groundAspect(opening);
    assert.ok(
      ground > naive,
      `ground aspect ${ground} should exceed the naive ratio ${naive}`,
    );
    assert.ok(Math.abs(ground - naive / LATITUDE_COSINE) < 1e-9);
  });

  it('matches the hand-computed value for the opening region', () => {
    // The DEM extent: lon span 1.4, lat span 1.3016.
    // 1.4 / (1.3016 * 0.924) = 1.16407
    assert.ok(Math.abs(groundAspect(opening) - 1.16407) < 0.001);
  });

  it('produces a viewBox whose aspect equals the ground aspect', () => {
    const { width, height } = viewBoxFor(opening, 1000);
    assert.equal(width, 1000);
    assert.ok(Math.abs(width / height - groundAspect(opening)) < 0.01);
  });

  it('is NOT the old fixed 760 height — that is the regression guard', () => {
    // 1000 / 1.16407 = 859. The old code used 760 unconditionally, which is
    // ~11% too tall for this box and was part of why the drawing overshot.
    const { height } = viewBoxFor(opening, 1000);
    assert.notEqual(height, 760);
    assert.equal(height, 859);
  });

  it('a full-IO-track fit also gets a sensible frame', () => {
    const track = boundsOf([
      { latitude: 18.75, longitude: 89.4 },
      { latitude: 24.2, longitude: 88.4 },
      { latitude: 21.0, longitude: 90.4 },
    ]);
    assert.ok(track !== null);
    const { width, height } = viewBoxFor(track, 1000);
    assert.ok(height > 0 && Number.isFinite(height));
    assert.ok(Math.abs(width / height - groundAspect(track)) < 0.01);
  });

  it('degrades safely on a degenerate box', () => {
    const flat = { west: 88, south: 21, east: 88, north: 21 };
    assert.ok(Number.isFinite(viewBoxFor(flat, 1000).height));
    assert.equal(groundAspect(flat), 1);
  });
});

describe('imageRect', () => {
  const p = makeProjection({ west: 87.8, south: 21.3, east: 89.2, north: 22.6 }, 1000, 760);

  it('places the DEM bbox over the whole frame at full extent', () => {
    const rect = imageRect({ west: 87.8, south: 21.3, east: 89.2, north: 22.6 }, p);
    assert.ok(rect !== null);
    assert.ok(Math.abs(rect.x) < 0.001);
    assert.ok(Math.abs(rect.y) < 0.001);
    assert.ok(Math.abs(rect.width - 1000) < 0.001);
    assert.ok(Math.abs(rect.height - 760) < 0.001);
  });

  it('places a subset bbox strictly inside the frame', () => {
    const rect = imageRect({ west: 88.0, south: 21.5, east: 88.5, north: 22.0 }, p);
    assert.ok(rect !== null);
    assert.ok(rect.x > 0 && rect.y > 0);
    assert.ok(rect.x + rect.width < 1000.001);
    assert.ok(rect.y + rect.height < 760.001);
  });

  it('returns null for a degenerate box instead of a NaN rect', () => {
    assert.equal(imageRect({ west: 88, south: 21, east: 88, north: 22 }, p), null);
    assert.equal(imageRect({ west: 88, south: 21, east: 89, north: 21 }, p), null);
  });
});
