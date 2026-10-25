import os
import urllib.parse
import requests
from dotenv import load_dotenv

from services.converters import meters_to_km, meters_to_miles

load_dotenv()
route_url = "https://graphhopper.com/api/1/route?"
key = os.getenv("GRAPHHOPPER_API_KEY", "").strip()
if not key:
    raise RuntimeError("Set GRAPHHOPPER_API_KEY in your .env file before starting the CLI.")

TIMEOUT = (4, 16)


def geocoding(location, key):
    while location == "":
        location = input("Enter the location again: ")

    geocode_url = "https://graphhopper.com/api/1/geocode?"
    url = geocode_url + urllib.parse.urlencode({"q": location, "limit": "1", "key": key})

    try:
        replydata = requests.get(url, timeout=TIMEOUT)
        json_status = replydata.status_code
        json_data = replydata.json()
    except requests.RequestException as exc:
        print("Geocoding connection error: " + str(exc))
        return 0, "null", "null", location

    if json_status == 200 and len(json_data.get("hits", [])) != 0:
        hit = json_data["hits"][0]
        lat = hit["point"]["lat"]
        lng = hit["point"]["lng"]
        name = hit["name"]
        value = hit.get("osm_value", "location")

        country = hit.get("country", "")
        state = hit.get("state", "")

        if len(state) != 0 and len(country) != 0:
            new_loc = name + ", " + state + ", " + country
        elif len(state) != 0:
            new_loc = name + ", " + country
        else:
            new_loc = name

        print("Geocoding API URL for " + new_loc + " (Location Type: " + value + ")\n" + url)
    else:
        lat = "null"
        lng = "null"
        new_loc = location
        if json_status != 200:
            err_msg = json_data.get("message", "Unknown error")
            print("Geocode API status: " + str(json_status) + "\nError message: " + err_msg)

    return json_status, lat, lng, new_loc


while True:
    print("\n+++++++++++++++++++++++++++++++++++++++++++++")
    print("Vehicle profiles available on Graphhopper:")
    print("+++++++++++++++++++++++++++++++++++++++++++++")
    print("car, bike, foot")
    print("+++++++++++++++++++++++++++++++++++++++++++++")
    profile = ["car", "bike", "foot"]
    vehicle = input("Enter a vehicle profile from the list above: ")
    if vehicle == "quit" or vehicle == "q":
        break
    elif vehicle in profile:
        vehicle = vehicle
    else:
        vehicle = "car"
        print("No valid vehicle profile was entered. Using the car profile.")

    loc1 = input("Starting Location: ")
    if loc1 == "quit" or loc1 == "q":
        break
    orig = geocoding(loc1, key)

    loc2 = input("Destination: ")
    if loc2 == "quit" or loc2 == "q":
        break
    dest = geocoding(loc2, key)

    print("=================================================")
    if orig[0] == 200 and dest[0] == 200:
        op = "&point=" + str(orig[1]) + "%2C" + str(orig[2])
        dp = "&point=" + str(dest[1]) + "%2C" + str(dest[2])
        paths_url = route_url + urllib.parse.urlencode({"key": key, "vehicle": vehicle}) + op + dp

        try:
            paths_res = requests.get(paths_url, timeout=TIMEOUT)
            paths_status = paths_res.status_code
            paths_data = paths_res.json()
        except requests.RequestException as exc:
            print("Routing request failed: " + str(exc))
            continue

        print("Routing API Status: " + str(paths_status) + "\nRouting API URL:\n" + paths_url)
        print("=================================================")
        print("Directions from " + orig[3] + " to " + dest[3] + " by " + vehicle)
        print("=================================================")

        if paths_status == 200:
            raw_distance = paths_data["paths"][0]["distance"]
            raw_time = paths_data["paths"][0]["time"]

            miles = meters_to_miles(raw_distance, 1)
            km = meters_to_km(raw_distance, 1)
            sec = int(raw_time / 1000 % 60)
            min = int(raw_time / 1000 / 60 % 60)
            hr = int(raw_time / 1000 / 60 / 60)

            print("Distance Traveled: {0:.1f} miles / {1:.1f} km".format(miles, km))
            print("Trip Duration: {0:02d}:{1:02d}:{2:02d}".format(hr, min, sec))
            print("=================================================")

            for step in paths_data["paths"][0].get("instructions", []):
                path = step.get("text", "Continue")
                dist = step.get("distance", 0)
                print("{0} ( {1:.1f} km / {2:.1f} miles )".format(path, meters_to_km(dist, 1), meters_to_miles(dist, 1)))

            print("=================================================")
        else:
            err_msg = paths_data.get("message", "Route calculation error")
            print("Error message: " + err_msg)
            print("*************************************************")

