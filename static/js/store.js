// Saved places and trip history, kept in this browser's localStorage.
// No account or server storage is needed; data stays on the user's device.

const PLACES_KEY = 'maply-places';
const HISTORY_KEY = 'maply-history';
const HISTORY_LIMIT = 50;
// Switching Drive/Cycle/Walk for the same search updates one trip instead of adding several.
const SAME_TRIP_WINDOW_MS = 30 * 60 * 1000;

export const PLACE_KINDS = {
  home: { label: 'Home', icon: 'house' },
  work: { label: 'Work', icon: 'briefcase' },
  school: { label: 'School', icon: 'graduation-cap' },
  other: { label: 'Saved place', icon: 'star' },
};

function read(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}
const write = (key, value) => localStorage.setItem(key, JSON.stringify(value));
const samePoint = (a, b) => Math.abs(a.lat - b.lat) < 1e-5 && Math.abs(a.lng - b.lng) < 1e-5;
const cleanPlace = (place) => ({ name: String(place.name || '').slice(0, 200), lat: Number(place.lat), lng: Number(place.lng) });

/* ---------- Saved places ---------- */

export function getPlaces() {
  return read(PLACES_KEY).filter((item) => item && item.place && Number.isFinite(item.place.lat) && Number.isFinite(item.place.lng));
}

export function findSavedPlace(place) {
  return getPlaces().find((item) => samePoint(item.place, place));
}

// Home and Work are unique: saving a new Home replaces the old one.
export function savePlace({ id, label, kind, place }) {
  const places = getPlaces();
  const entry = { id: id || crypto.randomUUID(), label: String(label || '').trim().slice(0, 60) || PLACE_KINDS[kind]?.label || 'Saved place', kind: PLACE_KINDS[kind] ? kind : 'other', place: cleanPlace(place), savedAt: Date.now() };
  const kept = places.filter((item) => item.id !== entry.id && !samePoint(item.place, entry.place) && !((entry.kind === 'home' || entry.kind === 'work') && item.kind === entry.kind));
  const ordered = [entry, ...kept].sort((a, b) => kindOrder(a) - kindOrder(b));
  write(PLACES_KEY, ordered);
  return entry;
}

const kindOrder = (item) => ({ home: 0, work: 1, school: 2 }[item.kind] ?? 3);

export function removePlace(id) {
  write(PLACES_KEY, getPlaces().filter((item) => item.id !== id));
}

/* ---------- Trip history ---------- */

export function getHistory() {
  return read(HISTORY_KEY).filter((trip) => trip && trip.origin && trip.destination);
}

export function recordTrip({ origin, destination, mode, route }) {
  const history = getHistory();
  const now = Date.now();
  const latest = history[0];
  const details = { mode, distance_m: route?.distance_m ?? null, duration_ms: route?.duration_ms ?? null };
  if (latest && samePoint(latest.origin, origin) && samePoint(latest.destination, destination) && now - (latest.createdAt || 0) < SAME_TRIP_WINDOW_MS) {
    Object.assign(latest, details, { origin: cleanPlace(origin), destination: cleanPlace(destination) });
    write(HISTORY_KEY, history);
    return latest;
  }
  const trip = { id: crypto.randomUUID(), origin: cleanPlace(origin), destination: cleanPlace(destination), createdAt: now, ...details };
  write(HISTORY_KEY, [trip, ...history].slice(0, HISTORY_LIMIT));
  return trip;
}

export function removeTrip(id) {
  write(HISTORY_KEY, getHistory().filter((trip) => trip.id !== id));
}

export function clearHistory() {
  localStorage.removeItem(HISTORY_KEY);
}

// One entry per start/destination pair, newest first, for the "Recent" card.
export function recentTrips(limit = 3) {
  const seen = new Set();
  return getHistory().filter((trip) => {
    const key = `${trip.origin.lat},${trip.origin.lng}>${trip.destination.lat},${trip.destination.lng}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, limit);
}

export function historyStats(trips, days = 7) {
  const since = Date.now() - days * 24 * 60 * 60 * 1000;
  const recent = trips.filter((trip) => (trip.createdAt || 0) >= since);
  return { trips: recent.length, distance_m: recent.reduce((sum, trip) => sum + (trip.distance_m || 0), 0) };
}

// "Today", "Yesterday", or a date like "Mon, 17 Nov".
export function dayLabel(timestamp, now = new Date()) {
  const date = new Date(timestamp);
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((startOf(now) - startOf(date)) / 86400000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  return date.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
}
