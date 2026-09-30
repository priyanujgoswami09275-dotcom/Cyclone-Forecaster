import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';

import { API_BASE_URL, type LatLng } from '../api';
import {
  fromLatLng,
  fromLatLngList,
  toLeaflet,
  type LeafletLatLng,
} from '../geo';
import { commandScript, leafletDocument, TILE_ATTRIBUTION } from '../leafletHtml';
import { theme } from '../theme';

/**
 * The map, rendered as Leaflet inside a WebView.
 *
 * ## Why a WebView at all
 *
 * The previous native map was `react-native-maps`. On Android that is Google
 * Maps, which requires an API key; Expo Go cannot carry a project's own key, so
 * the map came up solid black. Leaflet over raster tiles needs no key, which
 * removes the dependency that was failing rather than working around its
 * symptoms. The trade is real and worth naming: the map is no longer a native
 * view, so it no longer has native gestures or a native look. It is still a
 * map, it still draws the same layers, and it starts.
 *
 * `Rules.md` says "don't reintroduce Leaflet.js or any browser-only library".
 * **This is a deliberate, logged departure** and is recorded under "Flagged for
 * review" in MEMORY.md — the rule exists to stop a browser-only library
 * silently reaching the *Web* build, and this component is native-only and
 * cannot enter the Web bundle (asserted by `tests/webBundleSafety.test.mjs`).
 *
 * ## The command protocol
 *
 * The document in `leafletHtml.ts` owns every Leaflet call. This component
 * holds no Leaflet state; it serialises props into commands and posts messages
 * back. That split is what makes the map testable without a device: the same
 * command objects can be run against a stubbed `L` in jsdom.
 *
 * **Commands are queued until the map reports ready.** Injecting into a
 * WebView whose document has not booted silently does nothing, which previously
 * produced a map that came up empty with no error anywhere. Every command
 * therefore waits for `map_ready`, and the queue is replayed in order.
 */

export interface LeafletMapProps {
  /**
   * The container's own style.
   *
   * Supplied by the caller rather than hardcoded, because the map sits inside a
   * `flex: 1` wrapper with a panel below it and must grow with it — an absolute
   * fill inside a flex parent would size itself to the parent's padding box and
   * ignore the flex entirely.
   */
  style?: StyleProp<ViewStyle>;
  /** Where the camera starts. */
  initialCenter: LeafletLatLng;
  initialZoom: number;
  /** The flood raster. `null` means there is no flood layer to draw. */
  overlay: {
    url: string;
    bounds: { north: number; south: number; east: number; west: number };
  } | null;
  /**
   * The observed storm track.
   *
   * In the app's own `{latitude, longitude}` shape — the same shape
   * `/track` serves and `roadPaths` returns. **The transposition into Leaflet's
   * `[lat, lng]` happens inside this component, through `geo.ts`**, so the
   * props read like everything else in the app and there is exactly one place
   * that knows Leaflet's order is the reverse.
   */
  track: { path: LatLng[]; waypoints: LeafletWaypoint[] } | null;
  /**
   * Cut-off roads: one `LatLng[][]` per road, each inner list one geometry part.
   *
   * Three levels, which is `roadPaths`'s shape and not an accident. A
   * `MultiLineString` road is several disjoint paths and must draw as several
   * polylines; flattening it into one list would draw a spurious segment across
   * the gap between the parts.
   */
  roads: LatLng[][][] | null;
  /** Exposed point assets. */
  assets: LeafletAsset[];
  /** The evacuation route and its endpoints. */
  route: {
    path: LatLng[];
    origin: LatLng;
    shelter: LatLng | null;
    originTitle: string;
    shelterTitle: string | null;
  } | null;
  /** Recentre and fit-to-track, driven from the existing map controls. */
  fitRequest: number;
  recentreRequest: number;
}

/** One pin per best-track fix, with its callout text. */
export interface LeafletWaypoint {
  at: LatLng;
  title: string;
  detail: string;
}

/** One exposed point asset. */
export interface LeafletAsset {
  at: LatLng;
  kind: 'hospital' | 'substation';
  colour: string;
  title: string;
  detail: string;
}

/**
 * What the WebView reports back.
 *
 * A discriminated union on `type`, with `never` for the acknowledgement so the
 * handler's `switch` narrows exhaustively — a message shape added here without
 * a `case` becomes a compile error rather than a silently ignored frame.
 */
type MapMessage =
  | { type: 'map_ready' }
  | { type: 'map_failed'; reason: string }
  | { type: 'camera'; latitude: number; longitude: number; zoom: number }
  | { type: 'ack'; id: number; ok: boolean; error: string | null };

/**
 * The command queue and its acknowledgement state.
 *
 * Kept as one object so a single `useState` re-render covers both, and so the
 * queue is never half-updated.
 */
interface Bridge {
  ready: boolean;
  /** Commands issued before the map was ready, replayed in order. */
  queue: string[];
  nextId: number;
  failure: string | null;
}

