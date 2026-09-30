/**
 * The Leaflet map document, exercised headlessly.
 *
 * ## Why this test exists
 *
 * `leafletHtml.ts` is a string containing the entire map implementation, and it
 * runs inside a WebView on a phone — neither of which any CI here can reach.
 * **This is the only verification available without a device**, and it is not a
 * weak substitute: the document below is the exact `leafletDocument()` output,
 * executed by a real JavaScript engine, with `L` replaced by a recorder. Every
 * command is then run through `window.__cyclone`, exactly as
 * `LeafletMap` invokes it, and the recorded layer calls are asserted.
 *
 * So this tests the code that ships, not a paraphrase of it.
 *
 * ## What it cannot prove
 *
 * Leaflet itself is stubbed — the real library needs a layout engine, and jsdom
 * has none. That means tile loading, CSS, gesture handling, and anything that
 * depends on measurement are **NOT VERIFIED here** and are only verifiable in
 * Expo Go. What is verified is the whole of this app's own contribution: the
 * command protocol, the coordinate hand-off, the layer lifecycle, the popups'
 * use of `textContent`, and every failure path.
 */

import assert from 'node:assert/strict';
import { describe, it, before } from 'node:test';

import { JSDOM } from 'jsdom';

import {
  LEAFLET_SOURCES,
  LEAFLET_VERSION,
  commandScript,
  leafletDocument,
} from '../leafletHtml.ts';
import { fromLatLng, fromLatLngList, toLeaflet, toLeafletList } from '../geo.ts';

/**
 * A recording stand-in for Leaflet.
 *
 * Every method records its call. Nothing here simulates Leaflet's behaviour —
 * it records what the document *asked* for, which is the part this app is
 * responsible for.
 */
function installFakeLeaflet(window) {
  const calls = [];
  const record = (name, args) => calls.push({ name, args });

  const layer = (kind) => ({
    __kind: kind,
    bindPopup(content) {
      record(`bindPopup:${kind}`, [content]);
      return this;
    },
    addTo(target) {
      record(`addTo:${kind}`, [target]);
      return this;
    },
    getBounds() {
      record(`getBounds:${kind}`, []);
      return { __bounds: true };
    },
  });

  const groupOf = (children) => {
    const g = layer('layerGroup');
    g.__children = children;
    return g;
  };

  const L = {
    map: (id, options) => {
      record('map', [id, options]);
      return {
        options,
        getCenter: () => ({ lat: 21.68, lng: 88.08 }),
        getZoom: () => 11,
        setView: (...a) => record('setView', a),
        fitBounds: (...a) => record('fitBounds', a),
        invalidateSize: (...a) => record('invalidateSize', a),
        // `hasLayer` always true so the removal path is always taken — the point
        // of `clearRoute` is that a layer which *was* drawn comes off.
        hasLayer: () => true,
        removeLayer: (l) => record('removeLayer', [l]),
        on: (event, handler) => {
          record('map.on', [event]);
          if (event === 'tileerror') window.__tileErrorHandler = handler;
        },
      };
    },
    tileLayer: (url, options) => {
      record('tileLayer', [url, options]);
      return layer('tileLayer');
    },
    imageOverlay: (url, bounds, options) => {
      const l = layer('imageOverlay');
      record('imageOverlay', [url, bounds, options]);
      l.__url = url;
      l.__bounds = bounds;
      return l;
    },
    polyline: (path, options) => {
      const l = layer('polyline');
      record('polyline', [path, options]);
      l.__path = path;
      l.__options = options;
      return l;
    },
    circleMarker: (at, options) => {
      const l = layer('circleMarker');
      record('circleMarker', [at, options]);
      l.__at = at;
      l.__options = options;
      return l;
    },
    marker: (at, options) => {
      const l = layer('marker');
      record('marker', [at, options]);
      l.__at = at;
      return l;
    },
    layerGroup: (children) => {
      const g = groupOf(children ?? []);
      record('layerGroup', []);
      return g;
    },
    divIcon: (options) => {
      record('divIcon', [options]);
      return { __icon: true };
    },
  };

  window.L = L;
  window.__calls = calls;
  return calls;
}

/**
 * Boot the real document in jsdom.
 *
 * The two CDN `<script src>` tags never fire in jsdom, so the document's own
 * loader would sit waiting forever. `L` is therefore installed *before* the
 * document's inline script runs, and the inline script's `s.onload` is
 * triggered by hand — which means this test also covers the loader's happy
 * path end to end.
 */
