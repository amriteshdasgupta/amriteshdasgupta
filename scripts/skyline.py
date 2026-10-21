"""Render an original, deterministic contribution skyline as a pixel-art PNG."""

import argparse
import json
import math
import os
from pathlib import Path
import random
import subprocess
import urllib.request

from PIL import Image, ImageDraw

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""
ROOT = Path(__file__).resolve().parents[1]


def calendar(login):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    else:
        result = json.loads(subprocess.check_output(
            ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"login={login}"],
            text=True,
        ))
    if result.get("errors"):
        raise RuntimeError(result["errors"])
    user = result["data"]["user"]
    if not user:
        raise ValueError(f"GitHub user not found: {login}")
    return user["contributionsCollection"]["contributionCalendar"]


def render(data, output):
    weeks = data["weeks"]
    if not weeks:
        raise ValueError("Contribution calendar is empty")
    totals = [sum(day["contributionCount"] for day in week["contributionDays"]) for week in weeks]
    width, height, shore = 800, 300, 218
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    # A muted violet dusk fades toward a warm horizon.
    for y in range(shore):
        t = (y / shore) ** 1.6
        color = tuple(round(a + (b - a) * t) for a, b in zip((16, 22, 43), (99, 69, 89)))
        draw.line((0, y, width, y), fill=color)
    rng = random.Random(824)
    for _ in range(90):
        x, y = rng.randrange(width), rng.randrange(12, 125)
        draw.point((x, y), fill=rng.choice(["#536079", "#77819a", "#b5aec0"]))
    draw.ellipse((665, 30, 685, 50), fill="#ecd9ba")
    draw.ellipse((660, 26, 680, 46), fill="#161c33")
    # A distant silhouette gives the activity buildings depth.
    for x in range(0, width, 12):
        top = shore - rng.randint(14, 45)
        draw.rectangle((x, top, x + 10, shore), fill="#363449")
    margin = 24
    step = (width - 2 * margin) / len(weeks)
    peak = max(max(totals), 1)
    for i, (week, total) in enumerate(zip(weeks, totals)):
        local = random.Random(week["contributionDays"][0]["date"])
        x = round(margin + i * step)
        building_width = max(5, int(step) - 3)
        building_height = 9 + round(138 * math.sqrt(total / peak)) if total else 5
        top = shore - building_height
        face = local.choice(["#252b43", "#292e49", "#30334e", "#222b40"])
        draw.rectangle((x, top, x + building_width, shore), fill=face)
        draw.line((x, top, x + building_width, top), fill="#777084")
        draw.line((x + building_width, top + 1, x + building_width, shore), fill="#424359")
        if total and local.random() > 0.65:
            draw.line((x + building_width // 2, top - 7, x + building_width // 2, top), fill="#8a7e8c")
        active_days = sum(day["contributionCount"] > 0 for day in week["contributionDays"])
        for wy in range(top + 6, shore - 3, 7):
            for wx in range(x + 3, x + building_width - 1, 4):
                lit = total > 0 and local.random() < 0.2 + active_days / 10
                color = local.choice(["#edbc82", "#dba176", "#f3d5a2"]) if lit else "#41465f"
                draw.rectangle((wx, wy, wx + 1, wy + 2), fill=color)
    # Broken, dimmed reflections retain the actual building geometry.
    for y in range(shore + 4, height):
        depth = y - shore - 4
        source_y = max(0, shore - 1 - depth * 2)
        shift = round(math.sin(depth * 0.7) * (1 + depth / 18))
        for x in range(width):
            source = image.getpixel((max(0, min(width - 1, x + shift)), source_y))
            blend = max(0.04, 0.43 - depth / 220) if depth % 4 < 2 else 0.06
            image.putpixel((x, y), tuple(round(base + (c - base) * blend) for base, c in zip((13, 28, 43), source)))
    draw.rectangle((0, shore, width, shore + 3), fill="#171e30")
    for x in range(20, width, 65):
        draw.line((x, shore - 5, x, shore), fill="#90858a")
        draw.point((x, shore - 6), fill="#f6d4a3")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.resize((1600, 600), Image.Resampling.NEAREST).save(output, optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="amriteshdasgupta")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "skyline.png")
    args = parser.parse_args()
    data = calendar(args.username)
    render(data, args.output)
    print(f"Rendered {len(data['weeks'])} weeks / {data['totalContributions']} contributions to {args.output}")
