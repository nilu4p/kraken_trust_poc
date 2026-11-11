import { memo, useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { CircleMarker, GeoJSON, MapContainer, Marker, Polyline, Popup, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { BASEMAPS, OVERLAYS, goesLayer, nightLightsDate, nightLightsUrl } from "../imagery.js";
import VesselProfile from "./VesselProfile.jsx";
import { customTileUrl, sentinel2TileUrl } from "../services/api.js";
import {
  AIS_CATEGORIES,
  AIS_COLOR,
  EFFORT_COLOR,
  EFFORT_FOREIGN_COLOR,
  EVENTS,
  FISHING_LIMITS,
  FEEDS,
  FOOTPRINTS,
  GFW_SAR,
  ISOBATH,
  MPA,
  SELECTED_COLOR,
  TARGET,
  ZONES,
  targetClass,
} from "../theme.js";

const GUYANE_CENTER = [5.6, -52.4];
// The site sends no Referer (nginx "no-referrer"), but OpenStreetMap's tile usage policy
// requires one: tile requests alone send the site origin (never the page path).
const TILE_REFERRER = "strict-origin-when-cross-origin";
const iconCache = new globalThis.Map();

function darkIcon(color, selected, dimmed, pulse = true) {
  const key = `${color}|${selected}|${dimmed}|${pulse}`;
  if (!iconCache.has(key)) {
    iconCache.set(
      key,
      L.divIcon({
        className: "",
        html: `<span class="dark-vessel-marker${selected ? " is-selected" : ""}${dimmed ? " is-dimmed" : ""}${pulse ? "" : " no-pulse"}" style="--marker:${color}"><span class="pulse"></span><span class="core"></span></span>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15],
      }),
    );
  }
  return iconCache.get(key);
}

const AIS_ICON = L.divIcon({
  className: "",
  html: `<span class="ais-vessel-marker" style="--marker:${AIS_COLOR}"></span>`,
  iconSize: [14, 14],
  iconAnchor: [7, 7],
});

// AIS vessel on the IUU watchlist: same green core, thick red ring.
const LISTED_AIS_ICON = L.divIcon({
  className: "",
  html: `<span class="ais-vessel-marker is-listed" style="--marker:${AIS_COLOR};--ring:${TARGET.listed.color}"></span>`,
  iconSize: [20, 20],
  iconAnchor: [10, 10],
});

const arrowCache = new globalThis.Map();

/** Heading arrow for a live AIS vessel (rotation bucketed to 15° to keep the icon cache small). */
function aisArrow(category, course) {
  const angle = course == null ? null : Math.round(course / 15) * 15;
  const key = `${category}|${angle}`;
  if (!arrowCache.has(key)) {
    const color = (AIS_CATEGORIES[category] || AIS_CATEGORIES.unknown).color;
    const ring = category === "listed" ? `<circle cx="9" cy="9" r="8" fill="none" stroke="${color}" stroke-width="2"/>` : "";
    const shape =
      angle == null
        ? `<circle cx="9" cy="9" r="4" fill="${color}" stroke="#070b10" stroke-width="1.5"/>`
        : `<path d="M9 2 L14 15 L9 12 L4 15 Z" fill="${color}" stroke="#070b10" stroke-width="1.2" transform="rotate(${angle} 9 9)"/>`;
    arrowCache.set(
      key,
      L.divIcon({
        className: "",
        html: `<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">${ring}${shape}</svg>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      }),
    );
  }
  return arrowCache.get(key);
}

const sarIcons = {};
function sarIcon(matched) {
  const key = matched ? "matched" : "unmatched";
  if (!sarIcons[key]) {
    const color = GFW_SAR[key].color;
    sarIcons[key] = L.divIcon({
      className: "",
      html: `<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect x="2.5" y="2.5" width="7" height="7" transform="rotate(45 6 6)" fill="${color}" fill-opacity="0.8" stroke="#070b10"/></svg>`,
      iconSize: [12, 12],
      iconAnchor: [6, 6],
    });
  }
  return sarIcons[key];
}

function limitStyle(feature) {
  const spec = FISHING_LIMITS[feature.properties?.limit] || FISHING_LIMITS.fishing_100nm;
  return { color: spec.color, weight: 1.5, dashArray: spec.dash, opacity: 0.9 };
}

function latLng(feature) {
  const [lon, lat] = feature?.geometry?.coordinates || [];
  return lat == null || lon == null ? null : [lat, lon];
}

function bearing([lon1, lat1], [lon2, lat2]) {
  const toRad = (d) => (d * Math.PI) / 180;
  const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
  return (((Math.atan2(y, x) * 180) / Math.PI) + 360) % 360;
}

function formatDate(value) {
  return value ? String(value).slice(0, 16).replace("T", " ") : "—";
}

function zoneStyle(feature) {
  const zone = ZONES[feature.properties?.zone] || ZONES.eez;
  return {
    color: zone.color,
    weight: zone.weight,
    dashArray: zone.dash,
    fillColor: zone.color,
    fillOpacity: zone.fill,
  };
}

function mpaStyle() {
  return { color: MPA.color, weight: 1.5, dashArray: "4 3", fillColor: MPA.color, fillOpacity: 0.15 };
}

function isobathStyle(feature) {
  const depth = feature.properties?.depth_m || 0;
  if (depth === 200) return { color: ISOBATH.shelf, weight: 1.8, opacity: 0.85 };
  return { color: depth > 200 ? ISOBATH.deep : ISOBATH.color, weight: 0.8, opacity: depth > 200 ? 0.45 : 0.35 };
}

function footprintStyle(color) {
  return () => ({ color, weight: 1, dashArray: "3 3", fillColor: color, fillOpacity: 0.05 });
}

// Leaflet renders attributions as HTML: text typed in the administration is escaped.
function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/**
 * Popup of a custom-source object. Data comes from an external service: built with
 * textContent only (never innerHTML), so a hostile value cannot inject markup.
 */
function customPopup(layer, properties) {
  const root = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = properties._label != null ? String(properties._label) : layer.name;
  root.appendChild(title);
  const meta = document.createElement("div");
  meta.style.cssText = "font-size:10px;color:#94a3b8;margin-bottom:4px";
  meta.textContent = `${layer.name}${properties._time ? ` · ${properties._time}` : ""}`;
  root.appendChild(meta);
  const table = document.createElement("table");
  table.style.cssText = "font-size:11px;border-collapse:collapse";
  Object.entries(properties)
    .filter(([key, value]) => !key.startsWith("_") && value !== null && value !== "")
    .slice(0, 14)
    .forEach(([key, value]) => {
      const row = table.insertRow();
      const k = row.insertCell();
      k.textContent = key;
      k.style.cssText = "color:#64748b;padding-right:8px;vertical-align:top";
      row.insertCell().textContent = String(value).slice(0, 160);
    });
  root.appendChild(table);
  return root;
}

/**
 * Brings the selected vessel into the part of the map NOT covered by the
 * slide-over drawer (right side), zooming in if the view is too wide.
 */
function FollowSelection({ position }) {
  const map = useMap();
  useEffect(() => {
    if (!position) return;
    const size = map.getSize();
    const drawer = size.x >= 640 ? Math.min(448, size.x * 0.6) : 0;
    const margin = 60;
    const zoom = Math.max(map.getZoom(), 9);
    const point = map.project(position, zoom);
    const topLeft = map.project(map.getBounds().getNorthWest(), zoom);
    const visible =
      zoom === map.getZoom() &&
      point.x > topLeft.x + margin &&
      point.x < topLeft.x + size.x - drawer - margin &&
      point.y > topLeft.y + margin &&
      point.y < topLeft.y + size.y - margin;
    if (visible) return;
    // Centre the vessel in the free area: shift the map centre right by half the drawer.
    const center = map.unproject(point.add([drawer / 2, 0]), zoom);
    map.flyTo(center, zoom, { duration: 0.6 });
  }, [map, position]);
  return null;
}

/** Drawing a surveillance zone: each click adds a vertex (double-click zoom is off meanwhile). */
function DrawHandler({ active, onPoint }) {
  const map = useMap();
  useEffect(() => {
    if (!active) return undefined;
    map.doubleClickZoom.disable();
    map.getContainer().style.cursor = "crosshair";
    return () => {
      map.doubleClickZoom.enable();
      map.getContainer().style.cursor = "";
    };
  }, [active, map]);
  useMapEvents({
    click(event) {
      if (active) onPoint?.([event.latlng.lng, event.latlng.lat]);
    },
  });
  return null;
}

/** Right-click anywhere → point dossier (zone, sea state, nearby incidents). */
function ContextMenuHandler({ onContextPoint }) {
  useMapEvents({
    contextmenu(event) {
      onContextPoint?.(event.latlng.lat, event.latlng.lng);
    },
  });
  return null;
}

function Map({
  zee,
  darkVessels,
  aisVessels,
  events,
  scenes,
  optical,
  layers,
  basemap,
  imageryDate,
  selectedKey,
  onSelect,
  onContextPoint,
  fishingLimits,
  aisLive,
  gfwSar,
  effort,
  mpa,
  isobaths = null,
  oceanLayers,
  visual = {},
  customLayers = [],
  customData = {},
  geoSlot = null,
  sentinel2 = null,
  zones = null,
  drawing = null,
  onDrawPoint = null,
}) {
  // Stable click handlers: polling re-renders must never detach a marker listener.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const handlers = useMemo(() => {
    const byKey = {};
    darkVessels.forEach((f) => {
      byKey[f.key] = { click: () => onSelectRef.current(f.key) };
    });
    return byKey;
  }, [darkVessels]);

  const selected = darkVessels.find((f) => f.key === selectedKey);
  const selectedPosition = useMemo(() => latLng(selected), [selected]);
  const base = BASEMAPS[basemap] || BASEMAPS.osm;
  const baseUrl = base.dated ? base.url.replace("{date}", imageryDate) : base.url;
  const zonesKey = (zee?.features || []).map((f) => f.properties?.source).join(",");
  const scenesKey = (scenes?.features || []).map((f) => f.properties?.scene_id).join(",");
  const opticalKey = (optical?.features || []).map((f) => f.properties?.image_id).join(",");

  return (
    <MapContainer center={GUYANE_CENTER} zoom={7} className="h-full w-full" zoomControl scrollWheelZoom>
      <TileLayer
        referrerPolicy={TILE_REFERRER}
        key={baseUrl}
        className={base.className || "visual-tiles"}
        opacity={visual.tileOpacity ?? 1}
        attribution={`${base.attribution} · Limites : <a href="https://www.marineregions.org/">Marine Regions</a>`}
        url={baseUrl}
        maxNativeZoom={base.maxNativeZoom}
        maxZoom={19}
      />
      {visual.oceanLayer && visual.oceanLayer !== "none" && oceanLayers?.[visual.oceanLayer] && (
        <TileLayer
          referrerPolicy={TILE_REFERRER}
          key={`ocean-${visual.oceanLayer}`}
          url={oceanLayers[visual.oceanLayer].url}
          attribution={oceanLayers[visual.oceanLayer].attribution}
          opacity={visual.oceanOpacity ?? 0.6}
          maxNativeZoom={8}
          maxZoom={19}
        />
      )}
      {layers.geostationary && geoSlot && (() => {
        const geo = goesLayer(visual.geoMode || "auto", geoSlot);
        return (
          <TileLayer
            referrerPolicy={TILE_REFERRER}
            key={geo.url}
            url={geo.url}
            attribution={`NASA GIBS / NOAA ${geo.label} · ${geo.time.slice(0, 16).replace("T", " ")} Z`}
            maxNativeZoom={geo.maxNativeZoom}
            maxZoom={19}
            opacity={visual.geoOpacity ?? 0.75}
          />
        );
      })()}
      {layers.nightLights && (
        <TileLayer
          referrerPolicy={TILE_REFERRER}
          key={`dnb-${visual.nightSatellite}-${nightLightsDate(visual.nightSatellite, imageryDate)}`}
          className="night-lights-tiles"
          url={nightLightsUrl(visual.nightSatellite, imageryDate)}
          attribution={OVERLAYS.gibs_dnb.attribution}
          maxNativeZoom={OVERLAYS.gibs_dnb.maxNativeZoom}
          maxZoom={19}
          opacity={visual.nightLightsOpacity ?? OVERLAYS.gibs_dnb.opacity}
        />
      )}

      {customLayers
        .filter((layer) => layer.kind !== "features" && layers[`custom:${layer.id}`])
        .map((layer) => (
          <TileLayer
            key={`custom-${layer.id}-${imageryDate}`}
            url={customTileUrl(layer.id, imageryDate)}
            className={layer.tiles?.blend && layer.tiles.blend !== "normal" ? `blend-${layer.tiles.blend}` : ""}
            attribution={escapeHtml(layer.tiles?.attribution || layer.name)}
            opacity={layer.tiles?.opacity ?? 0.8}
            maxNativeZoom={layer.tiles?.max_native_zoom ?? 18}
            maxZoom={19}
          />
        ))}

      {visual.followSelection !== false && <FollowSelection position={selectedPosition} />}
      <ContextMenuHandler onContextPoint={onContextPoint} />
      <DrawHandler active={Boolean(drawing)} onPoint={onDrawPoint} />
      {layers.drawnZones && zones?.features?.length > 0 && (
        <GeoJSON
          key={`zones-drawn-${zones.features.map((f) => `${f.properties.id}:${f.properties.updated_at}`).join(",")}`}
          data={zones}
          interactive={!drawing}
          style={(feature) => ({
            color: feature.properties.color,
            weight: 2,
            dashArray: "6 4",
            fillColor: feature.properties.color,
            fillOpacity: 0.08,
          })}
          onEachFeature={(feature, layer) => {
            const p = feature.properties;
            layer.bindTooltip(
              `Zone de surveillance « ${p.name} »${p.used_by?.length ? ` · règle(s) : ${p.used_by.join(", ")}` : " · aucune règle"}`,
              { className: "tactical-tip", sticky: true },
            );
          }}
        />
      )}
      {drawing?.length > 0 && (
        <>
          <Polyline positions={[...drawing, ...(drawing.length > 2 ? [drawing[0]] : [])].map(([lon, lat]) => [lat, lon])}
            pathOptions={{ color: "#22d3ee", weight: 2, dashArray: "4 4" }} interactive={false} />
          {drawing.map(([lon, lat], index) => (
            <CircleMarker key={`${lon},${lat},${index}`} center={[lat, lon]} radius={index === 0 ? 6 : 4}
              pathOptions={{ color: "#22d3ee", fillColor: "#070b10", fillOpacity: 1, weight: 2 }} interactive={false} />
          ))}
        </>
      )}

      {layers.sentinel2 &&
        (sentinel2?.features || []).map((feature) => {
          const p = feature.properties;
          const [w, s, e, n] = feature.bbox || [];
          return (
            <TileLayer
              key={`s2-${p.item_id}`}
              url={sentinel2TileUrl(p.item_id)}
              bounds={feature.bbox ? [[s, w], [n, e]] : undefined}
              minZoom={9}
              maxNativeZoom={16}
              maxZoom={19}
              attribution={`Copernicus Sentinel-2 (${formatDate(p.datetime)})`}
            />
          );
        })}
      {layers.sentinel2 && sentinel2?.features?.length > 0 && (
        <GeoJSON
          key={`s2-fp-${sentinel2.features.map((f) => f.properties.item_id).join(",")}`}
          data={sentinel2}
          style={footprintStyle(FOOTPRINTS.s2.color)}
          onEachFeature={(feature, layer) => {
            const p = feature.properties || {};
            layer.bindTooltip(
              `Sentinel-2 ${p.tile} · ${formatDate(p.datetime)} · nuages ${p.cloud_cover} % — zoomez (≥ 9) pour l'image 10 m`,
              { className: "tactical-tip", sticky: true },
            );
          }}
        />
      )}
      {layers.scenes && scenes?.features?.length > 0 && (
        <GeoJSON
          key={`scenes-${scenesKey}`}
          data={scenes}
          style={footprintStyle(FOOTPRINTS.s1.color)}
          onEachFeature={(feature, layer) => {
            const p = feature.properties || {};
            layer.bindTooltip(`Sentinel-1 ${p.mode || ""} · ${formatDate(p.datetime)}`, {
              className: "tactical-tip",
              sticky: true,
            });
          }}
        />
      )}

      {layers.optical && optical?.features?.length > 0 && (
        <GeoJSON
          key={`optical-${opticalKey}`}
          data={optical}
          style={footprintStyle(FOOTPRINTS.optical.color)}
          onEachFeature={(feature, layer) => {
            const p = feature.properties || {};
            layer.bindTooltip(`${p.label} ${p.gsd_m ?? "?"} m · ${formatDate(p.acquired)}`, {
              className: "tactical-tip",
              sticky: true,
            });
          }}
        />
      )}

      {customLayers
        .filter((layer) => layer.kind === "features" && layers[`custom:${layer.id}`] && customData[layer.id]?.data?.features?.length)
        .map((layer) => {
          const data = customData[layer.id].data;
          const color = layer.style?.color || "#22d3ee";
          return (
            <GeoJSON
              key={`custom-${layer.id}-${data.fetched_at}`}
              data={data}
              style={() => ({ color, weight: 2, fillColor: color, fillOpacity: 0.15 })}
              pointToLayer={(_feature, latlng) =>
                L.circleMarker(latlng, { radius: layer.style?.radius || 5, color: "#070b10", weight: 1, fillColor: color, fillOpacity: 0.85 })
              }
              onEachFeature={(feature, leafletLayer) => {
                const p = feature.properties || {};
                const tip = document.createElement("span");
                tip.textContent = p._label != null ? String(p._label) : layer.name;
                leafletLayer.bindTooltip(tip, { className: "tactical-tip", sticky: true });
                leafletLayer.bindPopup(() => customPopup(layer, p), { className: "tactical-popup" });
              }}
            />
          );
        })}

      {layers.zones && zee && (
        // Non-interactive: boundaries must never swallow clicks meant for vessels.
        <GeoJSON key={`zones-${zonesKey}`} data={zee} style={zoneStyle} interactive={false} />
      )}

      {layers.mpa && mpa?.features?.length > 0 && (
        <GeoJSON
          key={`mpa-${mpa.features.length}`}
          data={mpa}
          style={mpaStyle}
          onEachFeature={(feature, layer) => {
            const p = feature.properties || {};
            layer.bindTooltip(
              `${p.name} · ${p.designation || "aire protégée"}${p.marine_km2 ? ` · ${p.marine_km2} km² marins` : ""} (${p.country})`,
              { className: "tactical-tip", sticky: true },
            );
          }}
        />
      )}

      {layers.isobaths && isobaths?.features?.length > 0 && (
        <GeoJSON
          key={`iso-${isobaths.features.length}`}
          data={isobaths}
          style={isobathStyle}
          onEachFeature={(feature, layer) => {
            const depth = feature.properties.depth_m;
            layer.bindTooltip(depth === 200 ? ISOBATH.shelfLabel : `Isobathe ${depth} m`, { className: "tactical-tip", sticky: true });
          }}
        />
      )}

      {layers.limits && fishingLimits?.features?.length > 0 && (
        <GeoJSON key={`limits-${fishingLimits.features.length}`} data={fishingLimits} style={limitStyle} interactive={false} />
      )}

      {layers.effort &&
        (effort?.features || []).map((feature, index) => {
          const position = latLng(feature);
          if (!position) return null;
          const p = feature.properties || {};
          return (
            <CircleMarker
              key={`eff-${index}`}
              center={position}
              radius={Math.min(14, 2 + Math.sqrt(p.hours || 0))}
              interactive={false}
              pathOptions={{ stroke: false, fillColor: p.foreign ? EFFORT_FOREIGN_COLOR : EFFORT_COLOR, fillOpacity: 0.18 }}
            />
          );
        })}

      {layers.tracks &&
        (aisLive?.features || []).map((feature) => {
          const p = feature.properties || {};
          if (!p.track?.length) return null;
          return (
            <Polyline
              key={`trk-${p.mmsi}`}
              positions={p.track.map(([lon, lat]) => [lat, lon])}
              interactive={false}
              pathOptions={{ color: (AIS_CATEGORIES[p.category] || AIS_CATEGORIES.unknown).color, weight: 1.5, opacity: 0.55 }}
            />
          );
        })}

      {layers.gfwSar &&
        (gfwSar?.features || []).map((feature, index) => {
          const position = latLng(feature);
          if (!position) return null;
          const p = feature.properties || {};
          return (
            <Marker key={`sar-${index}`} position={position} icon={sarIcon(p.matched_ais)}>
              <Tooltip className="tactical-tip" direction="top">
                SAR GFW · {p.date} · {p.matched_ais ? `AIS ${p.vessel_name || p.mmsi || "apparié"}${p.flag ? ` (${p.flag})` : ""}` : "sans AIS"}
              </Tooltip>
            </Marker>
          );
        })}

      {layers.events &&
        (events?.features || []).map((feature, index) => {
          const position = latLng(feature);
          if (!position) return null;
          const p = feature.properties || {};
          const style = EVENTS[p.event_type] || { color: "#94a3b8", label: p.event_type };
          return (
            <CircleMarker
              key={p.event_id || `evt-${index}`}
              center={position}
              radius={p.event_type === "gap" ? 6 : 4}
              pathOptions={{ color: style.color, fillColor: style.color, fillOpacity: 0.55, weight: 1 }}
            >
              <Popup className="tactical-popup">
                <strong>{style.label}</strong>
                <br />
                {p.vessel_name || "Navire inconnu"} ({p.flag || "—"}) · MMSI {p.mmsi || "—"}
                <br />
                {formatDate(p.start)} → {formatDate(p.end)}
                {p.watchlist && (
                  <>
                    <br />
                    <strong style={{ color: TARGET.listed.color }}>⚠ Liste {p.watchlist.source} : {p.watchlist.reason || p.watchlist.name}</strong>
                  </>
                )}
                {p.event_type === "port_visit" && (p.port_name || p.anchorage) && (
                  <>
                    <br />
                    Port : {p.port_name || p.anchorage}
                    {p.anchorage && p.port_name && p.anchorage !== p.port_name ? ` (${p.anchorage})` : ""}
                    {p.port_hours != null ? ` · ${Number(p.port_hours).toFixed(0)} h à quai` : ""}
                  </>
                )}
                {p.gap_hours != null && (
                  <>
                    <br />
                    {Number(p.gap_hours).toFixed(0)} h sans AIS
                    {p.intentional_disabling ? " (coupure volontaire probable)" : ""}
                  </>
                )}
              </Popup>
            </CircleMarker>
          );
        })}

      {layers.ais &&
        (aisLive?.features || []).map((feature) => {
          const position = latLng(feature);
          if (!position) return null;
          const p = feature.properties || {};
          const category = AIS_CATEGORIES[p.category] || AIS_CATEGORIES.unknown;
          const course = p.track?.length > 1 ? bearing(p.track[p.track.length - 2], p.track[p.track.length - 1]) : null;
          return (
            <Marker key={`live-${p.mmsi}`} position={position} icon={aisArrow(p.category, course)} zIndexOffset={p.category === "listed" ? 600 : 0}>
              <Popup className="tactical-popup">
                <strong style={{ color: category.color }}>{category.label}</strong>
                <br />
                {p.name || "Nom inconnu"} · MMSI {p.mmsi}
                <br />
                Pavillon : {p.flag || "inconnu"} · {p.fishing ? `pêche${p.gear ? ` (${p.gear})` : ""}` : `type ${p.ship_type ?? "—"}`}
                <br />
                {p.sog_knots != null ? `${p.sog_knots} nd` : "vitesse —"} · vu {formatDate(p.last_seen)} Z
                {p.feed && p.feed !== "aisstream" && ` · ${FEEDS[p.feed]?.label || p.feed}`}
                {p.licence && (
                  <>
                    <br />
                    <span style={{ color: AIS_CATEGORIES.licensed.color }}>
                      Sous licence{p.licence.licence ? ` ${p.licence.licence}` : ""}
                      {p.licence.valid_until ? ` (jusqu'au ${p.licence.valid_until})` : ""}
                    </span>
                  </>
                )}
                {p.watchlist && (
                  <>
                    <br />
                    <strong style={{ color: TARGET.listed.color }}>⚠ Liste {p.watchlist.source} : {p.watchlist.reason || p.watchlist.name}</strong>
                  </>
                )}
                <VesselProfile mmsi={p.mmsi} />
              </Popup>
            </Marker>
          );
        })}

      {layers.ais &&
        !(aisLive?.features || []).length &&
        aisVessels.map((feature) => {
          const position = latLng(feature);
          if (!position) return null;
          const props = feature.properties || {};
          return (
            <Marker key={feature.key} position={position} icon={props.watchlist ? LISTED_AIS_ICON : AIS_ICON} zIndexOffset={props.watchlist ? 500 : 0}>
              <Tooltip className="tactical-tip" direction="top">
                {props.watchlist && `⚠ LISTE ${props.watchlist.source.toUpperCase()} · `}
                AIS {props.name || "—"} · MMSI {props.mmsi || "—"}
                {props.sog_knots != null && ` · ${props.sog_knots} nd`}
                {props.source === "mock" && " · (simulé)"}
              </Tooltip>
            </Marker>
          );
        })}

      {darkVessels.map((feature) => {
        const position = latLng(feature);
        if (!position) return null;
        const props = feature.properties || {};
        const isSelected = feature.key === selectedKey;
        const cls = targetClass(props);
        const color = isSelected ? SELECTED_COLOR : TARGET[cls].color;
        return (
          <Marker
            key={feature.key}
            position={position}
            icon={darkIcon(color, isSelected, props.incident_status === "closed", cls === "priority")}
            zIndexOffset={isSelected ? 1000 : 0}
            eventHandlers={handlers[feature.key]}
            keyboard
          >
            <Tooltip className="tactical-tip" direction="top" offset={[0, -12]}>
              {props.vessel_id || props.alert_id}
              {props.risk_score != null && ` · risque ${props.risk_score}/100`}
            </Tooltip>
          </Marker>
        );
      })}
    </MapContainer>
  );
}

export default memo(Map);
