# Maply

Maply turns the original GraphHopper-powered command-line route finder into a responsive Metro Manila route planner. Search for places, plan driving/cycling/walking routes, compare profile availability, review turn-by-turn directions, and estimate driving fuel costs. Serves as Final Exam Project for CCNA DevNet Associate

## Features

- GraphHopper geocoding suggestions and routing with real route geometry.
- Built for Metro Manila: place search only suggests places in Metro Manila, and routes must start and end there.
- Car, bike, and foot routing profiles, plus Commute mode. The app reports unavailable profiles without substituting another mode.
- Commute mode (OpenAI with web search): jeepney, bus (including the EDSA Carousel and P2P), UV Express, tricycle and LRT/MRT trips found in commuter guides on the web, with where to board and get off, signboard tips, times, transfers, walking and approximate fares, plus the sources used. LRT/MRT legs are checked against real station data (positions, direction, time and fare), and jeepney, bus and walking legs are drawn along real roads with GraphHopper (up to 12 requests per new search). Results appear after about 30 seconds while the other modes show right away, and are cached for a day.
- Built-in LRT/MRT planner when no OpenAI key is set (or the web search finds nothing usable): plans LRT-1, LRT-2 and MRT-3 trips (walk to the best station, ride, transfer at EDSA/Taft, Doroteo Jose/Recto or Cubao, walk to the destination) with estimated times and approximate fares, offline and free. Station data comes from OpenStreetMap; refresh it with `python scripts/build_rail_data.py`.
- Interactive Mapbox GL JS map with Mapbox Standard and satellite styles, route line, start/end markers, current-location control, and route bounds.
- Route distance, estimated duration, and turn-by-turn instructions.
- Mode comparison with per-profile error reporting.
- Alternative routes: up to three routes for the chosen mode, labelled by their main road (e.g. "via Taft Avenue"), shown in a list and as grey lines you can tap on the map.
- Metric/imperial display preference saved in this browser.
- Driving fuel estimate in PHP, with editable price and km/L efficiency.
- Weather planning (OpenWeatherMap): pick when to leave (now, later today, tomorrow or any time in the next 5 days) and see the weather when you leave and arrive, rain and heat advice for your travel mode (using PAGASA heat index bands), a suggested drier departure time, a 3-hourly forecast, and rain tags on Walk and Cycle.
- Saved places: star a start or destination to save it as Home, Work, School or a named place. Saved places appear when you tap an empty search box and can be used as a start or destination in one tap.
- Trip history: the last 50 trips with mode, distance and time, grouped by day, with search, weekly totals, one-tap re-planning and delete/clear.
- Saved places and history are stored in this browser's `localStorage`; no account or database required.
- Responsive mobile bottom sheet and desktop route-planning sidebar.

## Technology

Python 3, Flask, Requests, Python-dotenv, vanilla JavaScript, Tailwind CSS CDN, Mapbox GL JS, Lucide, and GraphHopper Directions API.

## Clone, configure, and run

### 1. Install the prerequisites

Install Git and Python 3.10 or newer. On Windows, install the Python launcher (`py`) with Python. You also need internet access because the application calls GraphHopper and loads Mapbox map tiles and frontend libraries from public CDNs.

You will need these API credentials:

- A GraphHopper API key that has Geocoding and Routing access. Available travel profiles depend on the account.
- A Mapbox **public** access token (starts with `pk.`) that can load styles and tiles. Restrict its allowed URLs to localhost for development.
- An OpenWeatherMap API key (the free plan is enough) for weather planning. Without it, everything else works and the Weather card explains how to add it. New keys can take up to 2 hours to activate.
- An OpenAI API key for Commute mode (`OPENAI_API_KEY`). Each new commute search is one OpenAI request with web search, which costs a little; answers are cached for a day, each user is rate-limited, and `COMMUTE_DAILY_LIMIT` (default 200) caps searches per day. `OPENAI_MODEL` picks the model (default `gpt-5.4-mini`). Without a key, Commute uses Maply's built-in LRT/MRT planner (trains only, estimated times and fares).

The credentials enable real place search, routes, and map tiles; the application does not include mock route data.

### 2. Clone the repository

In PowerShell, Terminal, or another shell, run:

```sh
git clone https://github.com/joaquinadriano/Maply.git
cd Maply
```

