// Weather planning: turns an OpenWeatherMap forecast into advice for a trip.
// Pure functions only (no page access) so they can be tested in Node.

const HOUR = 60 * 60 * 1000;
const SLOT = 3 * HOUR; // OpenWeatherMap's free forecast comes in 3-hour steps.

// The reading closest to a time: current conditions for the next 90 minutes,
// otherwise the nearest 3-hour forecast. Null when the time is past the forecast.
export function readingAt(weather, time, now = Date.now()) {
  if (!weather) return null;
  if (weather.current && Math.abs(time - now) <= 1.5 * HOUR) return weather.current;
  const forecast = weather.forecast || [];
  if (!forecast.length) return null;
  let best = null;
  for (const slot of forecast) {
    if (!best || Math.abs(slot.time - time) < Math.abs(best.time - time)) best = slot;
  }
  return Math.abs(best.time - time) <= SLOT ? best : null;
}

// Rain level for one reading: 'heavy', 'likely', 'possible' or 'low'.
export function rainRisk(reading) {
  if (!reading) return 'low';
  const group = Math.floor(reading.code / 100);
  if (group === 2 || reading.rain_mm >= 7.5 || (reading.code >= 502 && reading.code <= 531)) return 'heavy';
  if (reading.pop >= 0.5 || group === 5 || group === 3) return 'likely';
  if (reading.pop >= 0.25) return 'possible';
  return 'low';
}

// PAGASA heat index bands: 33-41 °C extreme caution, 42 °C and up danger.
export function heatRisk(reading) {
  if (!reading) return null;
  if (reading.feels_like_c >= 42) return 'danger';
  if (reading.feels_like_c >= 33) return 'caution';
  return null;
}

const RAIN_ORDER = { low: 0, possible: 1, likely: 2, heavy: 3 };
const worstRain = (readings) => readings.reduce((worst, reading) => (RAIN_ORDER[rainRisk(reading)] > RAIN_ORDER[worst] ? rainRisk(reading) : worst), 'low');

const timeText = (time) => new Date(time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

// Short advice for the trip, most important first. Each item: { level: 'warn' | 'info', text }.
export function tripAdvice({ weather, mode, depart, arrive, now = Date.now() }) {
  const start = readingAt(weather, depart, now);
  const end = readingAt(weather, arrive, now);
  const readings = [start, end].filter(Boolean);
  if (!readings.length) return [];
  const exposed = mode === 'foot' || mode === 'bike';
  const rain = worstRain(readings);
  const hottest = readings.reduce((max, reading) => Math.max(max, reading.feels_like_c), -Infinity);
  const heat = heatRisk({ feels_like_c: hottest });
  const advice = [];

  if (rain === 'heavy') {
    advice.push({ level: 'warn', text: exposed
      ? 'Heavy rain or thunderstorms are expected. Streets may flood, so walking or cycling is not advised. Consider driving or another time.'
      : 'Heavy rain or thunderstorms are expected. Allow extra time and avoid flooded roads.' });
  } else if (rain === 'likely') {
    advice.push({ level: exposed ? 'warn' : 'info', text: exposed ? 'Rain is likely during your trip. Bring an umbrella or rain gear.' : 'Rain is likely during your trip. Traffic may be slower than usual.' });
  } else if (rain === 'possible' && exposed) {
    advice.push({ level: 'info', text: 'There is a chance of rain. An umbrella is a good idea.' });
  }

  if (heat === 'danger') {
    advice.push({ level: 'warn', text: `It will feel like ${Math.round(hottest)}°C. ${exposed ? 'Avoid long walks or rides in the sun; bring water and rest in the shade.' : 'Stay hydrated and avoid waiting outside.'}` });
  } else if (heat === 'caution' && exposed) {
    advice.push({ level: 'info', text: `It will feel like ${Math.round(hottest)}°C. Bring water and keep to the shade.` });
  }

  if (!advice.length) advice.push({ level: 'info', text: 'Good weather for this trip.' });
  return advice;
}

// The departure in the next `hours` hours with the lowest chance of rain during the trip.
// Returns null unless it is clearly drier (at least 20 points lower) than leaving at `from`.
export function driestDeparture({ weather, from, durationMs, hours = 12, now = Date.now() }) {
  if (!weather?.forecast?.length) return null;
  const tripPop = (depart) => {
    const readings = [readingAt(weather, depart, now), readingAt(weather, depart + durationMs, now)].filter(Boolean);
    if (!readings.length) return null;
    const heavy = readings.some((reading) => rainRisk(reading) === 'heavy') ? 0.3 : 0;
    return Math.min(1, Math.max(...readings.map((reading) => reading.pop)) + heavy);
  };
  const leavingNow = tripPop(from);
  if (leavingNow === null || leavingNow < 0.3) return null;
  let best = null;
  for (const slot of weather.forecast) {
    if (slot.time <= from || slot.time > from + hours * HOUR) continue;
    const pop = tripPop(slot.time);
    if (pop !== null && (!best || pop < best.pop)) best = { time: slot.time, pop };
  }
  if (!best || leavingNow - best.pop < 0.2) return null;
  return { ...best, currentPop: leavingNow, label: timeText(best.time) };
}

// The next few forecast steps from a time, for the hourly strip.
export function upcoming(weather, from, count = 6) {
  return (weather?.forecast || []).filter((slot) => slot.time >= from - SLOT / 2).slice(0, count);
}

export function conditionIcon(reading) {
  if (!reading) return 'cloud';
  const { code, is_day: isDay } = reading;
  if (code >= 200 && code < 300) return 'cloud-lightning';
  if (code >= 300 && code < 400) return 'cloud-drizzle';
  if (code >= 502 && code < 600) return 'cloud-rain-wind';
  if (code >= 500 && code < 600) return 'cloud-rain';
  if (code >= 600 && code < 700) return 'snowflake';
  if (code >= 700 && code < 800) return 'cloud-fog';
  if (code === 800) return isDay ? 'sun' : 'moon';
  if (code <= 802) return isDay ? 'cloud-sun' : 'cloud-moon';
  return 'cloud';
}

export const formatTemp = (celsius, unit = 'metric') => (unit === 'imperial' ? `${Math.round(celsius * 9 / 5 + 32)}°F` : `${Math.round(celsius)}°C`);
export const rainChance = (reading) => `${Math.round((reading?.pop || 0) * 100)}%`;