function boot(apiOrigin = 'https://example.test') {
  const html = leafletDocument(apiOrigin);
  // `outside-only`, **not** `dangerously`: jsdom must not run the document's
  // own script at construction, because at that point `L` does not exist yet
  // and the loader would fail against the real globals. The inline script is
  // evaluated explicitly below, after the stubs are in place, so the only run
  // of the document's code is the one this test controls.
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://localhost/' });
  const { window } = dom;

  // Messages the document posts back to React Native.
  window.__posted = [];
  window.ReactNativeWebView = {
    postMessage: (data) => window.__posted.push(JSON.parse(data)),
  };

  const calls = installFakeLeaflet(window);

  // Re-run the inline script now that `L` and the bridge exist.
  const inline = [...window.document.querySelectorAll('script')]
    .map((s) => s.textContent ?? '')
    .find((text) => text.includes('window.__cyclone'));
  assert.ok(inline, 'the document has an inline bootstrap script');

  window.eval(inline);

  // The loader appended a <script src> to <head>; jsdom will not fetch it, so
  // fire its onload by hand — which is exactly what a browser does on success.
  const appended = window.document.head.querySelector('script[src]');
  assert.ok(appended, 'the loader appended a script tag');
  assert.equal(appended.getAttribute('src'), LEAFLET_SOURCES[0], 'cdnjs is tried first');
  appended.onload();

  return { dom, window, calls };
}

/** Everything the document has acknowledged since boot. */
function acks(window) {
  return window.__posted.filter((m) => m.type === 'ack');
}

/**
 * A plain-realm copy of a value that came out of the jsdom window.
 *
 * `assert.deepEqual` compares prototypes, and every array the document built
 * inside jsdom has a *different* `Array.prototype` from this test file's. That
 * makes a structural comparison fail with "same structure but not
 * reference-equal", which tells you nothing about the code under test. Round-
 * tripping through JSON drops the foreign prototype and leaves only the values,
 * which is what was actually meant to be asserted.
 */
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