Cloning downloads the source code. Changing into the project directory ensures the following commands find `requirements.txt`, `.env.example`, and `app.py`.

### 3. Create and activate a virtual environment

**Windows PowerShell:**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```sh
python3 -m venv .venv
source .venv/bin/activate
```

A virtual environment keeps Maply's Python packages separate from system Python and other projects. Activate it again whenever you open a new terminal to work on Maply.

### 4. Install Python dependencies

With the virtual environment active, run:

```sh
python -m pip install -r requirements.txt
```

This installs Flask, Requests, and Python-dotenv, which the server needs to serve the app, contact GraphHopper, and read local configuration.

### 5. Configure API credentials

Copy the example environment file to `.env`:

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux:**

```sh
cp .env.example .env
```

Open `.env` in a text editor and replace the placeholder values:

```dotenv
GRAPHHOPPER_API_KEY=your_real_graphhopper_key
MAPBOX_ACCESS_TOKEN=pk.your_public_mapbox_token
```

The GraphHopper key stays on the Flask server and is not sent to the browser. Mapbox GL JS requires its public token in the browser, so use a `pk.` token and restrict it to your local development URL. `.env` is ignored by Git so your credentials are not accidentally committed. Do not put secrets in `.env.example`.

If the Mapbox token is missing, the server can still calculate routes, but the map displays setup instructions. GraphHopper credentials are required for place search and route calculations.

### 6. Start Maply

From the project directory with the virtual environment active, run:

```sh
python app.py
```

The Flask development server reads `.env` and listens on localhost at port 5000 by default. Keep this terminal open while using the app; closing it stops the server. To stop it intentionally, press **Ctrl+C**.

### 7. Open the app

In your browser, open [http://127.0.0.1:5000](http://127.0.0.1:5000). This address points to the server running on your own computer and is not a public website. If you change `PORT` in `.env`, use that port in the browser address too. After editing `.env`, restart the server so it loads the new values.

### Troubleshooting

- **`python` or `py` is not recognized:** Install Python and ensure it is on your PATH. On macOS/Linux, use `python3` to create the virtual environment.
- **`ModuleNotFoundError` on startup:** Activate `.venv` and run `python -m pip install -r requirements.txt` again in the project directory.
- **Mapbox setup message:** Confirm `.env` contains a valid public `MAPBOX_ACCESS_TOKEN`, then restart Flask. Check that the token's allowed URL includes your local address.
- **Place search or routing fails:** Confirm the GraphHopper key is set and that the key/account supports the selected profile. The app reports provider errors instead of substituting a different mode.
- **Port 5000 is already in use:** Set a different `PORT` in `.env`, restart the app, and open `http://127.0.0.1:<port>`.

## API integrations

- `GET /api/geocode?q=...` requests up to five GraphHopper place suggestions.
- `POST /api/route` calculates the selected profile route. Returns meter distance, millisecond duration, coordinates, turn-by-turn instructions, and enriched metric/imperial summaries with fuel estimates.
- `POST /api/compare` queries car, bike, and foot profiles concurrently using Python thread workers, returning profile availability and metrics.

GraphHopper requests are made by the Flask server so the GraphHopper key is not sent to the browser. Mapbox GL JS loads Mapbox Standard in the browser using the public Mapbox token. Requests have connect/read timeouts, input validation, and a per-client in-memory limit of 20 weighted GraphHopper requests per minute (mode comparison counts as three, alternative routes as two, a commute search as two). Commute answers are cached for a day and weather for 10 minutes.

## Testing

Run the unit and endpoint tests with:

```powershell
python -m unittest discover -s tests -v
```

Run the JavaScript tests (saved places and trip history) with Node.js 20 or newer:

```powershell
node --test "tests/js/*.test.mjs"
```

The tests mock external services and do not consume API credits. A live route search also requires valid GraphHopper credentials and network access.

## Limitations

- The GraphHopper account determines which profiles and geographies are available. The UI displays API errors per profile.
- Map styles and frontend libraries load from their respective public services; this app does not bundle or proxy those assets.
- Fuel estimates use route distance and configured average efficiency. They exclude tolls, parking fees, and changing traffic conditions.
- Recent trips and unit preference live in browser storage on the current browser/device and are not synchronized.
- A single best route is displayed for the selected profile; GraphHopper alternative-route options are not enabled.
