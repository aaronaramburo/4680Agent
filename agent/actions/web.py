"""External API calls. Hosts are fixed here; the model only supplies search terms."""
import requests

from .. import config
from . import ActionError

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_WIKI_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
_WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_HEADERS = {"User-Agent": "StudyPilot/1.0 (CS4680 course project)"}

# WMO weather interpretation codes -> plain words
_WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers",
    86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


def get_weather(ws, city):
    places = _get_json(_GEOCODE_URL, {"name": city, "count": 1}).get("results")
    if not places:
        raise ActionError(f"Could not find a place called {city!r}.")
    place = places[0]
    data = _get_json(_FORECAST_URL, {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,wind_speed_10m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
        "forecast_days": 7,
    })
    current, daily = data["current"], data["daily"]
    return {
        "location": ", ".join(str(place[k]) for k in ("name", "admin1", "country") if place.get(k)),
        "current": {
            "temperature_f": current["temperature_2m"],
            "wind_mph": current["wind_speed_10m"],
            "conditions": _WEATHER_CODES.get(current["weather_code"], "unknown"),
        },
        "forecast": [
            {
                "date": day,
                "high_f": daily["temperature_2m_max"][i],
                "low_f": daily["temperature_2m_min"][i],
                "rain_chance_percent": daily["precipitation_probability_max"][i],
                "conditions": _WEATHER_CODES.get(daily["weather_code"][i], "unknown"),
            }
            for i, day in enumerate(daily["time"])
        ],
    }


def lookup_topic(ws, topic):
    hits = _get_json(_WIKI_SEARCH_URL, {
        "action": "query", "list": "search", "srsearch": topic, "srlimit": 1, "format": "json",
    })["query"]["search"]
    if not hits:
        raise ActionError(f"No encyclopedia article found for {topic!r}.")
    title = hits[0]["title"]
    page = _get_json(_WIKI_SUMMARY_URL + requests.utils.quote(title.replace(" ", "_"), safe=""))
    return {
        "title": page.get("title", title),
        "summary": page.get("extract", ""),
        "url": page.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }


def _get_json(url, params=None):
    try:
        response = requests.get(url, params=params, headers=_HEADERS, timeout=config.HTTP_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        raise ActionError("The external service took too long to respond.") from None
    except requests.HTTPError as e:
        raise ActionError(f"The external service returned an error ({e.response.status_code}).") from None
    except (requests.RequestException, ValueError):
        raise ActionError("Could not reach the external service.") from None
