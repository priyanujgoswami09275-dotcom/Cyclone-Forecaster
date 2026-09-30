/**
 * The HTML document loaded into the WebView, and the small command language the
 * React Native side drives it with.
 *
 * ## Why this exists
 *
 * The native map was `react-native-maps`. On Android that renders through
 * Google Maps, which needs an API key; Expo Go cannot carry a project's own key,
 * so the map came up blank or black. Leaflet over raster tiles needs no key at
 * all, which removes the dependency that was failing.
 *
 * ## Why it is a string and not a component
 *
 * The map has to be built inside the WebView's own JavaScript context, so the
 * document *is* the implementation. Keeping it in a module rather than inline
 * in the `.tsx` means it can be rendered headlessly — `tests/leafletMap.test.mjs`
 * loads it in jsdom, runs the real command handlers against a stubbed `L`, and
 * asserts on the layers that come out. That is the only verification available
 * without a phone, so it is worth having.
 *
 * ## The command protocol
 *
 * React Native -> WebView: `webviewRef.current.injectJavaScript(cmd)` where
 * `cmd` is `window.__cyclone({...})({json})`. One call, one command.
 *
 * WebView -> React Native: `window.ReactNativeWebView.postMessage(json)`.
 *
 * Every command is acknowledged with `{id, ok}` so the caller can tell "the map
 * did it" from "the message never arrived" — which is the distinction that
 * matters when the WebView has not finished loading yet.
 *
 * ## Safety
 *
 * Popup content is written with `textContent`, never `innerHTML`. Asset names
 * come from OpenStreetMap and are attacker-controllable in principle; a
 * `<script>` in an OSM `name` would otherwise execute with WebView privileges.
 * There is exactly one place content is put into the DOM as markup — the
 * static basemap `<img>` and the tile layer — and neither interpolates data.
 */

/** Leaflet version, pinned. */
export const LEAFLET_VERSION = '1.9.4';

/**
 * Tile sources, tried in order.
 *
 * CARTO's light basemap needs no API key and no account, which is the whole
 * reason this route works where `react-native-maps` did not. jsDelivr is a
 * fallback for the *library*; if both CDNs are unreachable the map shows the
 * failure banner rather than a blank rectangle.
 */
export const LEAFLET_SOURCES = [
  `https://cdnjs.cloudflare.com/ajax/libs/leaflet/${LEAFLET_VERSION}/leaflet.js`,
  `https://cdn.jsdelivr.net/npm/leaflet@${LEAFLET_VERSION}/dist/leaflet.js`,
] as const;

export const LEAFLET_STYLESHEET = [
  `https://cdnjs.cloudflare.com/ajax/libs/leaflet/${LEAFLET_VERSION}/leaflet.css`,
  `https://cdn.jsdelivr.net/npm/leaflet@${LEAFLET_VERSION}/dist/leaflet.css`,
] as const;

/** Raster tiles. Keyless, light, and matches the app's dark-UI/pale-map split. */
export const TILE_URL =
  'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';
export const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

/**
 * The full document.
 *
 * `API_ORIGIN` is injected at runtime by the WebView's own `source` prop rather
 * than baked in, so a rebuild is not needed to point at a different backend —
 * and so the overlay URL keeps flowing through the same
 * `EXPO_PUBLIC_API_URL` the rest of the app reads.
 *
 * The tile constants are injected too, and that is not decoration. The first
 * version of this document read `TILE_URL` and `TILE_ATTRIBUTION` as bare
 * identifiers inside the WebView's script, where they do not exist: they are
 * TypeScript module constants, and nothing carries them across the boundary.
 * `boot()` therefore threw a `ReferenceError` on the tile layer, was caught by
 * its own `try`, and reported "the map library loaded but could not start" —
 * on a real device. **The jsdom test in `tests/leafletMap.test.mjs` is what
 * caught it**; it asserts that a `tileLayer` call was recorded.
 */