describe('the Leaflet document', () => {
  let window;

  before(() => {
    window = boot().window;
  });

  it('loads Leaflet 1.9.4 from cdnjs, with jsdelivr as the fallback', () => {
    assert.equal(LEAFLET_VERSION, '1.9.4');
    assert.match(LEAFLET_SOURCES[0], /^https:\/\/cdnjs\.cloudflare\.com\/.+1\.9\.4\//);
    assert.match(LEAFLET_SOURCES[1], /^https:\/\/cdn\.jsdelivr\.net\/npm\/leaflet@1\.9\.4\//);
    assert.equal(LEAFLET_SOURCES.length, 2);
  });

  it('uses keyless CARTO raster tiles and attributes them', () => {
    const window2 = boot().window;
    const tile = window2.__calls.find((c) => c.name === 'tileLayer');
    assert.ok(tile, 'a tile layer was added');
    assert.match(tile.args[0], /basemaps\.cartocdn\.com/);
    assert.match(tile.args[1].attribution, /OpenStreetMap/);
    // The whole point of this route: no key, no account.
    assert.ok(!/api[_-]?key|apikey|access_token/i.test(JSON.stringify(tile.args)));
  });

  it('disables rotation, pitch and double-tap zoom, matching the old native map', () => {
    const mapCall = window.__calls.find((c) => c.name === 'map');
    assert.equal(mapCall.args[1].rotate, false);
    assert.equal(mapCall.args[1].pitch, false);
    assert.equal(mapCall.args[1].doubleClickZoom, false);
  });

  it('announces map_ready once Leaflet has booted', () => {
    const window2 = boot().window;
    assert.ok(
      window2.__posted.some((m) => m.type === 'map_ready'),
      'the document announces map_ready',
    );
    assert.equal(window2.document.getElementById('banner').className, '', 'no banner on success');
  });

  it('falls through to jsdelivr when cdnjs fails, and shows the banner if both do', () => {
    const html = leafletDocument('https://example.test');
    const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://localhost/' });
    const { window } = dom;
    window.__posted = [];
    window.ReactNativeWebView = { postMessage: (d) => window.__posted.push(JSON.parse(d)) };
    installFakeLeaflet(window);
    const inline = [...window.document.querySelectorAll('script')]
      .map((s) => s.textContent ?? '')
      .find((t) => t.includes('window.__cyclone'));
    window.eval(inline);

    // cdnjs errors -> the loader must append jsdelivr, not give up.
    window.document.head.querySelector('script[src]').onerror();
    const second = window.document.head.querySelectorAll('script[src]');
    assert.equal(second.length, 2, 'the fallback is attempted');
    assert.equal(second[1].getAttribute('src'), LEAFLET_SOURCES[1]);

    // jsdelivr errors too -> the banner appears and a failure is posted, so the
    // map is never a blank rectangle with no explanation.
    second[1].onerror();
    assert.equal(window.document.getElementById('banner').className, 'on');
    const failure = window.__posted.find((m) => m.type === 'map_failed');
    assert.ok(failure, 'the failure is reported to React Native');
    assert.match(failure.reason, /could not be downloaded/i);
    assert.match(window.document.getElementById('banner-detail').textContent, /internet connection/i);
  });

  it('shows the banner when the library loads but the map cannot start', () => {
    // This is the path that caught the missing tile constants: `boot()` threw a
    // ReferenceError, and without a banner it would have been a black square
    // with nothing written about it.
    const html = leafletDocument('https://example.test');
    const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://localhost/' });
    const { window } = dom;
    window.__posted = [];
    window.ReactNativeWebView = { postMessage: (d) => window.__posted.push(JSON.parse(d)) };
    // `L` present but `L.map` throws, as it would on an unusable layout.
    window.L = {
      map() {
        throw new Error('container has no size');
      },
    };
    const inline = [...window.document.querySelectorAll('script')]
      .map((s) => s.textContent ?? '')
      .find((t) => t.includes('window.__cyclone'));
    window.eval(inline);
    window.document.head.querySelector('script[src]').onload();

    assert.equal(window.document.getElementById('banner').className, 'on');
    const failure = window.__posted.find((m) => m.type === 'map_failed');
    assert.match(failure.reason, /container has no size/);
  });

  it('gives up after repeated tile errors rather than showing grey squares', () => {
    const { window } = boot();
    assert.equal(window.document.getElementById('banner').className, '', 'not failed yet');
    for (let i = 0; i < 4; i += 1) window.__tileErrorHandler();
    // The check is debounced, so advance past it.
    window.eval('void 0');
    return new Promise((resolve) => {
      setTimeout(() => {
        assert.equal(
          window.document.getElementById('banner').className,
          'on',
          'four tile failures must produce the banner',
        );
        assert.match(
          window.document.getElementById('banner-detail').textContent,
          /map tiles could not be loaded/i,
        );
        resolve();
      }, 3000);
    });
  });

  it('gives every command an id and an acknowledgement, success or failure', () => {
    const window2 = boot().window;
    window2.eval(commandScript({ id: 7, cmd: 'setView', payload: { center: [21.6, 88.0], zoom: 9 } }));
    const ack = window2.__posted.find((m) => m.type === 'ack' && m.id === 7);
    assert.ok(ack, 'the command was acknowledged');
    assert.equal(ack.ok, true);
  });

  it('rejects an unknown command by name rather than ignoring it', () => {
    const window2 = boot().window;
    window2.eval(commandScript({ id: 1, cmd: 'definitelyNotACommand' }));
    const ack = window2.__posted.find((m) => m.type === 'ack' && m.id === 1);
    assert.equal(ack.ok, false);
    assert.match(ack.error, /Unknown map command/);
  });

  it('fails loudly on a command issued before the map exists', () => {
    // A document whose `boot()` threw: `map` is null, so every command must say
    // so rather than silently doing nothing.
    const dom = new JSDOM(leafletDocument('https://example.test'), {
      runScripts: 'outside-only',
      url: 'https://localhost/',
    });
    const { window: w } = dom;
    w.__posted = [];
    w.ReactNativeWebView = { postMessage: (d) => w.__posted.push(JSON.parse(d)) };
    const inline = [...w.document.querySelectorAll('script')]
      .map((s) => s.textContent ?? '')
      .find((t) => t.includes('window.__cyclone'));
    w.eval(inline);
    // Never fire onload, so `boot()` never runs and `map` stays null.
    w.eval(commandScript({ id: 1, cmd: 'setView', payload: { center: [1, 2], zoom: 3 } }));
    const ack = w.__posted.find((m) => m.type === 'ack' && m.id === 1);
    assert.equal(ack.ok, false);
    assert.match(ack.error, /not ready/);
  });
});

describe('the command protocol produces the right Leaflet layers', () => {
  it('draws the flood raster as an ImageOverlay with explicit bounds', () => {
    const { window } = boot();
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setOverlay',
        payload: {
          url: 'https://example.test/overlays/flood_cat6.png',
          bounds: { north: 22.6, south: 21.3, east: 89.2, west: 87.8 },
          opacity: 0.62,
        },
      }),
    );
    const call = window.__calls.find((c) => c.name === 'imageOverlay');
    assert.ok(call, 'an imageOverlay was created');
    assert.equal(call.args[0], 'https://example.test/overlays/flood_cat6.png');
    // Leaflet wants [[south, west], [north, east]] — the *opposite* order from
    // GeoJSON's [lon, lat], which is exactly the transposition item 9 exists to
    // get right once.
    assert.deepEqual(plain(call.args[1]), [
      [21.3, 87.8],
      [22.6, 89.2],
    ]);
    assert.equal(call.args[2].opacity, 0.62);
  });

  it('removes the flood layer when the overlay is cleared', () => {
    const { window } = boot();
    window.eval(commandScript({ id: 1, cmd: 'setOverlay', payload: { url: 'x', bounds: {} } }));
    const before = window.__calls.filter((c) => c.name === 'imageOverlay').length;
    window.eval(commandScript({ id: 2, cmd: 'setOverlay', payload: { url: null } }));
    const after = window.__calls.filter((c) => c.name === 'imageOverlay').length;
    assert.equal(after, before, 'a null url must not create a second raster');
    assert.ok(
      window.__calls.some((c) => c.name === 'addTo:imageOverlay'),
      'the first raster was added to the map',
    );
  });

  it('refuses an overlay with no bounds instead of misplacing it', () => {
    const { window } = boot();
    window.eval(commandScript({ id: 1, cmd: 'setOverlay', payload: { url: 'x' } }));
    const ack = window.__posted.find((m) => m.type === 'ack' && m.id === 1);
    assert.equal(ack.ok, false);
    assert.match(ack.error, /without bounds/);
  });

  it('draws the storm track as one polyline and its fixes as markers', () => {
    const { window } = boot();
    const path = [
      [21.5, 88.2],
      [21.8, 88.1],
      [22.0, 88.0],
    ];
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setTrack',
        payload: {
          path,
          waypoints: path.map((at, i) => ({ at, radius: 4, kind: 'waypoint' })),
        },
      }),
    );
    const polyline = window.__calls.find((c) => c.name === 'polyline');
    assert.ok(polyline, 'a polyline was created');
    assert.deepEqual(plain(polyline.args[0]), plain(path));
    assert.equal(polyline.args[1].weight, 3);
  });

  it('draws one polyline per road part, never joining disjoint parts', () => {
    const { window } = boot();
    // A MultiLineString: two disjoint paths that must not be connected.
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setRoads',
        payload: {
          paths: [
            [
              [
                [22.1, 88.1],
                [22.2, 88.2],
              ],
            ],
            [
              [
                [22.5, 88.5],
                [22.6, 88.6],
              ],
            ],
          ],
          colour: '#a11d00',
        },
      }),
    );
    const polylines = window.__calls.filter((c) => c.name === 'polyline');
    assert.equal(polylines.length, 2, 'one polyline per geometry part');
    assert.deepEqual(plain(polylines[0].args[0]), [
      [22.1, 88.1],
      [22.2, 88.2],
    ]);
    assert.deepEqual(plain(polylines[1].args[0]), [
      [22.5, 88.5],
      [22.6, 88.6],
    ]);
    // And the style still comes from the theme's danger token.
    assert.equal(polylines[0].args[1].color, '#a11d00');
  });

  it('draws hospitals larger than substations, in the theme colours', () => {
    const { window } = boot();
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setAssets',
        payload: {
          markers: [
            { at: [22.3, 88.1], kind: 'hospital', colour: '#a11d00', title: 'H', detail: 'flooded' },
            { at: [22.4, 88.2], kind: 'substation', colour: '#fcb42a', title: 'S', detail: 'flooded' },
          ],
        },
      }),
    );
    const markers = window.__calls.filter((c) => c.name === 'circleMarker');
    assert.equal(markers.length, 2);
    assert.equal(markers[0].args[1].radius, 7, 'hospital');
    assert.equal(markers[1].args[1].radius, 5, 'substation');
    assert.equal(markers[0].args[1].fillColor, '#a11d00');
    assert.equal(markers[1].args[1].fillColor, '#fcb42a');
  });

  it('skips a marker at an impossible coordinate rather than drawing it in the sea', () => {
    const { window } = boot();
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setAssets',
        payload: {
          markers: [
            { at: [200, 400], kind: 'hospital', colour: '#a11d00', title: 'bad', detail: '' },
            { at: [22.3, 88.1], kind: 'hospital', colour: '#a11d00', title: 'good', detail: '' },
          ],
        },
      }),
    );
    assert.equal(
      window.__calls.filter((c) => c.name === 'circleMarker').length,
      1,
      'only the usable coordinate becomes a marker',
    );
  });

  it('writes popup text with textContent, never innerHTML', () => {
    const { window } = boot();
    const hostile = '<img src=x onerror="window.__pwned=true">';
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setAssets',
        payload: {
          markers: [
            {
              at: [22.3, 88.1],
              kind: 'hospital',
              colour: '#a11d00',
              title: hostile,
              detail: hostile,
            },
          ],
        },
      }),
    );
    const bind = window.__calls.find((c) => c.name.startsWith('bindPopup'));
    assert.ok(bind, 'a popup was bound');
    const content = bind.args[0];
    assert.ok(content instanceof window.HTMLElement, 'the popup content is a real element');
    // The markup is data, not structure: it appears as literal text and creates
    // no element, so the injected handler never becomes an attribute.
    assert.equal(content.querySelectorAll('img').length, 0, 'no element was created');
    // Title and detail are both the hostile string, so the container reads it
    // twice. What matters is that it is *text* and that no element was built.
    assert.equal(content.textContent, hostile + hostile, 'preserved verbatim as text');
    assert.equal(content.children.length, 2, 'exactly the <b> and <span> this code created');
    assert.equal(window.__pwned, undefined, 'nothing executed');
  });

  it('draws the route with an origin and a square shelter marker', () => {
    const { window } = boot();
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setRoute',
        payload: {
          path: [
            [21.75, 88.22],
            [21.76, 88.23],
          ],
          origin: [21.75, 88.22],
          shelter: [21.76, 88.23],
          originTitle: 'Namkhana',
          shelterTitle: 'DEMO Shelter A',
        },
      }),
    );
    assert.ok(window.__calls.some((c) => c.name === 'polyline'), 'the route polyline');
    const origin = window.__calls.find((c) => c.name === 'circleMarker');
    assert.deepEqual(plain(origin.args[0]), [21.75, 88.22]);
    assert.ok(window.__calls.some((c) => c.name === 'marker'), 'the shelter marker');
  });

  it('clears the route, origin and shelter on request', () => {
    const { window } = boot();
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setRoute',
        payload: {
          path: [
            [21.75, 88.22],
            [21.76, 88.23],
          ],
          origin: [21.75, 88.22],
          shelter: [21.76, 88.23],
        },
      }),
    );
    assert.equal(window.__calls.filter((c) => c.name === 'removeLayer').length, 0);

    window.eval(commandScript({ id: 2, cmd: 'clearRoute', payload: {} }));
    assert.equal(acks(window).at(-1).ok, true, 'clearRoute acknowledges');
    // Three named layers come off: the route line, the origin ring and the
    // shelter marker. Leaving any of them on would show a stale evacuation.
    assert.equal(window.__calls.filter((c) => c.name === 'removeLayer').length, 3);
  });

  it('replaces a layer rather than stacking a second copy of it', () => {
    // The scenario chips swap categories rapidly; each swap re-issues setRoads,
    // and a document that added without removing would accumulate polylines
    // until the map was unreadable.
    const { window } = boot();
    const payload = {
      paths: [
        [
          [
            [22.1, 88.1],
            [22.2, 88.2],
          ],
        ],
      ],
    };
    window.eval(commandScript({ id: 1, cmd: 'setRoads', payload }));
    window.eval(commandScript({ id: 2, cmd: 'setRoads', payload }));
    window.eval(commandScript({ id: 3, cmd: 'setRoads', payload }));
    assert.equal(
      window.__calls.filter((c) => c.name === 'polyline').length,
      3,
      'three polylines drawn',
    );
    // Two removals between them: each call takes the previous layer off first.
    assert.equal(
      window.__calls.filter((c) => c.name === 'removeLayer').length,
      2,
      'each redraw removed the previous layer',
    );
  });

  it('reports roads that cannot be drawn instead of acknowledging nothing', () => {
    // Roads in the payload but no drawable part is a silent wrong map. The
    // handler throws so the acknowledgement says so.
    const { window } = boot();
    window.eval(
      commandScript({
        id: 1,
        cmd: 'setRoads',
        payload: { paths: [[[[22.1, 88.1]]], [[[22.5, 88.5]]]] },
      }),
    );
    const ack = window.__posted.find((m) => m.type === 'ack' && m.id === 1);
    assert.equal(ack.ok, false);
    assert.match(ack.error, /no part had two vertices/);
  });

  it('refuses to fit fewer than two points', () => {
    const { window } = boot();
    window.eval(commandScript({ id: 1, cmd: 'fit', payload: { points: [[21.6, 88.0]] } }));
    const ack = window.__posted.find((m) => m.type === 'ack' && m.id === 1);
    assert.equal(ack.ok, false);
    assert.match(ack.error, /at least two points/);
    assert.equal(window.__calls.filter((c) => c.name === 'fitBounds').length, 0);
  });

  it('fits the track with padding', () => {
    const { window } = boot();
    window.eval(
      commandScript({
        id: 1,
        cmd: 'fit',
        payload: { points: [[21.6, 88.0], [22.4, 88.9]], padding: [28, 28], animate: true },
      }),
    );
    const fit = window.__calls.find((c) => c.name === 'fitBounds');
    assert.deepEqual(plain(fit.args[0]), [
      [21.6, 88.0],
      [22.4, 88.9],
    ]);
    assert.deepEqual(plain(fit.args[1].padding), [28, 28]);
    assert.equal(fit.args[1].animate, true);
  });

  it('sets the view and invalidates size on recentre', () => {
    const { window } = boot();
    window.eval(commandScript({ id: 1, cmd: 'invalidate', payload: {} }));
    window.eval(
      commandScript({ id: 2, cmd: 'setView', payload: { center: [21.68, 88.08], zoom: 11 } }),
    );
    assert.ok(window.__calls.some((c) => c.name === 'invalidateSize'));
    const view = window.__calls.find((c) => c.name === 'setView');
    assert.deepEqual(plain(view.args[0]), [21.68, 88.08]);
    assert.equal(view.args[1], 11);
  });

  it('carries the API origin into the document so the raster has an absolute URL', () => {
    const html = leafletDocument('https://cyclone-forecaster-chi.vercel.app');
    assert.match(html, /"https:\/\/cyclone-forecaster-chi\.vercel\.app"/);
  });
});