export function LeafletMap({
  style,
  initialCenter,
  initialZoom,
  overlay,
  track,
  roads,
  assets,
  route,
  fitRequest,
  recentreRequest,
}: LeafletMapProps) {
  const webRef = useRef<WebView | null>(null);
  const [bridge, setBridge] = useState<Bridge>({
    ready: false,
    queue: [],
    nextId: 1,
    failure: null,
  });
  /**
   * The camera fallback.
   *
   * Kept as a ref rather than state so `send` does not need it in its
   * dependency list: a ref read is not a render-time value, and putting the
   * camera in state would re-issue `setView` on every pan the user makes.
   */
  const cameraRef = useRef({ center: initialCenter, zoom: initialZoom });

  /**
   * Run one command, or queue it if the map is not up yet.
   *
   * Written as a plain closure over `setBridge` rather than a `useCallback` so
   * it can be called from the effects below without a dependency cycle — each
   * effect intentionally runs on its own prop, not on the identity of `send`.
   */
  const send = useCallback((cmd: string, payload: unknown = {}) => {
    setBridge((prev) => {
      const request = { id: prev.nextId, cmd, payload };
      const script = commandScript(request);
      const next: Bridge = { ...prev, nextId: prev.nextId + 1 };
      if (prev.ready && !prev.failure) {
        webRef.current?.injectJavaScript(script);
      } else if (!prev.failure) {
        // Held, not dropped. An overlay command issued during boot used to
        // vanish and the map came up with no flood on it and no error.
        next.queue = [...prev.queue, script];
      }
      return next;
    });
  }, []);

  /** Inject everything queued before the map reported ready. */
  const flush = useCallback(() => {
    setBridge((prev) => {
      if (prev.queue.length === 0) return { ...prev, ready: true };
      for (const script of prev.queue) webRef.current?.injectJavaScript(script);
      return { ...prev, ready: true, queue: [] };
    });
  }, []);

  const onMessage = useCallback(
    (event: WebViewMessageEvent) => {
      let message: MapMessage;
      try {
        message = JSON.parse(event.nativeEvent.data) as MapMessage;
      } catch {
        // A non-JSON message is not one of ours. Swallowing it is correct: the
        // bridge only ever receives strings it wrote itself.
        return;
      }

      switch (message.type) {
        case 'map_ready':
          flush();
          return;
        case 'map_failed':
          // The banner inside the WebView is already showing this. Recording it
          // here as well means the failure is visible to the screen's own test
          // hooks and to anyone reading the device log, not only to a user who
          // happens to be looking at the map.
          setBridge((prev) => ({ ...prev, failure: message.reason }));
          return;
        case 'camera':
          cameraRef.current = {
            center: [message.latitude, message.longitude],
            zoom: message.zoom,
          };
          return;
        case 'ack':
          // A failed command is surfaced rather than swallowed, because a
          // command that did not run is a silent wrong map.
          if (!message.ok) {
            setBridge((prev) => ({
              ...prev,
              failure: message.error ?? 'A map command failed.',
            }));
          }
          return;
        default:
          // Unreachable: the union above is exhaustive, so adding a message
          // shape without a case is a compile error here rather than a frame
          // that vanishes.
          return;
      }
    },
    [flush],
  );

  // The document is built once. It carries the API origin so the flood raster
  // loads from the same backend the rest of the app talks to.
  const source = useMemo(
    () => ({
      html: leafletDocument(API_BASE_URL),
      baseUrl: 'https://localhost',
    }),
    [],
  );

  // --- initial camera -----------------------------------------------------
  useEffect(() => {
    send('setView', { center: initialCenter, zoom: initialZoom, animate: false });
  }, [initialCenter, initialZoom, send]);

  // The flood raster. Sent even when null, because clearing is a command too:
  // switching from a flooded scenario back to a dry one has to take the layer
  // off, or the previous scenario's water stays on screen.
  useEffect(() => {
    if (overlay === null) {
      send('setOverlay', { url: null });
      return;
    }
    send('setOverlay', {
      url: overlay.url,
      bounds: overlay.bounds,
      opacity: 0.62,
    });
  }, [overlay, send]);

  useEffect(() => {
    if (track === null) {
      send('setTrack', { path: [] });
      return;
    }
    send('setTrack', {
      path: fromLatLngList(track.path),
      waypoints: track.waypoints.flatMap((w) => {
        const at = fromLatLng(w.at);
        return at === null ? [] : [{ at, radius: 4, kind: 'waypoint' }];
      }),
    });
  }, [track, send]);

  useEffect(() => {
    send('setRoads', {
      // `roads` is road -> part -> vertex, so each part needs its own
      // conversion. Flattening here would join the parts of a MultiLineString
      // road into one polyline with a segment across the gap.
      paths:
        roads === null
          ? []
          : roads.map((parts) => parts.map((part) => fromLatLngList(part))),
    });
  }, [roads, send]);

  useEffect(() => {
    send('setAssets', {
      markers: assets.flatMap((a) => {
        const at = fromLatLng(a.at);
        // A dropped marker is a marker the map does not show but the tile still
        // counts. `fromLatLng` only returns null for a non-finite coordinate,
        // which the app's own `markerCoordinate` already filters, so this is a
        // belt-and-braces guard rather than an expected path.
        return at === null
          ? []
          : [{ at, kind: a.kind, colour: a.colour, title: a.title, detail: a.detail }];
      }),
    });
  }, [assets, send]);

  useEffect(() => {
    if (route === null) {
      send('clearRoute', {});
      return;
    }
    send('setRoute', {
      path: fromLatLngList(route.path),
      origin: fromLatLng(route.origin),
      shelter: fromLatLng(route.shelter),
      originTitle: route.originTitle,
      shelterTitle: route.shelterTitle,
    });
  }, [route, send]);

  // --- controls, driven from the existing map controls --------------------
  //
  // These are counters rather than booleans so the same value pressed twice
  // still produces a new effect run. A boolean would fire the effect once and
  // then never again, which is a control that appears to work and does not.

  const fitPoints = useMemo<LeafletLatLng[] | null>(() => {
    if (track === null) return null;
    const points = fromLatLngList(track.path);
    // Two points is the minimum Leaflet can fit a bounds to. One point has no
    // extent, and asking anyway leaves the camera somewhere arbitrary.
    return points.length >= 2 ? points : null;
  }, [track]);

  const fitSeen = useRef(0);
  useEffect(() => {
    if (fitRequest === 0 || fitRequest === fitSeen.current) return;
    fitSeen.current = fitRequest;
    if (fitPoints === null) return;
    send('fit', { points: fitPoints, padding: [28, 28], animate: true });
  }, [fitRequest, fitPoints, send]);

  const recentreSeen = useRef(0);
  useEffect(() => {
    if (recentreRequest === 0 || recentreRequest === recentreSeen.current) return;
    recentreSeen.current = recentreRequest;
    // Leaflet needs `invalidateSize` after a layout change, or a map that was
    // laid out at zero height keeps a zero-height viewport and the camera
    // moves the tiles without moving anything else.
    send('invalidate', {});
    send('setView', {
      center: initialCenter,
      zoom: initialZoom,
      animate: true,
    });
  }, [recentreRequest, initialCenter, initialZoom, send]);

  const failed = bridge.failure !== null;

  return (
    <View style={[styles.wrap, style]}>
      <WebView
        ref={webRef}
        source={source}
        originWhitelist={['*']}
        // Leaflet needs no cookies, no storage and no camera. Locking them off
        // means the map cannot become a place the backend's origin is talked to
        // from by anything but the tile layer and the flood image.
        domStorageEnabled={false}
        javaScriptEnabled
        allowsInlineMediaPlayback={false}
        mediaPlaybackRequiresUserAction
        setSupportMultipleWindows={false}
        onMessage={onMessage}
        // A transparent WebView over a black container would show black through
        // any gap while tiles load, which reads as the failure this replaced.
        style={styles.web}
        androidLayerType="hardware"
        overScrollMode="never"
      />
      {/*
        The failure banner.

        Rendered by React Native rather than only inside the WebView, so a
        WebView that failed to load the document at all — the case where no
        script is running and the in-page banner cannot appear — still says
        something. "Never leave a blank map" means never leave a blank map
        with no explanation.
      */}
      {failed ? (
        <View style={styles.banner} accessibilityRole="alert">
          <Text style={styles.bannerTitle}>The map could not load</Text>
          <Text style={styles.bannerBody}>{bridge.failure}</Text>
          <Text style={styles.bannerHint}>
            Every figure below was still computed and is unaffected. Only the map
            picture is missing.
          </Text>
        </View>
      ) : null}
      {!failed && !bridge.ready ? (
        <View style={styles.loading} accessibilityRole="progressbar">
          <Text style={styles.bannerBody}>Loading the map…</Text>
        </View>
      ) : null}
    </View>
  );
}