export function leafletDocument(apiOrigin: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<title>Cyclone impact map</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #090909; }
  #map { position: absolute; inset: 0; }
  /* The banner replaces the map, it does not cover it: a translucent overlay
     over a working map would hide the data while claiming the map is broken. */
  #banner {
    position: absolute; inset: 0; display: none; z-index: 1000;
    background: #0c0c0b; color: #eeedea; padding: 20px;
    font: 14px/1.5 system-ui, -apple-system, sans-serif;
    flex-direction: column; justify-content: center; gap: 10px;
  }
  #banner.on { display: flex; }
  #banner h1 { font-size: 15px; margin: 0; color: #5f9dea; }
  #banner p { margin: 0; color: #bcbbaf; }
  .pin { border-radius: 50%; border: 2px solid #090909; }
  .leaflet-popup-content { margin: 10px 12px; font: 13px/1.4 system-ui, sans-serif; color: #181d26; }
  .leaflet-popup-content b { display: block; font-size: 13px; }
  .leaflet-popup-content span { color: #555; }
  .leaflet-container { background: #090909; font: 13px system-ui, sans-serif; }
</style>
${LEAFLET_STYLESHEET.map((href) => `<link rel="stylesheet" href="${href}" />`).join('\n')}
</head>
<body>
<div id="map"></div>
<div id="banner" role="alert">
  <h1>The map could not load</h1>
  <p id="banner-detail">The map library or its map tiles could not be reached.</p>
</div>
<script>
(function () {
  'use strict';

  var API_ORIGIN = ${JSON.stringify(apiOrigin)};
  var TILE_URL = ${JSON.stringify(TILE_URL)};
  var TILE_ATTRIBUTION = ${JSON.stringify(TILE_ATTRIBUTION)};
  var READY = false;
  var FAILED = false;
  var map = null;
  var layers = {
    overlay: null,
    track: null,
    waypoints: null,
    roads: null,
    assets: null,
    route: null,
    origin: null,
    shelter: null,
  };

  function post(payload) {
    if (window.ReactNativeWebView && window.ReactNativeWebView.postMessage) {
      window.ReactNativeWebView.postMessage(JSON.stringify(payload));
    }
  }

  function ack(id, ok, error) {
    post({ type: 'ack', id: id, ok: ok, error: error === undefined ? null : error });
  }

  function fail(detail) {
    FAILED = true;
    var el = document.getElementById('banner');
    var d = document.getElementById('banner-detail');
    if (d && detail) d.textContent = detail;
    if (el) el.className = 'on';
    post({ type: 'map_failed', reason: detail || 'unknown' });
  }

  function ready() {
    READY = true;
    post({ type: 'map_ready' });
  }

  // --- library loading ----------------------------------------------------
  // Sequential rather than parallel so a slow cdnjs does not race a fast
  // jsdelivr and leave two Leaflets defining the same global.
  var sources = ${JSON.stringify(LEAFLET_SOURCES)};
  var idx = 0;
  function loadNext() {
    if (idx >= sources.length) {
      fail('The map library could not be downloaded. Check the device has a working internet connection.');
      return;
    }
    var src = sources[idx++];
    var s = document.createElement('script');
    s.src = src;
    s.onload = function () {
      if (typeof L === 'undefined') { loadNext(); return; }
      boot();
    };
    s.onerror = loadNext;
    document.head.appendChild(s);
  }

  function boot() {
    try {
      map = L.map('map', {
        zoomControl: true,
        attributionControl: true,
        // The flood raster is north-up and the app has no bearing control, so
        // rotation is off for the same reason react-native-maps had it off.
        rotate: false,
        // Matches \`rotateEnabled={false} pitchEnabled={false}\` on the old
        // native map: a tilted view would put the water at the wrong angle to
        // the coast, and correctness beats a gesture.
        pitch: false,
        // Panning stays on. Zoom is pinch-enabled, which Leaflet handles
        // natively; this only disables double-tap-to-zoom so a double tap does
        // not fire the recentre command underneath.
        doubleClickZoom: false,
      });
      L.tileLayer(TILE_URL, {
        attribution: TILE_ATTRIBUTION,
        maxZoom: 19,
      }).addTo(map);

      // A tile layer that never loads leaves a blank grey rectangle. Leaflet
      // fires \`tileerror\` per tile, which is far too chatty to report, so this
      // counts errors within a short window and only gives up after several.
      var tileErrors = 0;
      var tileTimer = null;
      map.on('tileerror', function () {
        tileErrors += 1;
        if (tileTimer) clearTimeout(tileTimer);
        tileTimer = setTimeout(function () {
          if (tileErrors >= 4 && !FAILED) {
            fail('The map tiles could not be loaded. The library loaded, but no map data came back — this needs a working internet connection.');
          }
        }, 2500);
      });

      map.on('moveend zoomend', function () {
        var c = map.getCenter();
        post({
          type: 'camera',
          latitude: c.lat,
          longitude: c.lng,
          zoom: map.getZoom(),
        });
      });

      ready();
    } catch (err) {
      fail('The map library loaded but could not start: ' + (err && err.message ? err.message : String(err)));
    }
  }

  // --- layer helpers ------------------------------------------------------

  function eachLayerGroup(key) {
    var g = layers[key];
    if (g && map.hasLayer(g)) map.removeLayer(g);
    layers[key] = null;
  }

  function needMap(cmd) {
    if (FAILED) throw new Error('The map did not load, so nothing can be drawn on it.');
    if (!map) throw new Error('The map is not ready yet.');
  }

  /** Only the poles and the antimeridian break Leaflet; this rejects the rest. */
  function validLatLng(p) {
    return (
      Array.isArray(p) &&
      p.length >= 2 &&
      Number.isFinite(p[0]) &&
      Number.isFinite(p[1]) &&
      Math.abs(p[0]) <= 90 &&
      Math.abs(p[1]) <= 180
    );
  }

  // --- commands -----------------------------------------------------------

  var commands = {
    setView: function (c) {
      needMap(this);
      map.setView(c.center, c.zoom, { animate: !!c.animate });
    },

    /** The flood raster, as a Leaflet ImageOverlay with explicit bounds. */
    setOverlay: function (c) {
      needMap(this);
      eachLayerGroup('overlay');
      if (!c.url) return;
      var b = c.bounds;
      if (!b) throw new Error('The flood layer arrived without bounds, so it cannot be placed.');
      layers.overlay = L.imageOverlay(c.url, [[b.south, b.west], [b.north, b.east]], {
        opacity: c.opacity === undefined ? 0.62 : c.opacity,
        interactive: false,
      }).addTo(map);
    },

    /** The observed storm track. */
    setTrack: function (c) {
      needMap(this);
      eachLayerGroup('track');
      eachLayerGroup('waypoints');
      if (!c.path || c.path.length < 2) return;
      layers.track = L.polyline(c.path, {
        color: c.colour || '#5f9dea',
        weight: 3,
        opacity: 0.9,
        dashArray: c.dash || null,
      }).addTo(map);
      if (c.waypoints) {
        layers.waypoints = L.layerGroup(c.waypoints).addTo(map);
      }
    },

    /** One pin per best-track fix, with an accessible popup. */
    setWaypoints: function (c) {
      needMap(this);
      eachLayerGroup('waypoints');
      if (!c.waypoints || c.waypoints.length === 0) return;
      layers.waypoints = L.layerGroup(c.waypoints).addTo(map);
    },

    /**
     * Cut-off roads. One polyline per geometry part, as the native map did.
     *
     * paths is road -> part -> vertex, which is roadPaths' shape.
     * Iterating one level too shallow is easy and wrong: it would treat a road's
     * *part list* as if it were a vertex list, find that a part list has one
     * element where two are needed, skip everything, and acknowledge success —
     * drawing no roads at all. That is what the first version of this handler
     * did, and the jsdom test caught it.
     *
     * Part boundaries are preserved rather than flattened, so a MultiLineString
     * road does not gain a segment across the gap between its disjoint halves.
     *
     * (Backticks are avoided throughout this document's comments: they would
     * terminate the template literal this script is written inside.)
     */
    setRoads: function (c) {
      needMap(this);
      eachLayerGroup('roads');
      if (!c.paths || c.paths.length === 0) return;
      var group = L.layerGroup();
      var drawn = 0;
      for (var i = 0; i < c.paths.length; i++) {
        var parts = c.paths[i] || [];
        for (var j = 0; j < parts.length; j++) {
          var path = parts[j];
          // Two vertices is the minimum a line can have; fewer is not a road.
          if (!path || path.length < 2) continue;
          L.polyline(path, {
            color: c.colour || '#a11d00',
            weight: 2,
            opacity: 0.85,
            dashArray: c.dash || '6 5',
          }).addTo(group);
          drawn += 1;
        }
      }
      // Roads in the payload but nothing drawn is a silent wrong map, which is
      // the failure this command exists to avoid. Say so instead.
      if (drawn === 0) {
        throw new Error(
          'Roads were requested but no part had two vertices to draw between.'
        );
      }
      layers.roads = group.addTo(map);
    },

    /** Exposed hospitals and substations, as circle markers. */
    setAssets: function (c) {
      needMap(this);
      eachLayerGroup('assets');
      if (!c.markers || c.markers.length === 0) return;
      var group = L.layerGroup();
      for (var i = 0; i < c.markers.length; i++) {
        var m = c.markers[i];
        if (!validLatLng(m.at)) continue;
        var marker = L.circleMarker(m.at, {
          radius: m.kind === 'hospital' ? 7 : 5,
          color: '#090909',
          weight: 2,
          fillColor: m.colour,
          fillOpacity: 0.95,
        }).addTo(group);
        bindPopup(marker, m.title, m.detail);
      }
      layers.assets = group.addTo(map);
    },

    /** The evacuation route, and its origin and shelter. */
    setRoute: function (c) {
      needMap(this);
      eachLayerGroup('route');
      eachLayerGroup('origin');
      eachLayerGroup('shelter');
      if (c.path && c.path.length >= 2) {
        layers.route = L.polyline(c.path, {
          color: '#0d7a3f',
          weight: 4,
          opacity: 0.95,
        }).addTo(map);
      }
      if (validLatLng(c.origin)) {
        layers.origin = L.circleMarker(c.origin, {
          radius: 10,
          color: '#090909',
          weight: 3,
          fillColor: '#0d7a3f',
          fillOpacity: 1,
        }).addTo(map);
        bindPopup(layers.origin, c.originTitle, c.originDetail);
      }
      if (validLatLng(c.shelter)) {
        layers.shelter = L.marker(c.shelter, {
          icon: L.divIcon({
            className: '',
            html: '<div style="width:14px;height:14px;background:#5f9dea;border:2px solid #090909;transform:rotate(45deg)"></div>',
            iconSize: [14, 14],
            iconAnchor: [7, 7],
          }),
          keyboard: false,
        }).addTo(map);
        bindPopup(layers.shelter, c.shelterTitle, c.shelterDetail);
      }
    },

    clearRoute: function () {
      needMap(this);
      eachLayerGroup('route');
      eachLayerGroup('origin');
      eachLayerGroup('shelter');
    },

    /** Fit a set of points. Rejected below two, so the camera is left alone. */
    fit: function (c) {
      needMap(this);
      if (!c.points || c.points.length < 2) {
        throw new Error('Fitting the map needs at least two points.');
      }
      for (var i = 0; i < c.points.length; i++) {
        if (!validLatLng(c.points[i])) {
          throw new Error('Cannot fit the map: one of the points is not a real coordinate.');
        }
      }
      map.fitBounds(c.points, {
        padding: c.padding || [28, 28],
        animate: !!c.animate,
      });
    },

    fitOverlay: function () {
      needMap(this);
      if (layers.overlay) {
        map.fitBounds(layers.overlay.getBounds(), {
          padding: [28, 28],
          animate: true,
        });
      }
    },

    invalidate: function () {
      if (map) map.invalidateSize();
    },
  };

  /**
   * Popup content, written with textContent.
   *
   * Asset names come from OpenStreetMap. Using innerHTML here would let a name
   * containing markup execute in the WebView's context; textContent cannot.
   */
  function bindPopup(layer, title, detail) {
    if (!layer || !title) return;
    var content = document.createElement('div');
    var b = document.createElement('b');
    b.textContent = title;
    content.appendChild(b);
    if (detail) {
      var span = document.createElement('span');
      span.textContent = detail;
      content.appendChild(span);
    }
    layer.bindPopup(content);
  }

  // A single entry point, so the React Native side never has to know the
  // command names as globals and can pass a request id straight through.
  window.__cyclone = function (req) {
    var handler = commands[req.cmd];
    if (!handler) {
      ack(req.id, false, 'Unknown map command: ' + String(req.cmd));
      return;
    }
    try {
      handler.call(null, req.payload || {});
      ack(req.id, true);
    } catch (err) {
      ack(req.id, false, err && err.message ? err.message : String(err));
    }
  };

  // Markers are created React-Native-side as {at, kind, colour, title, detail}
  // so this document never has to know about GeoJSON.
  window.__cycloneMarker = function (at, kind, colour, title, detail) {
    var m = L.circleMarker(at, {
      radius: kind === 'hospital' ? 7 : 5,
      color: '#090909',
      weight: 2,
      fillColor: colour,
      fillOpacity: 0.95,
    });
    bindPopup(m, title, detail);
    return m;
  };

  loadNext();
})();
</script>
</body>
</html>`;
}

/**
 * The JavaScript React Native injects to run one command.
 *
 * Kept next to the document so the two cannot drift: the command names in
 * `commands` above are the only ones this will run.
 */
export function commandScript(request: {
  id: number;
  cmd: string;
  payload?: unknown;
}): string {
  const json = JSON.stringify(request.payload ?? {});
  return `window.__cyclone && window.__cyclone({id:${request.id},cmd:${JSON.stringify(
    request.cmd,
  )},payload:${json}});true;`;
}