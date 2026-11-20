
import os
import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import requests

# Oxford, Pennsylvania
LATITUDE = 39.7857
LONGITUDE = -75.9788
TIMEZONE = "America/New_York"

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "Clear",
    1: "Mostly Clear",
    2: "Partly Cloudy",
    3: "Cloudy",
    45: "Fog",
    48: "Freezing Fog",
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
    81: "Showers",
    82: "Heavy Showers",
    95: "Thunderstorm",
    96: "Thunderstorm Hail",
    99: "Severe Thunderstorm",
}


def weather_description(code):
    return WEATHER_CODES.get(code, "Unknown")


def get_weather():
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "timezone": TIMEZONE,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "current": (
            "temperature_2m,relative_humidity_2m,"
            "apparent_temperature,precipitation,"
            "weather_code,wind_speed_10m,wind_direction_10m"
        ),
        "hourly": (
            "temperature_2m,precipitation_probability,"
            "weather_code,wind_speed_10m"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max"
        ),
        "forecast_days": 7,
        "forecast_hours": 48,
    }

    response = requests.get(WEATHER_URL, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def direction(degrees):
    directions = [
        "N", "NNE", "NE", "ENE", "E", "ESE",
        "SE", "SSE", "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW"
    ]
    return directions[int((degrees + 11.25) / 22.5) % 16]


def xml_screen(title, lines):
    safe_title = html.escape(str(title))
    safe_text = html.escape("\n".join(lines))

    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<YealinkIPPhoneTextScreen>'
        f'<Title>{safe_title}</Title>'
        f'<Text>{safe_text}</Text>'
        '</YealinkIPPhoneTextScreen>'
    )


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class WeatherHandler(BaseHTTPRequestHandler):

    def send_xml(self, content):
        data = content.encode("iso-8859-1", errors="replace")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=ISO-8859-1")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_error_text(self, message):
        self.send_xml(xml_screen("Weather Error", [message]))

    def base_url(self):
        host = self.headers.get("Host", "localhost")
        return "https://" + host

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if not path:
            path = "/"

        if path == "/health":
            data = b"Weather service is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/":
            base = self.base_url()
            menu = (
                '<?xml version="1.0" encoding="ISO-8859-1"?>'
                '<YealinkIPPhoneTextMenu>'
                '<Title>Oxford PA Weather</Title>'
                '<MenuItem><Prompt>Current Conditions</Prompt>'
                f'<URI>{base}/current</URI></MenuItem>'
                '<MenuItem><Prompt>24 Hour Forecast</Prompt>'
                f'<URI>{base}/hourly</URI></MenuItem>'
                '<MenuItem><Prompt>7 Day Forecast</Prompt>'
                f'<URI>{base}/daily</URI></MenuItem>'
                '</YealinkIPPhoneTextMenu>'
            )
            self.send_xml(menu)
            return

        try:
            weather = get_weather()

            if path == "/current":
                current = weather["current"]
                lines = [
                    "Oxford, Pennsylvania",
                    "",
                    weather_description(current["weather_code"]),
                    f"Temperature: {current['temperature_2m']:.1f} F",
                    f"Feels Like: {current['apparent_temperature']:.1f} F",
                    f"Humidity: {current['relative_humidity_2m']}%",
                    f"Wind: {current['wind_speed_10m']:.1f} mph",
                    f"Direction: {direction(current['wind_direction_10m'])}",
                    f"Precipitation: {current['precipitation']} in",
                    "",
                    "Updated: " + current["time"].replace("T", " ")
                ]
                self.send_xml(xml_screen("Current Conditions", lines))
                return

            if path == "/hourly":
                hourly = weather["hourly"]
                now = datetime.now(ZoneInfo(TIMEZONE)).replace(
                    tzinfo=None, minute=0, second=0, microsecond=0
                )

                lines = ["Oxford PA - Next 24 Hours", ""]
                shown = 0

                for i, time_text in enumerate(hourly["time"]):
                    hour_time = datetime.fromisoformat(time_text)

                    if hour_time < now:
                        continue

                    label = hour_time.strftime("%a %I %p").replace(" 0", " ")
                    temp = hourly["temperature_2m"][i]
                    rain = hourly["precipitation_probability"][i]
                    desc = weather_description(hourly["weather_code"][i])
                    wind = hourly["wind_speed_10m"][i]

                    lines.append(
                        f"{label}: {temp:.0f} F {desc}"
                    )
                    lines.append(f"Rain {rain}%  Wind {wind:.0f} mph")
                    lines.append("")
                    shown += 1

                    if shown >= 24:
                        break

                self.send_xml(xml_screen("24 Hour Forecast", lines))
                return

            if path == "/daily":
                daily = weather["daily"]
                lines = ["Oxford, Pennsylvania", ""]

                for i, date_text in enumerate(daily["time"]):
                    day = datetime.fromisoformat(date_text)
                    label = day.strftime("%a %b %d")
                    desc = weather_description(daily["weather_code"][i])
                    high = daily["temperature_2m_max"][i]
                    low = daily["temperature_2m_min"][i]
                    rain = daily["precipitation_probability_max"][i]

                    lines.append(f"{label}: {desc}")
                    lines.append(f"High {high:.0f} F  Low {low:.0f} F")
                    lines.append(f"Rain chance: {rain}%")
                    lines.append("")

                self.send_xml(xml_screen("7 Day Forecast", lines))
                return

            self.send_error_text("Page not found")

        except Exception as error:
            logging.exception("Weather request failed")
            self.send_error_text(
                "Weather data unavailable. Try again shortly."
            )

    def log_message(self, format_string, *args):
        logging.info("%s - %s", self.client_address[0], format_string % args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    port = int(os.environ.get("PORT", "8080"))
    server = ThreadedHTTPServer(("0.0.0.0", port), WeatherHandler)

    print(f"Weather service listening on port {port}", flush=True)
    server.serve_forever()
