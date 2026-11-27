
import os
import html
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import requests
from urllib.parse import parse_qs, quote_plus
from html.parser import HTMLParser
import xml.etree.ElementTree as ET

LATITUDE = 39.7857
LONGITUDE = -75.9788
TIMEZONE = "America/New_York"

class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.capture = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()

        if self.capture:
            self.depth += 1
            return

        if tag == "a" and "result__a" in classes:
            item = {
                "title": "",
                "url": attrs.get("href", ""),
                "snippet": ""
            }
            self.results.append(item)
            self.capture = (item, "title")
            self.depth = 1

        elif "result__snippet" in classes and self.results:
            self.capture = (self.results[-1], "snippet")
            self.depth = 1

    def handle_data(self, data):
        if self.capture:
            item, field = self.capture
            item[field] += data

    def handle_endtag(self, tag):
        if self.capture:
            self.depth -= 1
            if self.depth <= 0:
                self.capture = None
                self.depth = 0
            
NWS_HEADERS = {
    "User-Agent": "YealinkWeather/1.0 (weather display application)",
    "Accept": "application/geo+json"
}

CACHE_SECONDS = 900
cache = {}
cache_lock = threading.Lock()

session = requests.Session()
session.headers.update(NWS_HEADERS)


def search_results_screen(query, host, page=1):
    if not query.strip():
        return xml_screen("Search", ["Please enter search words."])

    try:
        response = requests.get(
            "https://www.bing.com/search",
            params={
                "q": query,
                "format": "rss",
                "first": 1
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        response.raise_for_status()

        root = ET.fromstring(response.content)
        items = root.findall(".//item")

        menu = [
            '<?xml version="1.0" encoding="ISO-8859-1"?>',
            '<YealinkIPPhoneTextMenu>',
            '<Title>Search Results</Title>'
        ]

        if not items:
            menu.append(
                '<MenuItem>'
                '<Prompt>No results found</Prompt>'
                '<URI>https://www.bing.com</URI>'
                '</MenuItem>'
            )
        else:
            for item in items[(page - 1) * 5:page * 5]:
                title = item.findtext("title", "Untitled")
                link = item.findtext("link", "")

                if not link.startswith(("http://", "https://")):
                    continue

                menu.append(
                    '<MenuItem>'
                    f'<Prompt>{html.escape(title[:70])}</Prompt>'
                    f'<URI>{html.escape(link)}</URI>'
                    '</MenuItem>'
                )

            if len(items) > page * 5:
                menu.append(
                    '<MenuItem>'
                    '<Prompt>More Results</Prompt>'
                    f'<URI>https://{host}/search-results?q={quote_plus(query)}&amp;page={page + 1}</URI>'
                    '</MenuItem>'
                )

        menu.append(
            '<MenuItem>'
            '<Prompt>Search Again</Prompt>'
            f'<URI>https://{host}/search</URI>'
            '</MenuItem>'
        )

        menu.append(
            '<MenuItem>'
            '<Prompt>Main Menu</Prompt>'
            f'<URI>https://{host}/</URI>'
            '</MenuItem>'
        )

        menu.append('</YealinkIPPhoneTextMenu>')

        return "".join(menu)

    except Exception as error:
        logging.exception("Bing search failed")
        return xml_screen(
            "Search Error",
            [f"{type(error).__name__}: {str(error)[:120]}"]
        )
        
def fetch_json(url):
    response = session.get(url, timeout=25)
    response.raise_for_status()
    return response.json()


def cached_json(key, url, seconds=CACHE_SECONDS):
    now = time.time()

    with cache_lock:
        item = cache.get(key)
        if item and now - item["time"] < seconds:
            return item["data"]

    data = fetch_json(url)

    with cache_lock:
        cache[key] = {"time": now, "data": data}

    return data


def get_weather():
    point_url = (
        f"https://api.weather.gov/points/"
        f"{LATITUDE},{LONGITUDE}"
    )

    point = cached_json("point", point_url, 604800)
    properties = point["properties"]

    hourly_url = properties["forecastHourly"]
    daily_url = properties["forecast"]
    extended_url = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=39.7857"
    "&longitude=-75.9788"
    "&timezone=America%2FNew_York"
    "&temperature_unit=fahrenheit"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max"
    "&forecast_days=14"
)

    hourly = cached_json("hourly", hourly_url)
    daily = cached_json("daily", daily_url)

    stations_url = properties["observationStations"]
    stations = cached_json("stations", stations_url, 604800)

    features = stations.get("features", [])
    if not features:
        raise RuntimeError("No nearby observation stations found")

    station_id = features[0]["properties"]["stationIdentifier"]
    observation_url = (
        f"https://api.weather.gov/stations/"
        f"{station_id}/observations/latest"
    )

    observation = cached_json(
        "observation",
        observation_url,
        600
    )

    return {
    "hourly": hourly["properties"]["periods"],
    "daily": daily["properties"]["periods"],
    "current": observation["properties"],
    "station": station_id
}
    
def get_extended_forecast():
    extended_url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=39.7857"
        "&longitude=-75.9788"
        "&timezone=America%2FNew_York"
        "&temperature_unit=fahrenheit"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max"
        "&forecast_days=14"
    )

    return cached_json(
        "extended",
        extended_url,
        86400
    )

