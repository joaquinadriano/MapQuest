(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const state = { mode: 'car', unit: localStorage.getItem('mapquest-unit') || 'metric', origin: null, destination: null, route: null, comparison: null, map: null, mapReady: false, pendingRoute: null, markers: [], userMarker: null, userLocation: null, satellite: false, busy: false, comparing: false };
  const modes = { car: { name: 'Driving', icon: 'car-front' }, bike: { name: 'Cycling', icon: 'bike' }, foot: { name: 'Walking', icon: 'person-standing' } };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const icons = () => window.lucide?.createIcons();
  const setStatus = (message = '', type = '') => { const el = $('#statusMessage'); el.textContent = message; el.className = `status-message ${type}`; };
  const setBusy = (busy) => { state.busy = busy; const button = $('#findRoute'); button.disabled = busy; button.innerHTML = busy ? '<span class="loading-spinner"></span><span>Finding route…</span>' : '<i data-lucide="search"></i><span>Find route</span>'; icons(); };
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Something went wrong. Please try again.');
    return payload;
  }

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
    if (state.map.getLayer('planned-route')) state.map.removeLayer('planned-route');
    if (state.map.getSource('planned-route')) state.map.removeSource('planned-route');
    state.markers.forEach((marker) => marker.remove());
    state.markers = [];
  }
  function makeMarker(destination = false) {
    const element = document.createElement('div');
    element.className = `route-marker${destination ? ' destination' : ''}`;
    element.innerHTML = '<span class="route-marker-pin"><span class="route-marker-dot"></span></span>';
    return element;
  }
  function drawRoute(route, origin, destination) {
    if (!state.map || !state.mapReady) { state.pendingRoute = [route, origin, destination]; return; }
    clearMapRoute();
    const coordinates = route.geometry.map(([lat, lng]) => [lng, lat]);
    state.map.addSource('planned-route', { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } } });
    state.map.addLayer({ id: 'planned-route', type: 'line', source: 'planned-route', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': '#1a73e8', 'line-width': 6, 'line-opacity': .92 } });
    const start = new mapboxgl.Marker({ element: makeMarker(), anchor: 'bottom' }).setLngLat([origin.lng, origin.lat]).setPopup(new mapboxgl.Popup({ offset: 25 }).setHTML(`<strong>Start</strong><br>${escapeHtml(origin.name)}`)).addTo(state.map);
    const end = new mapboxgl.Marker({ element: makeMarker(true), anchor: 'bottom' }).setLngLat([destination.lng, destination.lat]).setPopup(new mapboxgl.Popup({ offset: 25 }).setHTML(`<strong>Destination</strong><br>${escapeHtml(destination.name)}`)).addTo(state.map);
    state.markers.push(start, end);
    const bounds = new mapboxgl.LngLatBounds(); coordinates.forEach((coordinate) => bounds.extend(coordinate));
    state.map.fitBounds(bounds, { padding: { top: 80, bottom: 60, left: 55, right: 55 }, maxZoom: 15, duration: 500 });
  }
  const formatDistance = (meters, digits = 1) => state.unit === 'imperial' ? `${(meters / 1609.344).toFixed(digits)} mi` : `${(meters / 1000).toFixed(digits)} km`;
  const formatStepDistance = (meters) => state.unit === 'imperial' ? (meters < 160.934 ? `${Math.round(meters * 3.28084)} ft` : `${(meters / 1609.344).toFixed(1)} mi`) : (meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`);
  function formatDuration(milliseconds) {
    const minutes = Math.max(1, Math.round(milliseconds / 60000));
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60); const rest = minutes % 60;
    return rest ? `${hours} hr ${rest} min` : `${hours} hr`;
  }
  function renderSummary() {
    const route = state.route; if (!route) return;
    $('#routeSummary').classList.remove('hidden');
    $('#routeSummary').innerHTML = `<div class="route-summary-top"><div><div class="route-distance">${formatDistance(route.distance_m)} <small>${state.unit === 'metric' ? 'total' : 'total'}</small></div><div class="route-meta">${modes[state.mode].name} · ${formatDuration(route.duration_ms)} estimated</div></div><div class="route-duration">${formatDuration(route.duration_ms)}</div></div><div class="route-endpoints"><i data-lucide="circle-dot"></i><span title="${escapeHtml(state.origin.name)}">${escapeHtml(state.origin.name)}</span><i data-lucide="arrow-right"></i><i data-lucide="map-pin"></i><span title="${escapeHtml(state.destination.name)}">${escapeHtml(state.destination.name)}</span></div>`;
    icons();
  }
  function renderDirections() {
    const list = $('#directionsList'); list.innerHTML = '';
    const steps = state.route.instructions.filter((step) => step.text && step.text.toLowerCase() !== 'finish');
    $('#instructionCount').textContent = `${steps.length} steps`;
    if (!steps.length) { list.innerHTML = '<li class="empty-note">No turn-by-turn instructions were returned for this route.</li>'; return; }
    steps.forEach((step) => {
      const item = document.createElement('li'); item.className = 'direction-item';
      const signIcon = step.sign === -2 ? 'corner-up-left' : step.sign === -1 ? 'corner-down-left' : step.sign === 2 ? 'corner-up-right' : step.sign === 1 ? 'corner-down-right' : step.sign === 4 ? 'flag' : step.sign === 6 ? 'rotate-cw' : 'arrow-up';
      item.innerHTML = `<span class="turn-icon"><i data-lucide="${signIcon}"></i></span><span class="direction-text">${escapeHtml(step.text)}</span><span class="direction-distance">${formatStepDistance(step.distance)}</span>`;
      item.addEventListener('click', () => { const index = state.route.instructions.indexOf(step); const point = state.route.geometry[Math.min(index * 2, state.route.geometry.length - 1)]; if (point && state.map) state.map.flyTo({ center: [point[1], point[0]], zoom: Math.max(state.map.getZoom(), 15) }); }); list.appendChild(item);
    }); icons();
  }
  function renderFuel() {
    if (!state.route || state.mode !== 'car') { $('#fuelSection').classList.add('hidden'); return; }
    $('#fuelSection').classList.remove('hidden');
    const price = Number($('#fuelPrice').value), efficiency = Number($('#fuelEfficiency').value);
    if (!(efficiency > 0) || !(price >= 0)) { $('#fuelResult').innerHTML = '<span>Enter a valid fuel price and efficiency.</span>'; return; }
    const km = state.route.distance_m / 1000; const liters = km / efficiency; const cost = liters * price;
    $('#fuelResult').innerHTML = `<span>About ${liters.toFixed(1)} L for ${km.toFixed(1)} km</span><strong>₱${cost.toLocaleString('en-PH', { maximumFractionDigits: 2 })}</strong>`;
  }
  function getStored(key) { try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value : []; } catch { return []; } }
  function renderSaved() {
    localStorage.removeItem('mapquest-favorites');
    const history = getStored('mapquest-history');
    $('#historyList').innerHTML = history.length ? '' : '<p class="empty-note">Your recent routes will appear here.</p>';
    history.slice(0, 6).forEach((entry) => $('#historyList').appendChild(savedRow(entry)));
  }
  function savedRow(entry) {
    const row = document.createElement('div'); row.className = 'saved-item';
    const label = document.createElement('button'); label.type = 'button'; label.textContent = `${entry.origin.name} → ${entry.destination.name}`; label.title = 'Plan this route again';
    label.addEventListener('click', () => { setPlace('origin', entry.origin); setPlace('destination', entry.destination); state.mode = entry.mode || 'car'; setModeButton(); runRoute(); });
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'delete-saved'; remove.setAttribute('aria-label', 'Remove recent trip'); remove.innerHTML = '<i data-lucide="x"></i>';
    remove.addEventListener('click', () => { localStorage.setItem('mapquest-history', JSON.stringify(getStored('mapquest-history').filter((item) => item.id !== entry.id))); renderSaved(); icons(); });
    row.append(label, remove); return row;
  }
  function setPlace(which, place) { state[which] = place; $(`#${which === 'origin' ? 'originInput' : 'destinationInput'}`).value = place.name; }
  function setModeButton() { $$('.mode-button').forEach((button) => { const active = button.dataset.mode === state.mode; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); }); }
  async function runRoute(preserveComparison = false) {
    if (state.busy) return;
    if (!state.origin || !state.destination) { setStatus('Choose an origin and destination from the location suggestions.'); return; }
    setStatus(); setBusy(true);
    try {
      const result = await api('/api/route', { method: 'POST', body: JSON.stringify({ origin: state.origin, destination: state.destination, mode: state.mode }) });
      state.route = result.route; state.origin = result.origin; state.destination = result.destination;
      drawRoute(state.route, state.origin, state.destination); renderSummary(); renderDirections(); renderFuel();
      $('#directionsSection').classList.remove('hidden');
      const trip = { id: crypto.randomUUID(), origin: state.origin, destination: state.destination, mode: state.mode, createdAt: Date.now() };
      const history = getStored('mapquest-history').filter((item) => item.origin.name !== trip.origin.name || item.destination.name !== trip.destination.name);
      localStorage.setItem('mapquest-history', JSON.stringify([trip, ...history].slice(0, 10))); renderSaved();
      setStatus('Route found.', 'success');
      $('#comparisonSection').classList.remove('hidden');
      if (!preserveComparison) {
        state.comparison = null;
        $('#comparisonList').innerHTML = '<p class="empty-note">Compare available modes to see distance and estimated time.</p>';
      } else {
        $$('.compare-option').forEach((option) => { option.classList.toggle('selected', option.dataset.mode === state.mode); });
      }
      if (window.innerWidth <= 760) $('#sidebar').classList.add('collapsed');
    } catch (error) { setStatus(error.message); }
    finally { setBusy(false); }
  }
  async function compareModes() {
    if (!state.origin || !state.destination) return;
    const section = $('#comparisonSection'); section.classList.remove('hidden'); $('#comparisonList').innerHTML = '<p class="empty-note">Comparing available routes…</p>'; $('#compareButton').disabled = true; state.comparing = true;
    try {
      const { comparison } = await api('/api/compare', { method: 'POST', body: JSON.stringify({ origin: state.origin, destination: state.destination }) });
      state.comparison = comparison;
      renderComparison();
    } catch (error) { $('#comparisonList').innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`; }
    finally { state.comparing = false; $('#compareButton').disabled = false; }
  }
  function renderComparison() {
    if (!state.comparison) return;
    $('#comparisonList').innerHTML = '';
    state.comparison.forEach((item) => {
      const button = document.createElement('button'); button.type = 'button'; button.dataset.mode = item.mode; button.className = `compare-option${item.mode === state.mode ? ' selected' : ''}`; button.disabled = !item.available;
      button.innerHTML = `<span class="compare-icon"><i data-lucide="${modes[item.mode].icon}"></i></span><span class="compare-copy"><span class="compare-name">${modes[item.mode].name}</span><span class="compare-detail">${item.available ? formatDistance(item.distance_m) : 'Could not compare'}</span>${item.available ? '' : `<span class="compare-error">${escapeHtml(item.error)}</span>`}</span>${item.available ? `<span class="compare-time">${formatDuration(item.duration_ms)}</span>` : ''}`;
      button.addEventListener('click', () => { state.mode = item.mode; setModeButton(); runRoute(true); }); $('#comparisonList').appendChild(button);
    }); icons();
  }
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
          suggestions.forEach((place) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'suggestion'; button.setAttribute('role', 'option'); button.innerHTML = `<i data-lucide="map-pin"></i><span>${escapeHtml(place.label || place.name)}<small>${escapeHtml(place.country || 'Location')}</small></span>`; button.addEventListener('click', () => { setPlace(which, { ...place, name: place.label || place.name }); list.classList.remove('open'); setStatus(); }); list.appendChild(button); });
          if (!suggestions.length) list.innerHTML = '<div class="suggestion"><span>No places found. Try a more specific search.</span></div>';
          list.classList.add('open'); icons();
        } catch (error) { if (id === requestId) { list.innerHTML = `<div class="suggestion"><span>${escapeHtml(error.message)}</span></div>`; list.classList.add('open'); } }
      }, 320);
    });
    input.addEventListener('keydown', (event) => { if (event.key === 'Escape') list.classList.remove('open'); if (event.key === 'Enter' && list.classList.contains('open')) { event.preventDefault(); list.querySelector('.suggestion[role="option"]')?.click(); } });
    document.addEventListener('click', (event) => { if (!event.target.closest(`#${inputId}`) && !event.target.closest(`#${listId}`)) list.classList.remove('open'); });
  }
  function updateUnit(unit) { state.unit = unit; localStorage.setItem('mapquest-unit', unit); $$('[data-unit]').forEach((button) => button.classList.toggle('selected', button.dataset.unit === unit)); if (state.route) { renderSummary(); renderDirections(); renderFuel(); if (state.comparison) renderComparison(); } }

  initializeMap(); icons(); renderSaved();
  setupAutocomplete('originInput', 'originSuggestions', 'origin'); setupAutocomplete('destinationInput', 'destinationSuggestions', 'destination');
  updateUnit(state.unit);
  $$('.mode-button').forEach((button) => button.addEventListener('click', () => { state.mode = button.dataset.mode; setModeButton(); if (state.route && state.origin && state.destination) runRoute(); }));
  $('#routeForm').addEventListener('submit', (event) => { event.preventDefault(); runRoute(); });
  $('#compareButton').addEventListener('click', compareModes);
  $('#swapButton').addEventListener('click', () => { const origin = state.origin; const destination = state.destination; const originText = $('#originInput').value; const destinationText = $('#destinationInput').value; state.origin = destination; state.destination = origin; $('#originInput').value = destinationText; $('#destinationInput').value = originText; });
  $$('[data-unit]').forEach((button) => button.addEventListener('click', () => updateUnit(button.dataset.unit)));
  $('#fuelPrice').value = localStorage.getItem('mapquest-fuel-price') || '65.00'; $('#fuelEfficiency').value = localStorage.getItem('mapquest-fuel-efficiency') || '12';
  ['fuelPrice', 'fuelEfficiency'].forEach((id) => $(`#${id}`).addEventListener('input', () => { localStorage.setItem(id === 'fuelPrice' ? 'mapquest-fuel-price' : 'mapquest-fuel-efficiency', $(`#${id}`).value); renderFuel(); }));
  $('#clearHistoryButton').addEventListener('click', () => { localStorage.removeItem('mapquest-history'); renderSaved(); });
  $('#collapseSheet').addEventListener('click', () => $('#sidebar').classList.add('collapsed'));
  $('#expandSheet').addEventListener('click', () => $('#sidebar').classList.remove('collapsed'));
  $('#locateButton').addEventListener('click', () => {
    if (!state.map) { showToast('Add a Mapbox access token to enable the map.'); return; }
    if (!navigator.geolocation) { showToast('Location is not supported in this browser.'); return; }
    navigator.geolocation.getCurrentPosition((position) => {
      const point = [position.coords.longitude, position.coords.latitude]; state.userLocation = point;
      if (state.userMarker) state.userMarker.remove();
      state.userMarker = new mapboxgl.Marker({ color: '#4285f4' }).setLngLat(point).setPopup(new mapboxgl.Popup({ offset: 20 }).setText('You are here')).addTo(state.map);
      state.map.flyTo({ center: point, zoom: 15 });
    }, () => showToast('Could not access your location. Check browser permissions.'), { enableHighAccuracy: true, timeout: 10000 });
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
  function showToast(message) { const toast = $('#mapToast'); toast.textContent = message; toast.classList.remove('hidden'); setTimeout(() => toast.classList.add('hidden'), 3500); }
})();
