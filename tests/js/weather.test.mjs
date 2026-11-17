// Tests for weather planning (static/js/weather.js).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as w from '../../static/js/weather.js';

const HOUR = 60 * 60 * 1000;
const NOW = Date.UTC(2026, 10, 18, 6, 0); // 2 PM in Manila

const reading = (overrides = {}) => ({ time: NOW, temp_c: 30, feels_like_c: 32, rain_mm: 0, pop: 0, code: 800, is_day: true, ...overrides });
// Forecast every 3 hours from now; `pops` sets the chance of rain for each step.
const forecastWith = (pops, extra = {}) => pops.map((pop, i) => reading({ time: NOW + i * 3 * HOUR, pop, code: pop >= 0.5 ? 500 : 800, ...extra }));
const weather = (pops, extra) => ({ current: reading({ pop: 0 }), forecast: forecastWith(pops, extra) });

test('uses current weather for the next 90 minutes, then the forecast', () => {
  const data = { current: reading({ temp_c: 31 }), forecast: forecastWith([0, 0.8]) };
  assert.equal(w.readingAt(data, NOW + HOUR, NOW).temp_c, 31);
  assert.equal(w.readingAt(data, NOW + 3 * HOUR, NOW).pop, 0.8);
});

test('has no reading past the end of the forecast', () => {
  assert.equal(w.readingAt(weather([0, 0]), NOW + 24 * HOUR, NOW), null);
});

test('rates rain from codes, rainfall and chance of rain', () => {
  assert.equal(w.rainRisk(reading({ code: 211 })), 'heavy');
  assert.equal(w.rainRisk(reading({ code: 502 })), 'heavy');
  assert.equal(w.rainRisk(reading({ code: 500, rain_mm: 8 })), 'heavy');
  assert.equal(w.rainRisk(reading({ code: 500, pop: 0.4 })), 'likely');
  assert.equal(w.rainRisk(reading({ pop: 0.6 })), 'likely');
  assert.equal(w.rainRisk(reading({ pop: 0.3 })), 'possible');
  assert.equal(w.rainRisk(reading({ pop: 0.1 })), 'low');
});

test('uses PAGASA heat index bands', () => {
  assert.equal(w.heatRisk(reading({ feels_like_c: 32 })), null);
  assert.equal(w.heatRisk(reading({ feels_like_c: 35 })), 'caution');
  assert.equal(w.heatRisk(reading({ feels_like_c: 44 })), 'danger');
});

test('warns walkers about heavy rain and flooding', () => {
  const data = weather([0.9, 0.9], { code: 502 });
  const advice = w.tripAdvice({ weather: data, mode: 'foot', depart: NOW + 3 * HOUR, arrive: NOW + 4 * HOUR, now: NOW });
  assert.equal(advice[0].level, 'warn');
  assert.match(advice[0].text, /not advised/);
});

test('drivers get a milder rain note', () => {
  const advice = w.tripAdvice({ weather: weather([0, 0.7]), mode: 'car', depart: NOW + 3 * HOUR, arrive: NOW + 3.5 * HOUR, now: NOW });
  assert.equal(advice[0].level, 'info');
  assert.match(advice[0].text, /Traffic/);
});

test('heat advice for cyclists, not for drivers in mild heat', () => {
  const hot = { current: reading({ feels_like_c: 38 }), forecast: forecastWith([0, 0], { feels_like_c: 38 }) };
  assert.match(w.tripAdvice({ weather: hot, mode: 'bike', depart: NOW, arrive: NOW + HOUR, now: NOW })[0].text, /feel like 38°C/);
  assert.deepEqual(w.tripAdvice({ weather: hot, mode: 'car', depart: NOW, arrive: NOW + HOUR, now: NOW }).map((a) => a.text), ['Good weather for this trip.']);
});

test('no advice when there is no forecast for the trip time', () => {
  assert.deepEqual(w.tripAdvice({ weather: { forecast: [] }, mode: 'car', depart: NOW + 48 * HOUR, arrive: NOW + 49 * HOUR, now: NOW }), []);
});

test('suggests a drier time to leave', () => {
  // Rainy for the next 6 hours, then dry.
  const data = { current: reading({ pop: 0.9, code: 500 }), forecast: forecastWith([0.9, 0.8, 0.1, 0.1]) };
  const best = w.driestDeparture({ weather: data, from: NOW, durationMs: HOUR, now: NOW });
  assert.equal(best.time, NOW + 6 * HOUR);
  assert.equal(best.pop, 0.1);
});

test('no suggestion when leaving now is already dry, or nothing is clearly better', () => {
  assert.equal(w.driestDeparture({ weather: weather([0.1, 0.8]), from: NOW, durationMs: HOUR, now: NOW }), null);
  const allWet = { current: reading({ pop: 0.8, code: 500 }), forecast: forecastWith([0.8, 0.75, 0.7]) };
  assert.equal(w.driestDeparture({ weather: allWet, from: NOW, durationMs: HOUR, now: NOW }), null);
});

test('icons follow the condition and time of day', () => {
  assert.equal(w.conditionIcon(reading({ code: 800, is_day: false })), 'moon');
  assert.equal(w.conditionIcon(reading({ code: 501 })), 'cloud-rain');
  assert.equal(w.conditionIcon(reading({ code: 202 })), 'cloud-lightning');
  assert.equal(w.conditionIcon(reading({ code: 804 })), 'cloud');
});

test('formats temperature and rain chance', () => {
  assert.equal(w.formatTemp(30), '30°C');
  assert.equal(w.formatTemp(30, 'imperial'), '86°F');
  assert.equal(w.rainChance(reading({ pop: 0.47 })), '47%');
});

test('commuters get advice about walking and waiting at stops', () => {
  const rainy = w.tripAdvice({ weather: weather([0, 0.7]), mode: 'commute', depart: NOW + 3 * HOUR, arrive: NOW + 3.5 * HOUR, now: NOW });
  assert.match(rainy[0].text, /waiting at stops/);
  const storm = w.tripAdvice({ weather: weather([0.9, 0.9], { code: 211 }), mode: 'commute', depart: NOW + 3 * HOUR, arrive: NOW + 4 * HOUR, now: NOW });
  assert.equal(storm[0].level, 'warn');
  assert.match(storm[0].text, /fewer jeeps/);
});