def celsius_to_fahrenheit(value):
    if value is None:
        return None
    return value * 9 / 5 + 32


def meters_to_mph(value):
    if value is None:
        return None
    return value * 2.236936


def direction(degrees):
    if degrees is None:
        return "N/A"

    directions = [
        "N", "NNE", "NE", "ENE", "E", "ESE",
        "SE", "SSE", "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW"
    ]

    return directions[int((degrees + 11.25) / 22.5) % 16]


def safe_number(value, digits=1):
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def xml_screen(title, lines):
    safe_title = html.escape(str(title))
    safe_text = html.escape("\n".join(lines))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<YealinkIPPhoneTextScreen>'
        f'<Title>{safe_title}</Title>'
        f'<Text>{safe_text}</Text>'
        '</YealinkIPPhoneTextScreen>'
    )
    
def search_screen(host):
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<YealinkIPPhoneInputScreen type="string" '
        'Beep="no" Timeout="0" LockIn="no">'
        '<Title>Search</Title>'
        f'<URL>https://{host}/search-results</URL>'
        '<InputField type="string" editable="yes">'
        '<Prompt>Enter search words</Prompt>'
        '<Parameter>q</Parameter>'
        '</InputField>'
        '</YealinkIPPhoneInputScreen>'
    )
     

def calculator_screen():
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<YealinkIPPhoneInputScreen type="string" '
        'Beep="no" Timeout="0" LockIn="no">'
        '<Title>Calculator</Title>'
        '<URL>https://yealink-weather.onrender.com/calculate</URL>'
        '<InputField type="string" editable="yes">'
        '<Prompt>Example: 25+10</Prompt>'
        '<Parameter>expression</Parameter>'
        '</InputField>'
        '</YealinkIPPhoneInputScreen>'
    )

def main_menu(host):
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<YealinkIPPhoneTextMenu>'
        '<Title>MY PHONE</Title>'

        '<MenuItem>'
        '<Prompt>Weather</Prompt>'
        f'<URI>https://{host}/weather</URI>'
        '</MenuItem>'

        '<MenuItem>'
        '<Prompt>Search</Prompt>'
        f'<URI>https://{host}/search</URI>'
        '</MenuItem>'

        '<MenuItem>'
        '<Prompt>Calculator</Prompt>'
        '<URI>https://www.google.com/search?q=calculator</URI>'
        '</MenuItem>'

        '<MenuItem>'
        '<Prompt>Dictionary</Prompt>'
        '<URI>https://www.dictionary.com/</URI>'
        '</MenuItem>'

        '<MenuItem>'
        '<Prompt>YouTube</Prompt>'
        '<URI>https://www.youtube.com/</URI>'
        '</MenuItem>'

        '<MenuItem>'
        '<Prompt>Maps</Prompt>'
        '<URI>https://www.google.com/maps</URI>'
        '</MenuItem>'

        '</YealinkIPPhoneTextMenu>'
    )
    
def weather_menu(host):
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<YealinkIPPhoneTextMenu>'
        '<Title>Oxford PA Weather</Title>'
        '<MenuItem><Prompt>Current Conditions</Prompt>'
        f'<URI>https://{host}/current</URI></MenuItem>'
        '<MenuItem><Prompt>24 Hour Forecast</Prompt>'
        f'<URI>https://{host}/hourly</URI></MenuItem>'
        '<MenuItem><Prompt>7 Day Forecast</Prompt>'
        f'<URI>https://{host}/daily</URI></MenuItem>'
        '<MenuItem><Prompt>Weather Alerts</Prompt>'
        f'<URI>https://{host}/alerts</URI></MenuItem>'
        '</YealinkIPPhoneTextMenu>'
    )
    
def weather_symbol(description):
    text = description.lower()

    if "thunder" in text or "storm" in text:
        return "!"
    elif "snow" in text or "sleet" in text:
        return "+"
    elif "rain" in text or "shower" in text or "drizzle" in text:
        return "/"
    elif "fog" in text:
        return "~"
    elif "cloud" in text:
        if "partly" in text or "mostly" in text:
            return "*"
        return "~"
    elif "sun" in text or "clear" in text:
        return "*"

    return "-"
    