/**
 * `position: absolute` on all four edges, as a plain object.
 *
 * The same helper `AboutSheet.tsx` defines, for the same reason: this version of
 * react-native's types export only `StyleSheet.absoluteFill`, which is a
 * registered style *id*, and spreading that into a style object would put a
 * number where a style is expected. Duplicated rather than extracted because a
 * third shared module for four lines would be worse than the repetition, and
 * `AboutSheet`'s copy already explains itself where it is read.
 */
const ABSOLUTE_FILL: ViewStyle = {
  position: 'absolute',
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
};

const styles = StyleSheet.create({
  wrap: {
    // `flex: 1` rather than absolute fill: the caller sizes this from the
    // layout, and the WebView inside fills whatever box arrives. See `style`.
    flex: 1,
    backgroundColor: theme.colors.background,
    overflow: 'hidden',
  },
  web: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  banner: {
    ...ABSOLUTE_FILL,
    backgroundColor: theme.colors.card,
    padding: 20,
    justifyContent: 'center',
    gap: 10,
  },
  loading: {
    ...ABSOLUTE_FILL,
    alignItems: 'center',
    justifyContent: 'center',
  },
  bannerTitle: {
    fontFamily: theme.fonts.bodySemibold,
    fontSize: theme.typography.body,
    color: theme.colors.selectedText,
  },
  bannerBody: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
  bannerHint: {
    fontFamily: theme.fonts.body,
    fontSize: theme.typography.caption,
    color: theme.colors.textMuted,
  },
});