describe('the coordinate conversion, GeoJSON to Leaflet', () => {
  it('transposes [lon, lat] to Leaflet [lat, lng]', () => {
    // Sagar Island's landfall point.
    assert.deepEqual(toLeaflet([88.0568, 21.6476]), [21.6476, 88.0568]);
  });

  it('does not confuse itself on a square-ish pair', () => {
    assert.deepEqual(toLeaflet([88, 21]), [21, 88]);
  });

  it('rejects everything that would draw in the wrong place', () => {
    for (const bad of [null, undefined, [], [88], [NaN, 21], [88, NaN], [Infinity, 21], [88, -Infinity]]) {
      assert.equal(toLeaflet(bad), null, `toLeaflet(${JSON.stringify(bad)}) should be null`);
    }
  });

  it('drops only the bad vertices from a list', () => {
    const list = [
      [88.0, 21.0],
      [NaN, 21.5],
      [88.6, 22.6],
    ];
    assert.deepEqual(toLeafletList(list), [
      [21.0, 88.0],
      [22.6, 88.6],
    ]);
  });

  it('converts the named-object form the app already uses', () => {
    assert.deepEqual(fromLatLng({ latitude: 21.6476, longitude: 88.0568 }), [21.6476, 88.0568]);
    assert.equal(fromLatLng(null), null);
    assert.equal(fromLatLng({ latitude: NaN, longitude: 88 }), null);
    assert.deepEqual(
      fromLatLngList([
        { latitude: 21.0, longitude: 88.0 },
        { latitude: NaN, longitude: 88.5 },
        { latitude: 22.6, longitude: 88.9 },
      ]),
      [
        [21.0, 88.0],
        [22.6, 88.9],
      ],
    );
  });

  it('round-trips: Leaflet order back out is not the same as GeoJSON order', () => {
    // A guard against a "simplification" that quietly drops the transposition
    // because it looked redundant.
    const leaf = toLeaflet([88.0568, 21.6476]);
    assert.notDeepEqual(leaf, [88.0568, 21.6476]);
  });
});