def current_screen(weather):
    current = weather["current"]

    temperature = celsius_to_fahrenheit(
        current.get("temperature", {}).get("value")
    )

    feels = celsius_to_fahrenheit(
        current.get("heatIndex", {}).get("value")
    )

    if feels is None:
        feels = celsius_to_fahrenheit(
            current.get("windChill", {}).get("value")
        )

    humidity = current.get("relativeHumidity", {}).get("value")

    wind = meters_to_mph(
        current.get("windSpeed", {}).get("value")
    )

    gust = meters_to_mph(
        (current.get("windGust") or {}).get("value")
    )

    description = current.get("textDescription")

    if not description or description.lower() == "unknown":
        description = weather["hourly"][0].get("shortForecast", "Unknown")

    wind_direction = current.get("windDirection", {}).get("value")

    observed = current.get("timestamp", "")
    if observed:
        try:
            observed = datetime.fromisoformat(
                observed.replace("Z", "+00:00")
            ).astimezone(ZoneInfo(TIMEZONE)).strftime("%I:%M %p")
        except ValueError:
            pass

    lines = [
        "Oxford, Pennsylvania",
        "",
        f"{weather_symbol(description)} {description}",
        f"Temperature: {safe_number(temperature)} F",
        f"Feels Like: {safe_number(feels)} F",
        f"Humidity: {safe_number(humidity, 0)}%",
        f"Wind: {direction(wind_direction)} {safe_number(wind)} mph",
        f"Gusts: {safe_number(gust)} mph",
        "",
        f"Station: {weather['station']}",
        f"Observed: {observed}"
    ]

    return xml_screen("Current Conditions", lines)
    
def hourly_screen(weather):
    periods = weather["hourly"][:24]
    lines = ["Oxford PA - Hourly Forecast", ""]

    for period in periods:
        start = datetime.fromisoformat(
            period["startTime"].replace("Z", "+00:00")
        ).astimezone(ZoneInfo(TIMEZONE))

        label = start.strftime("%a %I %p").replace(" 0", " ")
        temperature = period.get("temperature")
        description = period.get("shortForecast", "Unknown")

        probability = (
            period.get("probabilityOfPrecipitation") or {}
        ).get("value")

        wind = period.get("windSpeed", "N/A")
        wind_direction = period.get("windDirection", "N/A")
        gust = period.get("windGust")
        print("HOURLY GUST:", period.get("windGust"))
        symbol = weather_symbol(description)

        lines.append(
            f"{label}: {temperature} F"
        )
        lines.append(
            f"{symbol} {description} | Rain {probability if probability is not None else 'N/A'}%"
        )
        lines.append(
            f"Wind: {wind_direction} {wind}"
        )

        if gust:
            lines.append(f"Gusts: {gust}")

        lines.append("")

    return xml_screen("24 Hour Forecast", lines)
    
def daily_screen(weather):
    periods = weather["daily"]
    grouped = {}

    for period in periods:
        date = period["startTime"][:10]

        if date not in grouped:
            grouped[date] = {}

        if period.get("isDaytime"):
            grouped[date]["day"] = period
        else:
            grouped[date]["night"] = period

    lines = ["Oxford, Pennsylvania", ""]

    for date, values in list(grouped.items())[:7]:
        day = values.get("day")
        night = values.get("night")

        label = datetime.fromisoformat(date).strftime("%a %b %d")

        if day:
            description = day.get("shortForecast", "Unknown")
            symbol = weather_symbol(description)

            lines.append(
                f"{label}: {symbol} {description}"
            )
            lines.append(
                f"High: {day.get('temperature', 'N/A')} F"
            )

            rain = (
                day.get("probabilityOfPrecipitation") or {}
            ).get("value")

            lines.append(
                f"Rain chance: {rain if rain is not None else 'N/A'}%"
            )

        if night:
            lines.append(
                f"Overnight Low: {night.get('temperature', 'N/A')} F"
            )

        lines.append("")

    return xml_screen("7 Day Forecast", lines)
    
