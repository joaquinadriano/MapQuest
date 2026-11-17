// Tests for saved places and trip history (static/js/store.js).
// Run with: node --test tests/js/
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// store.js uses the browser's localStorage; give it a simple in-memory stand-in.
const memory = new Map();
globalThis.localStorage = {
  getItem: (key) => (memory.has(key) ? memory.get(key) : null),
  setItem: (key, value) => memory.set(key, String(value)),
  removeItem: (key) => memory.delete(key),
};

const store = await import('../../static/js/store.js');

const manila = { name: 'Intramuros, Manila', lat: 14.5895, lng: 120.9747 };
const makati = { name: 'Ayala, Makati', lat: 14.5547, lng: 121.0244 };
const bgc = { name: 'BGC, Taguig', lat: 14.5509, lng: 121.0503 };
const route = { distance_m: 9000, duration_ms: 900000 };

beforeEach(() => memory.clear());

test('saves a place and finds it again by location', () => {
  store.savePlace({ label: 'Office', kind: 'work', place: makati });
  assert.equal(store.getPlaces().length, 1);
  assert.equal(store.findSavedPlace({ ...makati, name: 'different text' }).label, 'Office');
});

test('only one Home: saving a new Home replaces the old one', () => {
  store.savePlace({ label: 'Home', kind: 'home', place: manila });
  store.savePlace({ label: 'New home', kind: 'home', place: bgc });
  const homes = store.getPlaces().filter((item) => item.kind === 'home');
  assert.equal(homes.length, 1);
  assert.equal(homes[0].label, 'New home');
});

test('saving the same spot twice keeps one entry', () => {
  store.savePlace({ label: 'Gym', kind: 'other', place: bgc });
  store.savePlace({ label: 'Gym 2', kind: 'other', place: bgc });
  assert.equal(store.getPlaces().length, 1);
});

test('Home and Work are listed first', () => {
  store.savePlace({ label: 'Gym', kind: 'other', place: bgc });
  store.savePlace({ label: 'Work', kind: 'work', place: makati });
  store.savePlace({ label: 'Home', kind: 'home', place: manila });
  assert.deepEqual(store.getPlaces().map((item) => item.kind), ['home', 'work', 'other']);
});

test('an empty name falls back to the type of place', () => {
  assert.equal(store.savePlace({ label: '  ', kind: 'school', place: bgc }).label, 'School');
});

test('removes a saved place', () => {
  const saved = store.savePlace({ label: 'Gym', kind: 'other', place: bgc });
  store.removePlace(saved.id);
  assert.equal(store.getPlaces().length, 0);
});

test('records trips with distance and time, newest first', () => {
  store.recordTrip({ origin: manila, destination: makati, mode: 'car', route });
  store.recordTrip({ origin: makati, destination: bgc, mode: 'foot', route: { distance_m: 3000, duration_ms: 2400000 } });
  const history = store.getHistory();
  assert.equal(history.length, 2);
  assert.equal(history[0].destination.name, bgc.name);
  assert.equal(history[1].distance_m, 9000);
});

test('switching mode for the same search updates the trip instead of adding one', () => {
  store.recordTrip({ origin: manila, destination: makati, mode: 'car', route });
  store.recordTrip({ origin: manila, destination: makati, mode: 'bike', route: { distance_m: 8000, duration_ms: 1800000 } });
  const history = store.getHistory();
  assert.equal(history.length, 1);
  assert.equal(history[0].mode, 'bike');
  assert.equal(history[0].distance_m, 8000);
});

test('the same trip on another day is kept as a separate entry', () => {
  store.recordTrip({ origin: manila, destination: makati, mode: 'car', route });
  const history = store.getHistory();
  history[0].createdAt -= 2 * 24 * 60 * 60 * 1000;
  localStorage.setItem('maply-history', JSON.stringify(history));
  store.recordTrip({ origin: manila, destination: makati, mode: 'car', route });
  assert.equal(store.getHistory().length, 2);
  assert.equal(store.recentTrips().length, 1, 'Recent shows each start/destination pair once');
});

test('keeps at most 50 trips', () => {
  for (let i = 0; i < 60; i += 1) {
    const destination = { name: `Stop ${i}`, lat: 14.5 + i / 1000, lng: 121.0 };
    store.recordTrip({ origin: manila, destination, mode: 'car', route });
  }
  assert.equal(store.getHistory().length, 50);
  assert.equal(store.getHistory()[0].destination.name, 'Stop 59');
});

test('older trips saved before this feature (no distance) still load', () => {
  localStorage.setItem('maply-history', JSON.stringify([{ id: 'old', origin: manila, destination: makati, mode: 'car', createdAt: 1 }]));
  assert.equal(store.getHistory()[0].id, 'old');
  assert.deepEqual(store.historyStats(store.getHistory()), { trips: 0, distance_m: 0 });
});

test('removes one trip and clears all', () => {
  const trip = store.recordTrip({ origin: manila, destination: makati, mode: 'car', route });
  store.recordTrip({ origin: makati, destination: bgc, mode: 'car', route });
  store.removeTrip(trip.id);
  assert.equal(store.getHistory().length, 1);
  store.clearHistory();
  assert.equal(store.getHistory().length, 0);
});

test('weekly stats count trips and distance from the last 7 days', () => {
  store.recordTrip({ origin: manila, destination: makati, mode: 'car', route });
  store.recordTrip({ origin: makati, destination: bgc, mode: 'car', route });
  assert.deepEqual(store.historyStats(store.getHistory()), { trips: 2, distance_m: 18000 });
});

test('broken saved data does not crash the app', () => {
  localStorage.setItem('maply-places', '{not json');
  localStorage.setItem('maply-history', '"a string"');
  assert.deepEqual(store.getPlaces(), []);
  assert.deepEqual(store.getHistory(), []);
});

test('day labels read Today, Yesterday, then the date', () => {
  const now = new Date(2026, 10, 17, 12, 0);
  assert.equal(store.dayLabel(new Date(2026, 10, 17, 8, 0).getTime(), now), 'Today');
  assert.equal(store.dayLabel(new Date(2026, 10, 16, 23, 0).getTime(), now), 'Yesterday');
  assert.match(store.dayLabel(new Date(2026, 10, 10).getTime(), now), /10/);
});
