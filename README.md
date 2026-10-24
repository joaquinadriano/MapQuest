# MapQuest

MapQuest turns the original GraphHopper-powered command-line route finder into a responsive browser app. Search for places, plan driving/cycling/walking routes, compare profile availability, review turn-by-turn directions, and estimate driving fuel costs.

## Features

- GraphHopper geocoding suggestions and routing with real route geometry.
- Car, bike, and foot routing profiles. The app reports unavailable profiles without substituting another mode.
- Interactive Mapbox GL JS map with Mapbox Standard and satellite styles, route line, start/end markers, current-location control, and route bounds.
- Route distance, estimated duration, and turn-by-turn instructions.
- Mode comparison with per-profile error reporting.
- Metric/imperial display preference, saved in this browser.
- Driving fuel estimate in PHP, with editable price and km/L efficiency.
- Saved routes and recent trips stored in browser `localStorage`; no account or database required.
- Responsive mobile bottom sheet and desktop route-planning sidebar.

## Technology

Python 3, Flask, Requests, Python-dotenv, vanilla JavaScript, Tailwind CSS CDN, Mapbox GL JS, Lucide, and GraphHopper Directions API.

## Prerequisites

- Python 3.9 or newer.
- A GraphHopper API key with Geocoding and Routing API access. The key must allow the profiles you intend to use.
- A Mapbox public access token (starts with `pk.`) with styles/tiles access. Restrict its allowed URLs to localhost during development.
- Internet access for GraphHopper, map tiles, and the frontend CDN assets.

## Install and run

From this folder, create and activate a virtual environment, then install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from `.env.example` and set `GRAPHHOPPER_API_KEY` and `MAPBOX_ACCESS_TOKEN`. `.env` is ignored by Git. The public Mapbox token is rendered into the page because Mapbox GL JS uses it in the browser; restrict the token to your local development URL. Without a Mapbox token, route calculations still work but the map area shows setup instructions. Then start the app:

```powershell
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The application binds to localhost by default. Set `PORT` to change the port. `FLASK_DEBUG` defaults to `false`; enable it only during local development.

## API integrations

- `GET /api/geocode?q=...` requests up to five GraphHopper place suggestions.
- `POST /api/route` calculates the selected profile route. The API returns meter distance, millisecond duration, coordinates, and instructions.
- `POST /api/compare` attempts car, bike, and foot independently and includes unavailable profile explanations.

GraphHopper requests are made by the Flask server so the GraphHopper key is not sent to the browser. Mapbox GL JS loads Mapbox Standard in the browser using the public Mapbox token. Requests have connect/read timeouts, input validation, and a per-client in-memory limit of 20 weighted GraphHopper requests per minute (mode comparison counts as three).

## Testing

Run the unit and endpoint tests with:

```powershell
python -m unittest discover -s tests -v
```

The tests mock external services and do not consume API credits. A live route search also requires valid GraphHopper credentials and network access.

## Limitations

- The GraphHopper account determines which profiles and geographies are available. The UI displays API errors per profile.
- Map styles and frontend libraries load from their respective public services; this app does not bundle or proxy those assets.
- Fuel estimates use route distance and configured average efficiency. They exclude tolls, parking fees, and changing traffic conditions.
- Favorites and recent trips live in browser storage on the current browser/device and are not synchronized.
- A single best route is displayed for the selected profile; GraphHopper alternative-route options are not enabled.
