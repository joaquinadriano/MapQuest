(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  // One-time move of saved settings and trips from the app's old name (MapQuest).
  ['mode', 'unit', 'history', 'fuel-price', 'fuel-efficiency', 'favorites'].forEach((key) => {
    const old = localStorage.getItem(`mapquest-${key}`);
    if (old !== null && localStorage.getItem(`maply-${key}`) === null) localStorage.setItem(`maply-${key}`, old);
    localStorage.removeItem(`mapquest-${key}`);
  });
  const state = { mode: localStorage.getItem('maply-mode') || 'car', unit: localStorage.getItem('maply-unit') || 'metric', origin: null, destination: null, route: null, comparison: null, map: null, mapReady: false, pendingRoute: null, markers: [], userMarker: null, satellite: false, busy: false, comparing: false };
  const modes = { car: { name: 'Drive', verb: 'Driving', icon: 'car-front' }, bike: { name: 'Cycle', verb: 'Cycling', icon: 'bike' }, foot: { name: 'Walk', verb: 'Walking', icon: 'footprints' } };
  if (!modes[state.mode]) state.mode = 'car';
  const GREEN = '#37ab2f';
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
    state.map = new mapboxgl.Map({ container: 'map', style: 'mapbox://styles/mapbox/standard', center: [120.9842, 14.5995], zoom: 12, attributionControl: true });
    state.map.addControl(new mapboxgl.NavigationControl({ showCompass: true }), 'bottom-right');
    state.map.on('load', () => {
      state.mapReady = true;
      state.map.resize();
      if (state.pendingRoute) { const [route, origin, destination] = state.pendingRoute; state.pendingRoute = null; drawRoute(route, origin, destination); }
    });
    state.map.on('error', (event) => {
      if (event.error?.status === 401 || event.error?.status === 403) showToast('Mapbox rejected this token. Check that it is valid and allowed for localhost.');
    });
    window.addEventListener('resize', () => state.map?.resize());
  }

  function clearMapRoute() {
    if (!state.map || !state.mapReady) return;
    ['planned-route', 'planned-route-casing'].forEach((id) => { if (state.map.getLayer(id)) state.map.removeLayer(id); });
    if (state.map.getSource('planned-route')) state.map.removeSource('planned-route');
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
    state.map.addSource('planned-route', { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } } });
    state.map.addLayer({ id: 'planned-route-casing', type: 'line', source: 'planned-route', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': '#ffffff', 'line-width': 11 } });
    state.map.addLayer({ id: 'planned-route', type: 'line', source: 'planned-route', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': GREEN, 'line-width': 7, ...(dashed ? { 'line-dasharray': [0.1, 1.8] } : {}) } });
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
  const arrivalTime = (milliseconds) => new Date(Date.now() + milliseconds).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  function fuelCost(distanceMeters) {
    const price = Number($('#fuelPrice').value), efficiency = Number($('#fuelEfficiency').value);
    if (!(efficiency > 0) || !(price >= 0)) return null;
    const liters = distanceMeters / 1000 / efficiency;
    return { liters, cost: liters * price };
  }
  const peso = (amount) => `₱${amount.toLocaleString('en-PH', { maximumFractionDigits: 0 })}`;

  /* ---------- Rendering ---------- */

  function renderSummary() {
    const route = state.route; if (!route) return;
    const time = durationParts(route.duration_ms);
    $('#routeSummary').classList.remove('hidden');
    $('#routeSummary').innerHTML = `<div class="journey-head"><div class="journey-time"><strong>${time.value}</strong><span>${time.unit}</span></div><div class="journey-arrive">Arrive around<strong>${arrivalTime(route.duration_ms)}</strong></div></div><div class="journey-meta"><span class="mode-chip"><i data-lucide="${modes[state.mode].icon}"></i>${modes[state.mode].verb}</span><span>${formatDistance(route.distance_m)}</span></div>`;
    icons();
  }

  function renderDirections() {
    const list = $('#directionsList'); list.innerHTML = '';
    const steps = state.route.instructions.filter((step) => step.text && step.text.toLowerCase() !== 'finish');
    $('#instructionCount').textContent = steps.length ? `${steps.length} steps` : '';
    const stop = (place, end = false) => { const li = document.createElement('li'); li.className = `timeline-stop${end ? ' end' : ''}`; li.innerHTML = `<span class="timeline-node"></span><span class="timeline-place">${escapeHtml(place.name)}</span><span></span>`; return li; };
    list.appendChild(stop(state.origin));
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
    list.appendChild(stop(state.destination, true));
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
    $('#comparisonList').innerHTML = '<div class="option-skeleton"></div><div class="option-skeleton"></div><div class="option-skeleton"></div>';
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
      if (item.available) {
        const fuel = item.mode === 'car' ? fuelCost(item.distance_m) : null;
        meta = `<span class="option-meta">${formatDistance(item.distance_m)}${fuel ? `, about ${peso(fuel.cost)} fuel` : ''}</span>`;
      } else {
        meta = `<span class="option-error">${escapeHtml(item.error || 'Not available for this trip')}</span>`;
      }
      const time = item.available ? durationParts(item.duration_ms) : null;
      button.innerHTML = `<span class="option-icon"><i data-lucide="${mode.icon}"></i></span><span class="option-body"><span class="option-name">${mode.name}${item.mode === fastest ? '<span class="option-tag">Fastest</span>' : ''}</span>${meta}</span>${time ? `<span class="option-time"><strong>${time.value}</strong><small>${time.unit}</small></span>` : ''}`;
      button.addEventListener('click', () => { if (item.mode === state.mode && state.route) return; state.mode = item.mode; localStorage.setItem('maply-mode', item.mode); markSelectedOption(); runRoute(); });
      list.appendChild(button);
    });
    icons();
  }

  function markSelectedOption() {
    $$('.option').forEach((option) => { const active = option.dataset.mode === state.mode; option.classList.toggle('selected', active); option.setAttribute('aria-pressed', String(active)); });
  }

  /* ---------- Recent trips ---------- */

  function getStored(key) { try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value : []; } catch { return []; } }

  function renderSaved() {
    localStorage.removeItem('maply-favorites');
    const history = getStored('maply-history');
    $('#historyList').innerHTML = history.length ? '' : '<p class="empty-note">Routes you plan will be listed here.</p>';
    history.slice(0, 6).forEach((entry) => $('#historyList').appendChild(savedRow(entry)));
    icons();
  }

  function savedRow(entry) {
    const row = document.createElement('div'); row.className = 'saved-item';
    const label = document.createElement('button'); label.type = 'button'; label.title = 'Plan this route again';
    label.innerHTML = `<span class="saved-icon"><i data-lucide="${modes[entry.mode]?.icon || 'clock'}"></i></span><span class="saved-route"><strong>${escapeHtml(entry.destination.name)}</strong><small>From ${escapeHtml(entry.origin.name)}</small></span>`;
    label.addEventListener('click', () => { setPlace('origin', entry.origin); setPlace('destination', entry.destination); if (modes[entry.mode]) state.mode = entry.mode; findRoutes(); });
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'delete-saved'; remove.setAttribute('aria-label', `Remove trip to ${entry.destination.name}`); remove.innerHTML = '<i data-lucide="x"></i>';
    remove.addEventListener('click', () => { localStorage.setItem('maply-history', JSON.stringify(getStored('maply-history').filter((item) => item.id !== entry.id))); renderSaved(); });
    row.append(label, remove); return row;
  }

  function rememberTrip() {
    const trip = { id: crypto.randomUUID(), origin: state.origin, destination: state.destination, mode: state.mode, createdAt: Date.now() };
    const history = getStored('maply-history').filter((item) => item.origin.name !== trip.origin.name || item.destination.name !== trip.destination.name);
    localStorage.setItem('maply-history', JSON.stringify([trip, ...history].slice(0, 10)));
    renderSaved();
  }

  function setPlace(which, place) { state[which] = place; $(`#${which === 'origin' ? 'originInput' : 'destinationInput'}`).value = place.name; }

  /* ---------- Requests ---------- */

  function showRoute(route) {
    state.route = route;
    drawRoute(state.route, state.origin, state.destination);
    renderSummary(); renderDirections(); renderFuel();
    rememberTrip();
  }

  const cachedRoute = (mode) => state.comparison?.find((item) => item.mode === mode && item.available && item.route)?.route;

  // Selecting another option reuses the route from the last search; it only
  // asks GraphHopper again if that mode's route isn't already loaded.
  async function runRoute() {
    const cached = cachedRoute(state.mode);
    if (cached) { setStatus(); showRoute(cached); return; }
    if (state.busy) return;
    setStatus(); setBusy(true);
    try {
      const result = await api('/api/route', { method: 'POST', body: JSON.stringify({ origin: state.origin, destination: state.destination, mode: state.mode }) });
      state.origin = result.origin; state.destination = result.destination;
      showRoute(result.route);
    } catch (error) { setStatus(error.message); }
    finally { setBusy(false); }
  }

  // One request fetches every mode's route (GraphHopper is called once per mode, in sequence).
  async function compareModes() {
    if (!state.origin || !state.destination || state.comparing) return false;
    state.comparing = true; $('#compareButton').disabled = true; renderComparisonLoading();
    try {
      const result = await api('/api/compare', { method: 'POST', body: tripBody() });
      state.comparison = result.comparison; state.origin = result.origin; state.destination = result.destination;
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
    state.comparison = null;
    try {
      if (!(await compareModes())) return;
      if (!cachedRoute(state.mode)) {
        const fastest = state.comparison.filter((item) => item.available).sort((a, b) => a.duration_ms - b.duration_ms)[0];
        if (!fastest) { setStatus(state.comparison[0]?.error || 'No route found between these places.'); return; }
        state.mode = fastest.mode;
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
    input.addEventListener('input', () => {
      state[which] = null; clearTimeout(timer); list.classList.remove('open'); const query = input.value.trim();
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
          if (!suggestions.length) list.innerHTML = '<div class="suggestion"><span>No places found. Try a more specific search.</span></div>';
          list.classList.add('open'); icons();
        } catch (error) { if (id === requestId) { list.innerHTML = `<div class="suggestion"><span>${escapeHtml(error.message)}</span></div>`; list.classList.add('open'); } }
      }, 320);
    });
    input.addEventListener('keydown', (event) => { if (event.key === 'Escape') list.classList.remove('open'); if (event.key === 'Enter' && list.classList.contains('open')) { event.preventDefault(); list.querySelector('.suggestion[role="option"]')?.click(); } });
    document.addEventListener('click', (event) => { if (!event.target.closest(`#${inputId}`) && !event.target.closest(`#${listId}`)) list.classList.remove('open'); });
  }

  function updateUnit(unit) {
    state.unit = unit; localStorage.setItem('maply-unit', unit);
    $$('[data-unit]').forEach((button) => button.classList.toggle('selected', button.dataset.unit === unit));
    if (state.route) { renderSummary(); renderDirections(); renderFuel(); }
    if (state.comparison) renderComparison();
  }

  function showToast(message) { const toast = $('#mapToast'); toast.textContent = message; toast.classList.remove('hidden'); setTimeout(() => toast.classList.add('hidden'), 3500); }

  /* ---------- Wiring ---------- */

  initializeMap(); icons(); renderSaved();
  setupAutocomplete('originInput', 'originSuggestions', 'origin'); setupAutocomplete('destinationInput', 'destinationSuggestions', 'destination');
  $('#fuelPrice').value = localStorage.getItem('maply-fuel-price') || '65.00';
  $('#fuelEfficiency').value = localStorage.getItem('maply-fuel-efficiency') || '12';
  updateUnit(state.unit);

  $('#routeForm').addEventListener('submit', (event) => { event.preventDefault(); findRoutes(); });
  $('#compareButton').addEventListener('click', compareModes);
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
  $('#clearHistoryButton').addEventListener('click', () => { localStorage.removeItem('maply-history'); renderSaved(); });
  $('#collapseSheet').addEventListener('click', () => $('#sidebar').classList.add('collapsed'));
  $('#expandSheet').addEventListener('click', () => $('#sidebar').classList.remove('collapsed'));
  $('#locateButton').addEventListener('click', () => {
    if (!state.map) { showToast('Add a Mapbox access token to enable the map.'); return; }
    if (!navigator.geolocation) { showToast('Location is not supported in this browser.'); return; }
    navigator.geolocation.getCurrentPosition((position) => {
      const point = [position.coords.longitude, position.coords.latitude];
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