def extended_screen(weather):
    daily = weather["extended"]["daily"]

    dates = daily["time"]
    highs = daily["temperature_2m_max"]
    lows = daily["temperature_2m_min"]
    rain = daily["precipitation_probability_max"]
    codes = daily["weather_code"]

    descriptions = {
        0: "Sunny",
        1: "Mostly Sunny",
        2: "Partly Cloudy",
        3: "Cloudy",
        45: "Fog",
        48: "Fog",
        51: "Light Drizzle",
        53: "Drizzle",
        55: "Heavy Drizzle",
        61: "Light Rain",
        63: "Rain",
        65: "Heavy Rain",
        71: "Light Snow",
        73: "Snow",
        75: "Heavy Snow",
        80: "Rain Showers",
        81: "Rain Showers",
        82: "Heavy Showers",
        95: "Thunderstorms",
        96: "Storms/Hail",
        99: "Storms/Hail"
    }

    lines = ["Oxford, Pennsylvania", ""]

    for i in range(min(14, len(dates))):
        label = datetime.fromisoformat(dates[i]).strftime("%a %b %d")
        description = descriptions.get(codes[i], "Variable")
        high = highs[i] if highs[i] is not None else "N/A"
        low = lows[i] if lows[i] is not None else "N/A"
        chance = rain[i] if rain[i] is not None else "N/A"

        lines.append(
            f"{label}: {description}"
        )
        lines.append(
            f"High: {high} F  Low: {low} F  Rain: {chance}%"
        )
        lines.append("")

    return xml_screen("14 Day Forecast", lines)

def get_active_alerts():
    url = (
        "https://api.weather.gov/alerts/active"
        "?point=39.7857,-75.9788"
    )

    return cached_json("alerts", url, 300)


def alerts_screen():
    data = get_active_alerts()
    features = data.get("features", [])

    lines = ["Oxford, Pennsylvania", ""]

    if not features:
        lines.append("No active weather alerts.")
        lines.append("")
        lines.append("No warnings or advisories.")
        return xml_screen("Weather Alerts", lines)

    lines.append(f"Active alerts: {len(features)}")
    lines.append("")

    for alert in features[:4]:
        details = alert.get("properties", {})

        event = details.get("event", "Weather Alert")
        severity = details.get("severity", "Unknown")
        area = details.get("areaDesc", "")
        ends = details.get("ends") or details.get("expires", "")

        if severity == "Extreme" or severity == "Severe":
            symbol = "!"
        elif "Watch" in event:
            symbol = "W"
        elif "Advisory" in event:
            symbol = "A"
        elif severity == "Moderate":
            symbol = "A"
        else:
            symbol = "i"

        lines.append(f"{symbol} {event[:53]}")
        lines.append(f"Severity: {severity}")

        if area:
            lines.append(area[:65])

        if ends:
            lines.append(f"Expires: {ends[:16]}")

        lines.append("")

    return xml_screen("Weather Alerts", lines)
    
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class WeatherHandler(BaseHTTPRequestHandler):

    def send_content(self, content, content_type="text/xml"):
        data = content.encode("iso-8859-1", errors="replace")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"{content_type}; charset=ISO-8859-1"
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    
    def do_GET(self):
        raw_path, _, query_string = self.path.partition("?")
        path = raw_path.rstrip("/")

        if not path:
            path = "/"

        params = parse_qs(query_string)
        search_query = params.get("q", [""])[0]
        search_page = int(params.get("page", ["1"])[0])

        host = self.headers.get("Host", "localhost")

        if path == "/health":
            self.send_content(
                "Weather service is running",
                "text/plain"
            )
            return

        if path == "/":
            self.send_content(main_menu(host))
            return

        if path == "/weather":
            self.send_content(weather_menu(host))
            return

        if path == "/search":
            self.send_content(search_screen(host))
            return

        if path == "/search-results":
            self.send_content(
                search_results_screen(search_query, host, search_page)
            )
            return

        if path not in ("/current", "/hourly", "/daily", "/alerts"):
            self.send_content(
                xml_screen("Weather", ["Page not found"])
            )
            return

        try:
            if path == "/alerts":
                result = alerts_screen()
            else:
                weather = get_weather()

                if path == "/current":
                    result = current_screen(weather)
                elif path == "/hourly":
                    result = hourly_screen(weather)
                else:
                    result = daily_screen(weather)

            self.send_content(result)

        except Exception as error:
            logging.exception("Weather request failed")
            message = (
                f"{type(error).__name__}: {str(error)[:150]}"
            )
            self.send_content(
                xml_screen("Weather Error", [message])
            )

    def log_message(self, format_string, *args):
        logging.info(
            "%s - %s",
            self.client_address[0],
            format_string % args
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    port = int(os.environ.get("PORT", "8080"))

    server = ThreadedHTTPServer(
        ("0.0.0.0", port),
        WeatherHandler
    )

    print(
        f"NOAA Weather Service listening on port {port}",
        flush=True
    )

    server.serve_forever()
