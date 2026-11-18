import { conditionIcon, driestDeparture, formatTemp, rainChance, rainRisk, readingAt, tripAdvice, upcoming } from './weather.js';
import { PLACE_KINDS, clearHistory, dayLabel, findSavedPlace, getHistory, getPlaces, historyStats, recentTrips, recordTrip, removePlace, removeTrip, savePlace } from './store.js';

(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  // One-time move of saved settings and trips from the app's old name (MapQuest).
  ['mode', 'unit', 'history', 'fuel-price', 'fuel-efficiency', 'favorites'].forEach((key) => {
    const old = localStorage.getItem(`mapquest-${key}`);
    if (old !== null && localStorage.getItem(`maply-${key}`) === null) localStorage.setItem(`maply-${key}`, old);
    localStorage.removeItem(`mapquest-${key}`);
  });
  const state = { mode: localStorage.getItem('maply-mode') || 'car', unit: localStorage.getItem('maply-unit') || 'metric', origin: null, destination: null, route: null, comparison: null, map: null, mapReady: false, pendingRoute: null, markers: [], userMarker: null, satellite: false, busy: false, comparing: false, alternatives: {}, loadingAlternatives: null, departAt: null, weather: null, mapquest: {} };
  const modes = { car: { name: 'Drive', verb: 'Driving', icon: 'car-front' }, bike: { name: 'Cycle', verb: 'Cycling', icon: 'bike' }, foot: { name: 'Walk', verb: 'Walking', icon: 'footprints' }, commute: { name: 'Commute', verb: 'Commuting', icon: 'bus' } };
  // Trips saved before Commute replaced Transit still show the right icon.
  Object.defineProperty(modes, 'transit', { value: modes.commute, enumerable: false });
  const VEHICLE_ICONS = { train: 'train-front', bus: 'bus', jeepney: 'bus-front', van: 'car-front', tricycle: 'bike', ferry: 'ship' };
  if (state.mode === 'transit') state.mode = 'commute';
  if (!modes[state.mode]) state.mode = 'car';
  const ROUTE_BLUE = '#2f6db5';
  const ALTERNATIVE_GREY = '#9aa7b8';
  // Maply covers Metro Manila only; keep the map near it (with some room around the edges).
  const METRO_MANILA = { minLat: 14.34, maxLat: 14.79, minLng: 120.90, maxLng: 121.14 };
  const METRO_MANILA_VIEW = [[120.70, 14.20], [121.35, 14.95]];
  const inMetroManila = ([lng, lat]) => lat >= METRO_MANILA.minLat && lat <= METRO_MANILA.maxLat && lng >= METRO_MANILA.minLng && lng <= METRO_MANILA.maxLng;
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const icons = () => window.lucide?.createIcons();
  const setStatus = (message = '', type = '') => { const el = $('#statusMessage'); el.textContent = message; el.className = `status-message ${type}`; };
  const isMobile = () => window.innerWidth <= 760;

  function setBusy(busy) {
    state.busy = busy;
    const button = $('#findRoute');
    button.disabled = busy;
    button.innerHTML = busy ? '<span class="loading-spinner"></span><span>Finding routes…</span>' : '<span>Find routes</span>';
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Something went wrong. Try again.');
    return payload;
  }
  const tripBody = () => JSON.stringify({ origin: state.origin, destination: state.destination });

  /* ---------- Map ---------- */

  function initializeMap() {
    const token = document.body.dataset.mapboxToken?.trim();
    if (!token) return;
    mapboxgl.accessToken = token;
    state.map = new mapboxgl.Map({ container: 'map', style: 'mapbox://styles/mapbox/standard', center: [121.02, 14.58], zoom: 11, minZoom: 9.5, maxBounds: METRO_MANILA_VIEW, attributionControl: true });
    state.map.addControl(new mapboxgl.NavigationControl({ showCompass: true }), 'bottom-right');
    state.map.on('load', () => {
      state.mapReady = true;
      state.map.resize();
      if (state.pendingRoute) { const [route, origin, destination] = state.pendingRoute; state.pendingRoute = null; drawRoute(route, origin, destination); }
    });
    // Tap a grey route on the map to switch to it.
    state.map.on('click', 'alternative-routes', (event) => { const index = event.features?.[0]?.properties?.index; if (index !== undefined) selectAlternative(Number(index)); });
    state.map.on('mouseenter', 'alternative-routes', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'alternative-routes', () => { state.map.getCanvas().style.cursor = ''; });
    state.map.on('error', (event) => {
      if (event.error?.status === 401 || event.error?.status === 403) showToast('Mapbox rejected this token. Check that it is valid and allowed for localhost.');
    });
    window.addEventListener('resize', () => state.map?.resize());
  }

  function clearMapRoute() {
    if (!state.map || !state.mapReady) return;
    ['planned-route', 'planned-route-casing', 'alternative-routes', 'transit-walk', 'transit-ride'].forEach((id) => { if (state.map.getLayer(id)) state.map.removeLayer(id); });
    ['planned-route', 'alternative-routes', 'transit-legs'].forEach((id) => { if (state.map.getSource(id)) state.map.removeSource(id); });
    state.markers.forEach((marker) => marker.remove());
    state.markers = [];
  }

  function makeMarker(destination = false) {
    const element = document.createElement('div');
    element.className = `route-marker${destination ? ' destination' : ''}`;
    element.innerHTML = '<span class="route-marker-pin"></span>';
    return element;
  }

  function drawRoute(route, origin, destination) {
    if (!state.map || !state.mapReady) { state.pendingRoute = [route, origin, destination]; return; }
    clearMapRoute();
    const coordinates = route.geometry.map(([lat, lng]) => [lng, lat]);
    const dashed = state.mode === 'foot';
    // Other routes for this mode are drawn first, in grey, so the chosen route sits on top.
    const others = (state.alternatives[state.mode] || []).map((alt, index) => ({ alt, index })).filter(({ alt }) => !sameRoute(alt, route));
    if (others.length) {
      state.map.addSource('alternative-routes', { type: 'geojson', data: { type: 'FeatureCollection', features: others.map(({ alt, index }) => ({ type: 'Feature', properties: { index }, geometry: { type: 'LineString', coordinates: alt.geometry.map(([lat, lng]) => [lng, lat]) } })) } });
      state.map.addLayer({ id: 'alternative-routes', type: 'line', source: 'alternative-routes', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': ALTERNATIVE_GREY, 'line-width': 6, 'line-opacity': 0.85 } });
      others.forEach(({ alt }) => alt.geometry.forEach(([lat, lng]) => coordinates.push([lng, lat])));
    }
    state.map.addSource('planned-route', { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: route.geometry.map(([lat, lng]) => [lng, lat]) } } });
    state.map.addLayer({ id: 'planned-route-casing', type: 'line', source: 'planned-route', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': '#ffffff', 'line-width': 11 } });
    if (route.legs) {
      // Transit: each ride in its line's colour, walks dotted.
      const features = route.legs.filter((leg) => leg.geometry.length > 1).map((leg) => ({ type: 'Feature', properties: { kind: leg.type, color: leg.color || ROUTE_BLUE }, geometry: { type: 'LineString', coordinates: leg.geometry.map(([lat, lng]) => [lng, lat]) } }));
      state.map.addSource('transit-legs', { type: 'geojson', data: { type: 'FeatureCollection', features } });
      state.map.addLayer({ id: 'transit-walk', type: 'line', source: 'transit-legs', filter: ['==', ['get', 'kind'], 'walk'], layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': '#5b6878', 'line-width': 5, 'line-dasharray': [0.1, 1.8] } });
      state.map.addLayer({ id: 'transit-ride', type: 'line', source: 'transit-legs', filter: ['==', ['get', 'kind'], 'transit'], layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': ['get', 'color'], 'line-width': 7 } });
    } else {
      state.map.addLayer({ id: 'planned-route', type: 'line', source: 'planned-route', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': ROUTE_BLUE, 'line-width': 7, ...(dashed ? { 'line-dasharray': [0.1, 1.8] } : {}) } });
    }
    const start = new mapboxgl.Marker({ element: makeMarker(), anchor: 'center' }).setLngLat([origin.lng, origin.lat]).setPopup(new mapboxgl.Popup({ offset: 16 }).setHTML(`<strong>Start</strong><br>${escapeHtml(origin.name)}`)).addTo(state.map);
    const end = new mapboxgl.Marker({ element: makeMarker(true), anchor: 'center' }).setLngLat([destination.lng, destination.lat]).setPopup(new mapboxgl.Popup({ offset: 16 }).setHTML(`<strong>Destination</strong><br>${escapeHtml(destination.name)}`)).addTo(state.map);
    state.markers.push(start, end);
    const bounds = new mapboxgl.LngLatBounds(); coordinates.forEach((coordinate) => bounds.extend(coordinate));
    state.map.fitBounds(bounds, { padding: { top: 80, bottom: isMobile() ? 120 : 60, left: 60, right: 80 }, maxZoom: 15, duration: 600 });
  }

  /* ---------- Formatting ---------- */

  const formatDistance = (meters, digits = 1) => state.unit === 'imperial' ? `${(meters / 1609.344).toFixed(digits)} mi` : `${(meters / 1000).toFixed(digits)} km`;
  const formatStepDistance = (meters) => state.unit === 'imperial' ? (meters < 160.934 ? `${Math.round(meters * 3.28084)} ft` : `${(meters / 1609.344).toFixed(1)} mi`) : (meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`);
  function durationParts(milliseconds) {
    const minutes = Math.max(1, Math.round(milliseconds / 60000));
    if (minutes < 60) return { value: String(minutes), unit: 'min' };
    const hours = Math.floor(minutes / 60); const rest = minutes % 60;
    return { value: rest ? `${hours}h ${String(rest).padStart(2, '0')}` : `${hours}h`, unit: rest ? 'hr min' : 'hr' };
  }
  const clock = (time) => new Date(time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  // Departure time: "now" unless the user picked a time in the Leave menu.
  const departTime = () => state.departAt ?? Date.now();
  const isToday = (time) => new Date(time).toDateString() === new Date().toDateString();
  const whenText = (time) => (isToday(time) ? clock(time) : `${new Date(time).toLocaleDateString([], { weekday: 'short' })} ${clock(time)}`);
  const arrivalTime = (milliseconds) => whenText(departTime() + milliseconds);
  function fuelCost(distanceMeters) {
    const price = Number($('#fuelPrice').value), efficiency = Number($('#fuelEfficiency').value);
    if (!(efficiency > 0) || !(price >= 0)) return null;
    const liters = distanceMeters / 1000 / efficiency;
    return { liters, cost: liters * price };
  }
  const sameRoute = (a, b) => !!a && !!b && a.distance_m === b.distance_m && a.duration_ms === b.duration_ms;
  const peso = (amount) => `₱${amount.toLocaleString('en-PH', { maximumFractionDigits: 0 })}`;

  /* ---------- Rendering ---------- */

  function renderSummary() {
    const route = state.route; if (!route) return;
    const time = durationParts(route.duration_ms);
    $('#routeSummary').classList.remove('hidden');
    $('#routeSummary').innerHTML = `<div class="journey-head"><div class="journey-time"><strong>${time.value}</strong><span>${time.unit}</span></div><div class="journey-arrive">${state.departAt ? `Leave ${whenText(state.departAt)}, arrive` : 'Arrive around'}<strong>${route.arrive_time ? whenText(route.arrive_time) : arrivalTime(route.duration_ms)}</strong></div></div><div class="journey-meta"><span class="mode-chip"><i data-lucide="${modes[state.mode].icon}"></i>${modes[state.mode].verb}</span><span>${route.legs ? transitSummary(route) : formatDistance(route.distance_m)}</span></div>${route.legs ? `<div class="line-chips">${lineChips(route)}</div>${transitNotes(route)}` : ''}`;
    icons();
  }

  // Start or destination row in the step list, with a star to save the place.
  function timelineStop(place, end = false) {
    const li = document.createElement('li'); li.className = `timeline-stop${end ? ' end' : ''}`;
    const saved = findSavedPlace(place);
    li.innerHTML = `<span class="timeline-node"></span><span class="timeline-place">${saved ? `<span class="saved-tag">${escapeHtml(saved.label)}</span>` : ''}${escapeHtml(place.name)}</span>`;
    const star = document.createElement('button'); star.type = 'button'; star.className = `star-button${saved ? ' saved' : ''}`;
    star.setAttribute('aria-label', saved ? `Edit saved place ${saved.label}` : `Save ${place.name}`);
    star.title = saved ? 'Edit saved place' : 'Save this place';
    star.innerHTML = '<i data-lucide="star"></i>';
    star.addEventListener('click', () => openPlaceDialog(place, saved || null));
    li.appendChild(star); return li;
  }

  /* ---------- Transit details ---------- */

  const fareText = (fare) => (fare ? `${fare.approximate ? 'about ' : ''}₱${Number(fare.amount).toLocaleString('en-PH', { maximumFractionDigits: 2 })} fare` : '');
  // Only real warnings are shown (e.g. trains not running); everything else stays out of the way.
  const transitNotes = (route) => [route.notice ? `<p class="transit-note warn"><i data-lucide="triangle-alert"></i>${escapeHtml(route.notice)}</p>` : '',
    route.source === 'rail-estimate' ? '<p class="transit-note"><i data-lucide="info"></i>Trains only. Times and fares are estimates.</p>' : '',
    sourceLinks(route)].join('');
  // One link per website, e.g. "Sources: moovitapp.com, lrta.gov.ph".
  const sourceLinks = (route) => {
    const byHost = new Map();
    (route.sources || []).forEach((source) => { try { const host = new URL(source.url).hostname.replace(/^www\./, ''); if (!byHost.has(host)) byHost.set(host, source.url); } catch { /* skip bad links */ } });
    return byHost.size ? `<p class="source-links">Sources: ${[...byHost].map(([host, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(host)}</a>`).join(', ')}</p>` : '';
  };
  const transitSummary = (route) => [fareText(route.fare), route.transfers ? `${route.transfers} transfer${route.transfers === 1 ? '' : 's'}` : 'No transfers', `${formatStepDistance(route.walk_m || 0)} walking`].filter(Boolean).join(', ');
  // Coloured chips for each ride, e.g. [MRT-3] > [LRT-1].
  const lineChips = (route) => route.legs.filter((leg) => leg.type === 'transit').map((leg) => `<span class="line-chip" style="${leg.color ? `background:${escapeHtml(leg.color)};color:${escapeHtml(leg.text_color || '#fff')}` : ''}"><i data-lucide="${VEHICLE_ICONS[leg.vehicle] || 'bus'}"></i>${escapeHtml(leg.line)}</span>`).join('<i data-lucide="chevron-right" class="chip-sep"></i>');

  function renderTransitSteps(list) {
    const legs = state.route.legs;
    $('#instructionCount').textContent = `${legs.filter((leg) => leg.type === 'transit').length} ride${legs.filter((leg) => leg.type === 'transit').length === 1 ? '' : 's'}`;
    legs.forEach((leg, index) => {
      const item = document.createElement('li'); item.className = `direction-item transit-leg ${leg.type}`; item.tabIndex = 0;
      const minutes = Math.max(1, Math.round(leg.duration_ms / 60000));
      if (leg.type === 'walk') {
        const next = legs[index + 1]; const target = next?.from_stop ? `to ${next.from_stop}` : `to ${state.destination.name}`;
        item.innerHTML = `<span class="turn-icon"><i data-lucide="footprints"></i></span><span class="direction-text">Walk ${escapeHtml(target)}<small>${minutes} min</small></span><span class="direction-distance">${formatStepDistance(leg.distance_m)}</span>`;
      } else {
        const style = leg.color ? ` style="background:${escapeHtml(leg.color)};color:${escapeHtml(leg.text_color || '#fff')}"` : '';
        const times = leg.depart_time ? `${clock(leg.depart_time)} to ${clock(leg.arrive_time)}` : `${minutes} min`;
        item.innerHTML = `<span class="turn-icon ride"${style}><i data-lucide="${VEHICLE_ICONS[leg.vehicle] || 'bus'}"></i></span><span class="direction-text"><strong>${escapeHtml(leg.vehicle_name || 'Ride')} ${escapeHtml(leg.line)}</strong>${leg.headsign ? ` toward ${escapeHtml(leg.headsign)}` : ''}<small>${leg.from_stop ? `Board at ${escapeHtml(leg.from_stop)}, get off at ${escapeHtml(leg.to_stop)}` : ''}${leg.stops ? ` (${leg.stops} stop${leg.stops === 1 ? '' : 's'})` : ''}</small><small>${times}${leg.fare ? `, about ₱${Math.round(leg.fare)}` : ''}</small>${leg.note ? `<small class="leg-note">${escapeHtml(leg.note)}</small>` : ''}</span><span class="direction-distance">${minutes} min</span>`;
      }
      const focusLeg = () => { const point = leg.geometry[0]; if (point && state.map) { state.map.flyTo({ center: [point[1], point[0]], zoom: Math.max(state.map.getZoom(), 15) }); if (isMobile()) $('#sidebar').classList.add('collapsed'); } };
      item.addEventListener('click', focusLeg);
      item.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); focusLeg(); } });
      list.appendChild(item);
    });
  }

  function renderDirections() {
    const list = $('#directionsList'); list.innerHTML = '';
    if (state.route.legs) {
      list.appendChild(timelineStop(state.origin));
      renderTransitSteps(list);
      list.appendChild(timelineStop(state.destination, true));
      $('#directionsSection').classList.remove('hidden'); icons();
      return;
    }
    const steps = state.route.instructions.filter((step) => step.text && step.text.toLowerCase() !== 'finish');
    $('#instructionCount').textContent = steps.length ? `${steps.length} steps` : '';
    list.appendChild(timelineStop(state.origin));
    if (!steps.length) { const li = document.createElement('li'); li.className = 'direction-item'; li.innerHTML = '<span></span><span class="direction-text">No turn-by-turn instructions came back for this route.</span><span></span>'; list.appendChild(li); }
    steps.forEach((step) => {
      const item = document.createElement('li'); item.className = 'direction-item'; item.tabIndex = 0;
      const signIcon = step.sign === -2 ? 'corner-up-left' : step.sign === -1 ? 'corner-down-left' : step.sign === 2 ? 'corner-up-right' : step.sign === 1 ? 'corner-down-right' : step.sign === 4 ? 'flag' : step.sign === 6 ? 'rotate-cw' : 'arrow-up';
      item.innerHTML = `<span class="turn-icon"><i data-lucide="${signIcon}"></i></span><span class="direction-text">${escapeHtml(step.text)}</span><span class="direction-distance">${formatStepDistance(step.distance)}</span>`;
      const focusStep = () => { const index = state.route.instructions.indexOf(step); const point = state.route.geometry[Math.min(index * 2, state.route.geometry.length - 1)]; if (point && state.map) { state.map.flyTo({ center: [point[1], point[0]], zoom: Math.max(state.map.getZoom(), 15) }); if (isMobile()) $('#sidebar').classList.add('collapsed'); } };
      item.addEventListener('click', focusStep);
      item.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); focusStep(); } });
      list.appendChild(item);
    });
    list.appendChild(timelineStop(state.destination, true));
    $('#directionsSection').classList.remove('hidden');
    icons();
  }

  function renderFuel() {
    if (!state.route || state.mode !== 'car') { $('#fuelSection').classList.add('hidden'); return; }
    $('#fuelSection').classList.remove('hidden');
    const fuel = fuelCost(state.route.distance_m);
    $('#fuelResult').innerHTML = fuel ? `<span>About ${fuel.liters.toFixed(1)} L of fuel</span><strong>${peso(fuel.cost)}</strong>` : '<span>Enter a valid fuel price and efficiency.</span>';
  }

  function renderComparisonLoading() {
    $('#comparisonSection').classList.remove('hidden');
    $('#comparisonList').innerHTML = '<div class="option-skeleton"></div>'.repeat(4);
  }

  function renderComparison() {
    if (!state.comparison) return;
    const list = $('#comparisonList'); list.innerHTML = '';
    const available = state.comparison.filter((item) => item.available);
    const fastest = available.length > 1 ? available.reduce((best, item) => (item.duration_ms < best.duration_ms ? item : best)).mode : null;
    const ordered = [...available].sort((a, b) => a.duration_ms - b.duration_ms).concat(state.comparison.filter((item) => !item.available));
    ordered.forEach((item) => {
      const mode = modes[item.mode];
      const button = document.createElement('button'); button.type = 'button'; button.dataset.mode = item.mode;
      button.className = `option${item.mode === state.mode ? ' selected' : ''}`; button.disabled = !item.available;
      button.setAttribute('aria-pressed', String(item.mode === state.mode));
      let meta = '';
      if (item.loading) {
        meta = '<span class="option-meta searching"><span class="mini-spinner"></span>Finding jeep, bus and train routes…</span>';
      } else if (item.available && item.mode === 'commute') {
        meta = `<span class="option-meta">${[item.route?.label, fareText(item.route?.fare)].filter(Boolean).map(escapeHtml).join(', ')}</span>`;
      } else if (item.available) {
        const fuel = item.mode === 'car' ? fuelCost(item.distance_m) : null;
        meta = `<span class="option-meta">${formatDistance(item.distance_m)}${fuel ? `, about ${peso(fuel.cost)} fuel` : ''}</span>`;
      } else {
        meta = `<span class="option-error">${escapeHtml(item.error || 'Not available for this trip')}</span>`;
      }
      const time = item.available ? durationParts(item.duration_ms) : null;
      button.innerHTML = `<span class="option-icon"><i data-lucide="${mode.icon}"></i></span><span class="option-body"><span class="option-name">${mode.name}${item.mode === fastest ? '<span class="option-tag">Fastest</span>' : ''}${item.available ? rainTag(item.mode) : ''}</span>${meta}</span>${time ? `<span class="option-time"><strong>${time.value}</strong><small>${time.unit}</small></span>` : ''}`;
      button.addEventListener('click', () => { state.waitingForCommute = false; if (item.mode === state.mode && state.route) return; state.mode = item.mode; localStorage.setItem('maply-mode', item.mode); markSelectedOption(); runRoute(); });
      list.appendChild(button);
    });
    icons();
  }

  /* ---------- Weather planning ---------- */

  // Weather is fetched for the destination (Metro Manila is small enough that one
  // forecast covers the trip) and reused until the destination changes.
  async function loadWeather() {
    const place = state.destination; if (!place) return;
    const key = `${place.lat.toFixed(2)},${place.lng.toFixed(2)}`;
    if (state.weather?.key === key && (state.weather.data || state.weather.loading)) { renderWeather(); return; }
    state.weather = { key, loading: true }; renderWeather();
    try {
      const data = await api(`/api/weather?lat=${place.lat}&lng=${place.lng}`);
      if (state.weather?.key !== key) return;
      state.weather = { key, data };
    } catch (error) {
      if (state.weather?.key !== key) return;
      state.weather = { key, error: error.message };
    }
    renderWeather();
    if (state.comparison) renderComparison();
  }

  function weatherTile(label, reading) {
    if (!reading) return `<div class="weather-tile"><span class="weather-label">${label}</span><span class="empty-note">No forecast yet.</span></div>`;
    return `<div class="weather-tile"><span class="weather-label">${label}</span><span class="weather-main"><i data-lucide="${conditionIcon(reading)}"></i><strong>${formatTemp(reading.temp_c, state.unit)}</strong></span><span class="weather-desc">${escapeHtml(reading.description || reading.condition)}</span><span class="weather-rain"><i data-lucide="umbrella"></i>${rainChance(reading)} chance of rain</span></div>`;
  }

  function renderWeather() {
    const section = $('#weatherSection'); const body = $('#weatherBody');
    if (!state.route || !state.weather) { section.classList.add('hidden'); return; }
    section.classList.remove('hidden');
    $('#weatherPlace').textContent = state.weather.data?.place || '';
    if (state.weather.loading) { body.innerHTML = '<div class="option-skeleton"></div>'; return; }
    if (state.weather.error) { body.innerHTML = `<p class="empty-note">${escapeHtml(state.weather.error)}</p>`; return; }
    const data = state.weather.data;
    const depart = departTime(); const arrive = depart + state.route.duration_ms;
    const leaving = readingAt(data, depart); const arriving = readingAt(data, arrive);
    const advice = tripAdvice({ weather: data, mode: state.mode, depart, arrive });
    const better = driestDeparture({ weather: data, from: depart, durationMs: state.route.duration_ms });
    // Each step shows its hour; the first step of each day also names the day.
    let lastDay = new Date(depart).toDateString();
    const strip = upcoming(data, depart).map((slot) => { const day = new Date(slot.time).toDateString(); const dayLabel = day !== lastDay ? new Date(slot.time).toLocaleDateString([], { weekday: 'short' }) : ''; lastDay = day; return `<li><span>${new Date(slot.time).toLocaleTimeString([], { hour: 'numeric' })}</span><em>${dayLabel}</em><i data-lucide="${conditionIcon(slot)}"></i><strong>${formatTemp(slot.temp_c, state.unit)}</strong><small>${rainChance(slot)}</small></li>`; }).join('');
    body.innerHTML = `<div class="weather-pair">${weatherTile(state.departAt ? `Leaving ${whenText(depart)}` : 'Leaving now', leaving)}${weatherTile(`Arriving ${whenText(arrive)}`, arriving)}</div>`
      + (advice.length ? `<ul class="weather-advice">${advice.map((item) => `<li class="${item.level}"><i data-lucide="${item.level === 'warn' ? 'triangle-alert' : 'info'}"></i><span>${escapeHtml(item.text)}</span></li>`).join('')}</ul>` : '')
      + (better ? `<div class="weather-better"><span>Drier at <strong>${escapeHtml(whenText(better.time))}</strong> (${Math.round(better.pop * 100)}% rain)</span><button type="button" class="chip-button" id="leaveLaterButton">Leave then</button></div>` : '')
      + (strip ? `<ul class="weather-strip" aria-label="Forecast every 3 hours">${strip}</ul>` : '');
    $('#leaveLaterButton')?.addEventListener('click', () => setDeparture(better.time));
    icons();
  }

  // Rain tag on Walk/Cycle options when rain is likely at departure.
  const rainTag = (mode) => {
    if (mode === 'car' || !state.weather?.data) return '';
    const risk = rainRisk(readingAt(state.weather.data, departTime()));
    return risk === 'heavy' || risk === 'likely' ? `<span class="option-tag rain"><i data-lucide="umbrella"></i>${risk === 'heavy' ? 'Heavy rain' : 'Rain likely'}</span>` : '';
  };

  // menuValue keeps the Leave menu on the option the user picked; other times show "Pick a time".
  function setDeparture(time, menuValue = null) {
    state.departAt = time;
    const select = $('#departSelect'); const custom = $('#departCustom');
    if (time === null) { select.value = 'now'; custom.hidden = true; }
    else if (menuValue) { select.value = menuValue; custom.hidden = true; }
    else { select.value = 'custom'; custom.hidden = false; custom.value = toLocalInput(time); }
    if (state.route) { renderSummary(); renderWeather(); }
    if (state.comparison) renderComparison();
    updateTripBar();
    refreshCommuteForDeparture();
  }

  // Commute options depend on rush hour, so a new departure time asks again (the server
  // caches rush-hour and off-peak answers separately). Other modes do not change.
  let commuteTimer;
  function refreshCommuteForDeparture() {
    if (!state.comparison || !state.origin || !state.destination) return;
    clearTimeout(commuteTimer);
    commuteTimer = setTimeout(async () => {
      const item = await loadCommute();
      if (state.mode === 'commute') {
        if (item.available) showRoute(item.route);
        else setStatus(item.error);
      }
    }, 400);
  }

  const toLocalInput = (time) => { const d = new Date(time); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };

  function departureFromMenu(value) {
    if (value === 'now') return null;
    const now = new Date();
    if (value.startsWith('tomorrow-')) { const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, Number(value.split('-')[1]), 0); return d.getTime(); }
    // "In N hours", rounded to the next quarter hour.
    const d = new Date(now.getTime() + Number(value) * 3600000); d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0); return d.getTime();
  }

  /* ---------- Alternative routes ---------- */

  // Fetch other routes for the current mode once per search (2 GraphHopper credits), then reuse them.
  async function loadAlternatives() {
    const mode = state.mode; const key = `${mode}:${state.origin.lat},${state.origin.lng}>${state.destination.lat},${state.destination.lng}`;
    // Commute alternatives come with the commute search; the rest are fetched from GraphHopper.
    if (mode === 'commute' || state.alternatives[mode] || state.loadingAlternatives === key) { renderAlternatives(); return; }
    state.loadingAlternatives = key; renderAlternatives();
    try {
      const result = await api('/api/alternatives', { method: 'POST', body: JSON.stringify({ origin: state.origin, destination: state.destination, mode }) });
      if (state.loadingAlternatives !== key) return; // A newer search replaced this one.
      state.alternatives[mode] = result.routes;
      state.alternativesError = null;
    } catch (error) {
      if (state.loadingAlternatives !== key) return;
      state.alternativesError = error.message;
    } finally {
      if (state.loadingAlternatives === key) state.loadingAlternatives = null;
    }
    if (state.mode === mode) { renderAlternatives(); if (state.route) drawRoute(state.route, state.origin, state.destination); }
  }

  function renderAlternatives() {
    const section = $('#alternativesSection'); const list = $('#alternativesList');
    if (!state.route) { section.classList.add('hidden'); return; }
    section.classList.remove('hidden');
    const routes = state.alternatives[state.mode];
    if (!routes) {
      $('#alternativesCount').textContent = '';
      list.innerHTML = state.loadingAlternatives ? '<div class="option-skeleton"></div><div class="option-skeleton"></div>' : `<p class="empty-note">${escapeHtml(state.alternativesError || 'Other routes are not available right now.')}</p>`;
      return;
    }
    $('#alternativesCount').textContent = `${routes.length} route${routes.length === 1 ? '' : 's'}`;
    if (routes.length < 2) { list.innerHTML = '<p class="empty-note">No other good routes.</p>'; return; }
    list.innerHTML = '';
    const fastest = routes[0].duration_ms;
    routes.forEach((route, index) => {
      const selected = sameRoute(route, state.route);
      const extra = Math.round((route.duration_ms - fastest) / 60000);
      const time = durationParts(route.duration_ms);
      const button = document.createElement('button'); button.type = 'button';
      button.className = `option alternative${selected ? ' selected' : ''}`; button.setAttribute('aria-pressed', String(selected));
      button.innerHTML = `<span class="route-swatch${selected ? ' active' : ''}" aria-hidden="true"></span><span class="option-body"><span class="option-name">${escapeHtml(route.label || `Route ${index + 1}`)}${index === 0 ? '<span class="option-tag">Fastest</span>' : ''}</span><span class="option-meta">${formatDistance(route.distance_m)}${extra > 0 ? `, ${extra} min slower` : ''}</span></span><span class="option-time"><strong>${time.value}</strong><small>${time.unit}</small></span>`;
      button.addEventListener('click', () => selectAlternative(index));
      list.appendChild(button);
    });
  }

  function selectAlternative(index) {
    const route = state.alternatives[state.mode]?.[index];
    if (!route || sameRoute(route, state.route)) return;
    showRoute(route);
  }

  function markSelectedOption() {
    $$('.option').forEach((option) => { const active = option.dataset.mode === state.mode; option.classList.toggle('selected', active); option.setAttribute('aria-pressed', String(active)); });
  }

  /* ---------- Saved places ---------- */

  const kindIcon = (item) => PLACE_KINDS[item.kind]?.icon || 'star';

  function renderPlaces() {
    const list = $('#placesList'); list.innerHTML = '';
    const places = getPlaces();
    if (!places.length) {
      list.innerHTML = '<p class="empty-note">Tap the star next to a place to save it.</p>';
      return;
    }
    places.forEach((item) => {
      const row = document.createElement('div'); row.className = 'saved-item';
      const go = document.createElement('button'); go.type = 'button'; go.title = `Directions to ${item.label}`;
      go.innerHTML = `<span class="saved-icon kind-${item.kind}"><i data-lucide="${kindIcon(item)}"></i></span><span class="saved-route"><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.place.name)}</small></span>`;
      go.addEventListener('click', () => useSavedPlace(item, 'destination'));
      const from = document.createElement('button'); from.type = 'button'; from.className = 'chip-button'; from.textContent = 'From here';
      from.setAttribute('aria-label', `Start from ${item.label}`);
      from.addEventListener('click', () => useSavedPlace(item, 'origin'));
      const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'delete-saved'; edit.setAttribute('aria-label', `Edit ${item.label}`); edit.innerHTML = '<i data-lucide="pencil"></i>';
      edit.addEventListener('click', () => openPlaceDialog(item.place, item));
      row.append(go, from, edit); list.appendChild(row);
    });
    icons();
  }

  // Fill one end of the trip with a saved place; plan the route when both ends are set.
  function useSavedPlace(item, which) {
    setPlace(which, { ...item.place });
    setStatus();
    const other = which === 'origin' ? 'destination' : 'origin';
    if (state[other]) findRoutes();
    else { $(`#${other}Input`).focus(); if (isMobile()) $('#sidebar').classList.remove('collapsed'); }
  }

  let editingPlace = null;
  function openPlaceDialog(place, existing = null) {
    editingPlace = { place, existing };
    const form = $('#placeForm');
    $('#placeDialogTitle').textContent = existing ? 'Edit saved place' : 'Save place';
    $('#placeDialogAddress').textContent = place.name;
    $('#placeLabel').value = existing?.label || '';
    form.kind.value = existing?.kind || 'other';
    $('#removePlaceButton').hidden = !existing;
    $('#placeDialog').showModal();
    $('#placeLabel').focus();
  }

  function savePlaceFromDialog() {
    if (!editingPlace) return;
    const kind = $('#placeForm').kind.value;
    const label = $('#placeLabel').value.trim() || PLACE_KINDS[kind].label;
    const saved = savePlace({ id: editingPlace.existing?.id, label, kind, place: editingPlace.place });
    showToast(`Saved ${saved.label}.`);
    editingPlace = null;
    renderPlaces(); if (state.route) renderDirections();
  }

  function savedPlaceSuggestions(which, list) {
    const places = getPlaces();
    if (!places.length) return false;
    list.innerHTML = '<div class="suggestion-heading">Saved places</div>';
    places.slice(0, 6).forEach((item) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'suggestion'; button.setAttribute('role', 'option');
      button.innerHTML = `<i data-lucide="${kindIcon(item)}"></i><span>${escapeHtml(item.label)}<small>${escapeHtml(item.place.name)}</small></span>`;
      button.addEventListener('click', () => { list.classList.remove('open'); useSavedPlace(item, which); });
      list.appendChild(button);
    });
    list.classList.add('open'); icons();
    return true;
  }

  /* ---------- Trip history ---------- */

  function historyRow(trip, { showTime = false } = {}) {
    const row = document.createElement('div'); row.className = 'saved-item';
    const label = document.createElement('button'); label.type = 'button'; label.title = 'Plan this route again';
    const details = [showTime ? new Date(trip.createdAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : `From ${trip.origin.name}`];
    if (showTime) details.push(`from ${trip.origin.name}`);
    const stats = trip.distance_m ? `<span class="trip-stats">${formatDistance(trip.distance_m)}${trip.duration_ms ? `, ${durationParts(trip.duration_ms).value} ${durationParts(trip.duration_ms).unit}` : ''}</span>` : '';
    label.innerHTML = `<span class="saved-icon"><i data-lucide="${modes[trip.mode]?.icon || 'clock'}"></i></span><span class="saved-route"><strong>${escapeHtml(trip.destination.name)}</strong><small>${escapeHtml(details.join(', '))}</small>${stats}</span>`;
    label.addEventListener('click', () => {
      $('#historyDialog').close();
      setPlace('origin', trip.origin); setPlace('destination', trip.destination);
      const mode = trip.mode === 'transit' ? 'commute' : trip.mode; // Transit became Commute.
      if (modes[mode]) state.mode = mode;
      findRoutes();
    });
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'delete-saved'; remove.setAttribute('aria-label', `Remove trip to ${trip.destination.name}`); remove.innerHTML = '<i data-lucide="x"></i>';
    remove.addEventListener('click', () => { removeTrip(trip.id); renderSaved(); if ($('#historyDialog').open) renderHistoryDialog(); });
    row.append(label, remove); return row;
  }

  function renderSaved() {
    localStorage.removeItem('maply-favorites');
    const trips = recentTrips(3); const total = getHistory().length;
    $('#historyList').innerHTML = trips.length ? '' : '<p class="empty-note">Routes you plan will be listed here.</p>';
    trips.forEach((trip) => $('#historyList').appendChild(historyRow(trip)));
    $('#openHistoryButton').textContent = total ? `See all (${total})` : 'See all';
    $('#openHistoryButton').disabled = !total;
    icons();
  }

  function renderHistoryDialog() {
    const query = $('#historySearch').value.trim().toLowerCase();
    const all = getHistory();
    const trips = query ? all.filter((trip) => `${trip.origin.name} ${trip.destination.name}`.toLowerCase().includes(query)) : all;
    const week = historyStats(all);
    $('#historyStats').textContent = week.trips ? `This week: ${week.trips} trip${week.trips === 1 ? '' : 's'}${week.distance_m ? `, ${formatDistance(week.distance_m)} in total` : ''}.` : 'No trips in the last 7 days.';
    const groups = $('#historyGroups'); groups.innerHTML = '';
    if (!trips.length) { groups.innerHTML = `<p class="empty-note">${query ? 'No trips match that search.' : 'Routes you plan will be listed here.'}</p>`; }
    let current = null; let list = null;
    trips.forEach((trip) => {
      const day = dayLabel(trip.createdAt || Date.now());
      if (day !== current) {
        current = day;
        const heading = document.createElement('h3'); heading.className = 'history-day'; heading.textContent = day;
        list = document.createElement('div'); list.className = 'saved-list';
        groups.append(heading, list);
      }
      list.appendChild(historyRow(trip, { showTime: true }));
    });
    $('#clearHistoryButton').disabled = !all.length;
    icons();
  }

  function setPlace(which, place) { state[which] = place; $(`#${which === 'origin' ? 'originInput' : 'destinationInput'}`).value = place.name; }

  /* ---------- MapQuest trip (tables) and Webex summary ---------- */

  // MapQuest plans the same trip and its answer is shown as two tables. The server posts a
  // trip summary (with weather) to Webex each time MapQuest is asked, so each trip, mode and
  // departure time is asked once and kept here; switching units only redraws the tables
  // (MapQuest sends km and miles, weather comes in °C and is converted here).
  // The panel follows the last route shown (state.mapquestKey), not the live form fields, so
  // it stays put while the user types a new place or when Commute results arrive later.
  // MapQuest has no public transport, so Commute shows MapQuest's driving route.
  const MQ_PREVIEW_STEPS = 4;

  function mapquestRequest() {
    const mode = ['car', 'bike', 'foot'].includes(state.mode) ? state.mode : 'car';
    return { origin: state.origin, destination: state.destination, mode, unit: state.unit, depart_at: state.departAt };
  }

  async function askMapQuest(key, request) {
    try {
      const result = await api('/api/mapquest', { method: 'POST', body: JSON.stringify(request) });
      state.mapquest[key] = { trip: result.trip, weather: result.weather, webex: { ...result.webex, at: Date.now() }, request };
    } catch (error) {
      state.mapquest[key] = { error: error.message, request };
    }
    if (key === state.mapquestKey) renderMapQuest();
  }

  function loadMapQuest() {
    if (!state.origin || !state.destination) return;
    const request = mapquestRequest();
    const key = `${request.origin.lat},${request.origin.lng}|${request.destination.lat},${request.destination.lng}|${request.mode}|${request.depart_at || 'now'}`;
    state.mapquestKey = key;
    if (state.mapquest[key]) { renderMapQuest(); return; }
    state.mapquest[key] = { loading: true, request };
    renderMapQuest();
    askMapQuest(key, request);
  }

  // "Send again" / "Try again": asks MapQuest once more (fresh weather) and posts a new summary.
  function resendMapQuest() {
    const key = state.mapquestKey; const entry = state.mapquest[key];
    if (!entry || entry.loading || entry.sending) return;
    state.mapquest[key] = { ...entry, sending: true };
    renderMapQuest();
    askMapQuest(key, { ...entry.request, unit: state.unit });
  }

  const mqDistance = (value) => (value < 0.05 ? '&lt; 0.1' : value.toFixed(1));
  const mqTemp = (celsius) => (state.unit === 'imperial' ? `${Math.round(celsius * 9 / 5 + 32)}°F` : `${Math.round(celsius)}°C`);
  const stepTime = (seconds) => {
    if (seconds < 60) return `${seconds} s`;
    const minutes = Math.round(seconds / 60); const h = Math.floor(minutes / 60);
    return h ? `${h} h ${minutes % 60} min` : `${minutes} min`;
  };

  function mqWeatherCell(weather) {
    const part = (label, reading) => reading
      ? `<span class="mq-weather-part" title="${escapeHtml(reading.description)}, feels like ${mqTemp(reading.feels_like_c)}, ${reading.rain_chance}% chance of rain"><span aria-hidden="true">${reading.emoji}</span> <strong>${mqTemp(reading.temp_c)}</strong> <small>${label}</small></span>`
      : '';
    return `${part('leaving', weather.start)}${part('arriving', weather.end)}`;
  }

  function renderMapQuest() {
    const section = $('#mapquestSection'); const body = $('#mapquestBody');
    const entry = state.mapquestKey && state.mapquest[state.mapquestKey];
    if (!entry) { section.classList.add('hidden'); return; }
    section.classList.remove('hidden');
    const modeLabel = $('#mapquestMode');
    if (entry.loading) {
      modeLabel.textContent = '';
      body.innerHTML = `<div class="mq-skeleton" aria-label="Loading MapQuest directions">${'<span></span>'.repeat(5)}</div><p class="sr-only">Asking MapQuest for directions…</p>`;
      return;
    }
    if (entry.error) {
      modeLabel.textContent = '';
      body.innerHTML = `<p class="mq-error"><i data-lucide="triangle-alert"></i><span>${escapeHtml(entry.error)}</span><button type="button" class="text-button" data-mq-retry>Try again</button></p>`;
      icons(); return;
    }
    const trip = entry.trip; const english = state.unit === 'imperial'; const unit = english ? 'mi' : 'km';
    modeLabel.textContent = `${modes[trip.mode]?.verb || ''} · ${trip.route_type}`;
    const fuel = trip.fuel ? (english ? `${trip.fuel.gallons.toFixed(2)} gal` : `${trip.fuel.liters.toFixed(2)} L`) : null;
    const rows = [
      ['Distance', `<span class="mq-chip green">${mqDistance(trip.distance[unit])} ${unit}</span>`],
      ['Trip duration', `<span class="mq-chip amber">${escapeHtml(trip.formatted_time)}</span> <small>hh:mm:ss</small>`],
      ...(entry.weather ? [['Weather', mqWeatherCell(entry.weather)]] : []),
      ...(fuel ? [['Fuel used (est.)', `<span class="mq-chip purple">${fuel}</span>`]] : []),
      ['Toll roads', trip.has_toll_road ? '<span class="mq-chip red">Yes</span>' : '<span class="mq-chip green">No</span>'],
      ['Highways', trip.has_highway ? '<span class="mq-chip amber">Yes</span>' : '<span class="mq-chip green">No</span>'],
    ];
    const summary = `<table class="mq-table mq-summary"><caption class="sr-only">MapQuest trip summary</caption><tbody>${rows.map(([label, value]) => `<tr><th scope="row">${label}</th><td>${value}</td></tr>`).join('')}</tbody></table>`;
    const advice = entry.weather?.advice?.length ? `<ul class="mq-advice">${entry.weather.advice.map((tip) => `<li>${escapeHtml(tip)}</li>`).join('')}</ul>` : '';

    const count = trip.maneuvers.length;
    const collapsible = count > MQ_PREVIEW_STEPS + 1;
    const collapsed = collapsible && !state.mapquestExpanded;
    const steps = trip.maneuvers.map((step) => `<tr><td class="mq-num">${step.number}</td><td>${escapeHtml(step.narrative)}</td><td class="mq-dist">${mqDistance(step.distance[unit])}</td><td class="mq-time">${stepTime(step.seconds)}</td></tr>`).join('');
    const toggle = collapsible ? `<button type="button" class="mq-toggle" data-mq-toggle aria-expanded="${!collapsed}" aria-controls="mqSteps">${collapsed ? `Show all ${count} steps` : 'Show fewer steps'}<i data-lucide="${collapsed ? 'chevron-down' : 'chevron-up'}"></i></button>` : '';
    const directions = count ? `<table class="mq-table mq-steps${collapsed ? ' collapsed' : ''}" id="mqSteps"><caption>Turn-by-turn directions <small>${count} steps</small></caption><thead><tr><th scope="col">#</th><th scope="col">Direction</th><th scope="col">${unit}</th><th scope="col">Time</th></tr></thead><tbody>${steps}</tbody></table>${toggle}` : '';

    const webex = entry.webex || {};
    const notSetUp = !webex.sent && /not set up/i.test(webex.message || '');
    const sentText = webex.sent ? `Sent to Webex at ${new Date(webex.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}${entry.weather ? ', with weather' : ''}.` : escapeHtml(webex.message || '');
    const action = entry.sending ? '<span class="mq-sending">Sending…</span>' : notSetUp ? '' : `<button type="button" class="text-button" data-mq-retry>${webex.sent ? 'Send again' : 'Try again'}</button>`;
    const webexLine = `<div class="mq-webex ${webex.sent ? 'sent' : 'not-sent'}" role="status"><i data-lucide="${webex.sent ? 'circle-check' : 'message-square-warning'}"></i><span>${sentText}</span>${action}</div>`;
    const note = state.mode === 'commute' ? '<p class="empty-note mq-note">MapQuest has no jeepney, bus or train routes, so this is its driving route.</p>' : '';
    body.innerHTML = note + summary + advice + directions + webexLine;
    icons();
  }

  $('#mapquestBody').addEventListener('click', (event) => {
    if (event.target.closest('[data-mq-retry]')) { resendMapQuest(); return; }
    if (event.target.closest('[data-mq-toggle]')) {
      state.mapquestExpanded = !state.mapquestExpanded;
      renderMapQuest();
      if (!state.mapquestExpanded) $('#mapquestSection').scrollIntoView({ block: 'nearest' });
    }
  });

  /* ---------- Requests ---------- */

  function showRoute(route) {
    state.route = route;
    drawRoute(state.route, state.origin, state.destination);
    renderSummary(); renderDirections(); renderFuel(); loadAlternatives(); loadWeather(); loadMapQuest();
    recordTrip({ origin: state.origin, destination: state.destination, mode: state.mode, route });
    renderSaved();
    setStatus('Route found.', 'success');
    if (!state.editingTrip) setCompact(true); // Don't fold the form while the user is changing the trip.
    // On phones, slide the panel away so the route is visible on the map.
    $('#expandSheet span').textContent = 'Show route details';
    if (isMobile()) $('#sidebar').classList.add('collapsed');
  }

  /* ---------- Compact header ---------- */

  // After a route is shown, the blue search form folds into a one-line trip bar
  // ("Pasig to Cubao, leave now, Edit") so the route details get the space.
  const shortName = (place) => String(place?.name || '').split(',')[0].trim() || 'Selected place';

  function updateTripBar() {
    if (!state.origin || !state.destination) return;
    $('#tripBarFrom').textContent = shortName(state.origin);
    $('#tripBarTo').textContent = shortName(state.destination);
    $('#tripBarWhen').textContent = state.departAt ? `Leave ${whenText(state.departAt)}` : 'Leave now';
    $('#tripBar').setAttribute('aria-label', `Change trip from ${state.origin.name} to ${state.destination.name}, ${$('#tripBarWhen').textContent.toLowerCase()}`);
  }

  function setCompact(compact) {
    const header = $('.search-header');
    const wasCompact = header.classList.contains('compact');
    header.classList.toggle('compact', compact);
    $('#tripBar').hidden = !compact;
    // While editing an existing route, offer a way back without searching again.
    $('#backToRoute').hidden = compact || !state.route;
    if (compact) updateTripBar();
    if (!compact && wasCompact) $('#destinationInput').focus({ preventScroll: true });
  }

  const cachedRoute = (mode) => state.comparison?.find((item) => item.mode === mode && item.available && item.route)?.route;

  // Selecting another option reuses the route from the last search; it only
  // asks GraphHopper again if that mode's route isn't already loaded.
  async function runRoute() {
    const cached = cachedRoute(state.mode);
    if (cached) { showRoute(cached); return; }
    if (state.busy) return;
    setStatus(); setBusy(true);
    try {
      if (state.mode === 'commute') {
        const item = state.commuteItem?.loading ? await state.commutePromise : await loadCommute();
        if (!item.available) throw new Error(item.error);
        showRoute(item.route); return;
      }
      const result = await api('/api/route', { method: 'POST', body: JSON.stringify({ origin: state.origin, destination: state.destination, mode: state.mode }) });
      state.origin = result.origin; state.destination = result.destination;
      showRoute(result.route);
    } catch (error) { setStatus(error.message); }
    finally { setBusy(false); }
  }

  // Commute routes (jeepney, bus, UV Express, LRT/MRT) found on the web by OpenAI.
  // This can take about 30 seconds, so the other modes are shown first and this row
  // fills in when ready. Errors become an unavailable item.
  let commuteSearch = 0;
  function loadCommute() {
    const search = ++commuteSearch;
    state.commuteItem = { mode: 'commute', loading: true };
    placeCommuteItem();
    state.commutePromise = (async () => {
      let item;
      try {
        const result = await api('/api/commute', { method: 'POST', body: JSON.stringify({ origin: state.origin, destination: state.destination, depart_at: state.departAt }) });
        const best = result.routes[0];
        item = { mode: 'commute', available: true, distance_m: best.distance_m, duration_ms: best.duration_ms, route: best, routes: result.routes };
      } catch (error) {
        item = { mode: 'commute', available: false, error: error.message };
      }
      if (search !== commuteSearch) return item; // A newer search replaced this one.
      state.commuteItem = item;
      if (item.available) state.alternatives.commute = item.routes; else delete state.alternatives.commute;
      placeCommuteItem();
      // The user's preferred mode is Commute and they haven't picked another option: show it now.
      if (item.available && state.waitingForCommute) { state.waitingForCommute = false; state.mode = 'commute'; markSelectedOption(); showRoute(item.route); }
      else if (state.waitingForCommute) { state.waitingForCommute = false; }
      return item;
    })();
    return state.commutePromise;
  }

  function placeCommuteItem() {
    if (!state.comparison) return;
    state.comparison = [...state.comparison.filter((entry) => entry.mode !== 'commute'), state.commuteItem];
    renderComparison();
  }

  // One request fetches every mode's route (the server asks GraphHopper for all three modes at once).
  async function compareModes() {
    if (!state.origin || !state.destination || state.comparing) return false;
    state.comparing = true; $('#compareButton').disabled = true; renderComparisonLoading();
    try {
      const result = await api('/api/compare', { method: 'POST', body: tripBody() });
      state.comparison = [...result.comparison, state.commuteItem || { mode: 'commute', loading: true }]; state.origin = result.origin; state.destination = result.destination;
      renderComparison();
      return true;
    } catch (error) {
      state.comparison = null;
      $('#comparisonList').innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`;
      setStatus(error.message);
      return false;
    } finally { state.comparing = false; $('#compareButton').disabled = false; }
  }

  // A new search lists every mode (like a transit planner) and opens the preferred one,
  // or the fastest available mode if the preferred one has no route.
  async function findRoutes() {
    if (state.busy) return;
    if (!state.origin || !state.destination) { setStatus('Pick a start and destination from the suggestions.'); return; }
    setStatus(); setBusy(true);
    state.comparison = null; state.alternatives = {}; state.alternativesError = null; state.loadingAlternatives = null;
    state.commuteItem = null; state.waitingForCommute = false; state.editingTrip = false;
    state.mapquest = {}; // A new search asks MapQuest again, which posts a fresh Webex summary.
    loadCommute();
    try {
      if (!(await compareModes())) return;
      // A new search opens Drive (or the cycling/walking mode the user picked) and stays there:
      // Commute results arrive later and are only listed, never opened automatically.
      if (state.mode === 'commute' || !cachedRoute(state.mode)) {
        const roads = state.comparison.filter((item) => item.mode !== 'commute' && item.available && item.route);
        const pick = roads.find((item) => item.mode === 'car') || roads.sort((a, b) => a.duration_ms - b.duration_ms)[0];
        if (!pick) {
          // No road route at all: Commute is the only option left, so open it when it's ready.
          state.waitingForCommute = true; setStatus('Finding commute routes…'); return;
        }
        state.mode = pick.mode;
      }
      markSelectedOption();
      showRoute(cachedRoute(state.mode));
      $('#resultsScroll').scrollTop = 0;
    } finally {
      setBusy(false);
    }
  }

  /* ---------- Autocomplete ---------- */

  function setupAutocomplete(inputId, listId, which) {
    const input = $(`#${inputId}`); const list = $(`#${listId}`); let timer; let requestId = 0;
    // An empty search box offers saved places first.
    input.addEventListener('focus', () => { if (!input.value.trim()) savedPlaceSuggestions(which, list); });
    input.addEventListener('input', () => {
      state[which] = null; clearTimeout(timer); list.classList.remove('open'); const query = input.value.trim();
      if (!query) { savedPlaceSuggestions(which, list); return; }
      if (query.length < 2) return;
      timer = setTimeout(async () => {
        const id = ++requestId;
        try {
          const { suggestions } = await api(`/api/geocode?q=${encodeURIComponent(query)}`); if (id !== requestId || input.value.trim() !== query) return;
          list.innerHTML = '';
          suggestions.forEach((place) => {
            const button = document.createElement('button'); button.type = 'button'; button.className = 'suggestion'; button.setAttribute('role', 'option');
            button.innerHTML = `<i data-lucide="map-pin"></i><span>${escapeHtml(place.name || place.label)}<small>${escapeHtml(place.label && place.label !== place.name ? place.label : place.country || '')}</small></span>`;
            button.addEventListener('click', () => {
              setPlace(which, { ...place, name: place.label || place.name }); list.classList.remove('open'); setStatus();
              if (which === 'origin' && !state.destination) $('#destinationInput').focus();
              else if (state.origin && state.destination) findRoutes();
            });
            list.appendChild(button);
          });
          if (!suggestions.length) list.innerHTML = '<div class="suggestion"><span>No places found in Metro Manila. Try a more specific search.</span></div>';
          list.classList.add('open'); icons();
        } catch (error) { if (id === requestId) { list.innerHTML = `<div class="suggestion"><span>${escapeHtml(error.message)}</span></div>`; list.classList.add('open'); } }
      }, 320);
    });
    input.addEventListener('keydown', (event) => { if (event.key === 'Escape') list.classList.remove('open'); if (event.key === 'Enter' && list.classList.contains('open')) { event.preventDefault(); list.querySelector('.suggestion[role="option"]')?.click(); } });
    document.addEventListener('click', (event) => { if (!event.target.closest(`#${inputId}`) && !event.target.closest(`#${listId}`)) list.classList.remove('open'); });
  }

  function updateUnit(unit) {
    state.unit = unit; localStorage.setItem('maply-unit', unit);
    $$('[data-unit]').forEach((button) => { button.classList.toggle('selected', button.dataset.unit === unit); button.setAttribute('aria-pressed', String(button.dataset.unit === unit)); });
    if (state.route) { renderSummary(); renderDirections(); renderFuel(); renderAlternatives(); renderWeather(); renderMapQuest(); }
    if (state.comparison) renderComparison();
  }

  function showToast(message) { const toast = $('#mapToast'); toast.textContent = message; toast.classList.remove('hidden'); setTimeout(() => toast.classList.add('hidden'), 3500); }

  /* ---------- Wiring ---------- */

  initializeMap(); icons(); renderSaved(); renderPlaces();
  setupAutocomplete('originInput', 'originSuggestions', 'origin'); setupAutocomplete('destinationInput', 'destinationSuggestions', 'destination');
  $('#fuelPrice').value = localStorage.getItem('maply-fuel-price') || '65.00';
  $('#fuelEfficiency').value = localStorage.getItem('maply-fuel-efficiency') || '12';
  updateUnit(state.unit);

  $('#departSelect').addEventListener('change', (event) => {
    const custom = $('#departCustom');
    if (event.target.value === 'custom') {
      custom.hidden = false;
      custom.min = toLocalInput(Date.now()); custom.max = toLocalInput(Date.now() + 5 * 24 * 3600000);
      custom.value = toLocalInput(state.departAt ?? Date.now()); custom.focus();
      return;
    }
    setDeparture(departureFromMenu(event.target.value), event.target.value);
  });
  $('#departCustom').addEventListener('change', (event) => {
    const time = new Date(event.target.value).getTime();
    if (Number.isFinite(time)) setDeparture(time < Date.now() ? null : time);
  });
  $('#tripBar').addEventListener('click', () => { state.editingTrip = true; setCompact(false); });
  $('#backToRoute').addEventListener('click', () => { state.editingTrip = false; setCompact(true); });
  $('#routeForm').addEventListener('submit', (event) => { event.preventDefault(); findRoutes(); });
  $('#compareButton').addEventListener('click', () => { loadCommute(); compareModes(); });
  $('#swapButton').addEventListener('click', () => {
    const origin = state.origin; const originText = $('#originInput').value;
    state.origin = state.destination; state.destination = origin;
    $('#originInput').value = $('#destinationInput').value; $('#destinationInput').value = originText;
  });
  $$('[data-unit]').forEach((button) => button.addEventListener('click', () => updateUnit(button.dataset.unit)));
  ['fuelPrice', 'fuelEfficiency'].forEach((id) => $(`#${id}`).addEventListener('input', () => {
    localStorage.setItem(id === 'fuelPrice' ? 'maply-fuel-price' : 'maply-fuel-efficiency', $(`#${id}`).value);
    renderFuel(); if (state.comparison) renderComparison();
  }));
  $('#openHistoryButton').addEventListener('click', () => { $('#historySearch').value = ''; renderHistoryDialog(); $('#historyDialog').showModal(); });
  $('#closeHistoryButton').addEventListener('click', () => $('#historyDialog').close());
  $('#historySearch').addEventListener('input', renderHistoryDialog);
  $('#clearHistoryButton').addEventListener('click', () => {
    if (!window.confirm('Delete all trip history on this device? This cannot be undone.')) return;
    clearHistory(); renderSaved(); renderHistoryDialog();
  });
  $('#placeForm').addEventListener('submit', (event) => { if (event.submitter?.value === 'save') savePlaceFromDialog(); });
  $('#placeForm').kind.forEach?.((radio) => radio.addEventListener('change', () => {
    // Pre-fill the name with the type (Home, Work, School) unless the user typed one.
    const current = $('#placeLabel').value.trim();
    if (!current || Object.values(PLACE_KINDS).some((kind) => kind.label === current)) $('#placeLabel').value = radio.value === 'other' ? '' : PLACE_KINDS[radio.value].label;
  }));
  $('#cancelPlaceButton').addEventListener('click', () => { editingPlace = null; $('#placeDialog').close(); });
  $('#removePlaceButton').addEventListener('click', () => {
    if (editingPlace?.existing) { removePlace(editingPlace.existing.id); showToast(`Removed ${editingPlace.existing.label}.`); }
    editingPlace = null; $('#placeDialog').close(); renderPlaces(); if (state.route) renderDirections();
  });
  // Clicking the dimmed backdrop closes a dialog.
  $$('dialog').forEach((dialog) => dialog.addEventListener('click', (event) => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    const outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
    if (outside) { editingPlace = null; dialog.close(); }
  }));
  $('#collapseSheet').addEventListener('click', () => $('#sidebar').classList.add('collapsed'));
  $('#expandSheet').addEventListener('click', () => $('#sidebar').classList.remove('collapsed'));
  $('#locateButton').addEventListener('click', () => {
    if (!state.map) { showToast('Add a Mapbox access token to enable the map.'); return; }
    if (!navigator.geolocation) { showToast('Location is not supported in this browser.'); return; }
    navigator.geolocation.getCurrentPosition((position) => {
      const point = [position.coords.longitude, position.coords.latitude];
      if (!inMetroManila(point)) { showToast('You appear to be outside Metro Manila. Maply only covers Metro Manila.'); return; }
      if (state.userMarker) state.userMarker.remove();
      state.userMarker = new mapboxgl.Marker({ color: '#1a73e8' }).setLngLat(point).setPopup(new mapboxgl.Popup({ offset: 20 }).setText('You are here')).addTo(state.map);
      state.map.flyTo({ center: point, zoom: 15 });
    }, () => showToast('Could not get your location. Check browser permissions.'), { enableHighAccuracy: true, timeout: 10000 });
  });
  $('#mapThemeButton').addEventListener('click', () => {
    if (!state.map) { showToast('Add a Mapbox access token to enable map styles.'); return; }
    state.satellite = !state.satellite;
    $('#mapThemeButton').setAttribute('aria-label', state.satellite ? 'Switch to street map' : 'Switch to satellite map');
    state.mapReady = false;
    state.map.setStyle(state.satellite ? 'mapbox://styles/mapbox/satellite-streets-v12' : 'mapbox://styles/mapbox/standard');
    state.map.once('style.load', () => {
      state.mapReady = true;
      if (state.route && state.origin && state.destination) drawRoute(state.route, state.origin, state.destination);
    });
  });
})();
