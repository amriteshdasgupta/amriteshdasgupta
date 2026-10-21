"""Render an original, deterministic contribution skyline as a pixel-art PNG."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import urllib.request

from PIL import Image, ImageDraw

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date weekday contributionCount contributionLevel } }
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
    # The calendar keeps GitHub's 53-week / seven-day structure. Every
    # tower is a real day; no ornamental towers invent contribution data.
    width, height = 1600, 580
    image = Image.new("RGB", (width, height), "#0d1117")
    draw = ImageDraw.Draw(image)
    greens = ["#212830", "#0e4429", "#006d32", "#26a641", "#39d353"]
    levels = {name: i for i, name in enumerate([
        "NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"
    ])}

    def point(u, v, z=0):
        return (round(185 + u * 24 - v * 17), round(135 + u * 4.4 + v * 13 - z))

    def shade(color, factor):
        return tuple(round(int(color[i:i + 2], 16) * factor) for i in (1, 3, 5))

    def block(u, v, w, d, base, h, color):
        a, b, c, e = [point(x, y, base + h) for x, y in
                       [(u, v), (u + w, v), (u + w, v + d), (u, v + d)]]
        bottom_b, bottom_c, bottom_e = [point(x, y, base) for x, y in
                                       [(u + w, v), (u + w, v + d), (u, v + d)]]
        draw.polygon([e, c, bottom_c, bottom_e], fill=shade(color, 0.64))
        draw.polygon([b, c, bottom_c, bottom_b], fill=shade(color, 0.43))
        draw.polygon([a, b, c, e], fill=color)

    draw.text((56, 36), "THE CONTRIBUTION KEEP", fill="#8b949e", font_size=18)
    draw.text((56, 64), f"{data['totalContributions']:,} contributions / past year", fill="#52616a", font_size=14)
    block(-0.4, -0.4, len(weeks) + 0.8, 7.8, -10, 7, "#1a2824")
    days = [(i, day["weekday"], day) for i, week in enumerate(weeks) for day in week["contributionDays"]]
    peak = max([day["contributionCount"] for _, _, day in days] + [1])
    # Draw back to front so near towers correctly obscure distant ones.
    for u, v, day in sorted(days, key=lambda item: (item[0] + item[1], item[0])):
        count = day["contributionCount"]
        level = levels[day["contributionLevel"]]
        color = greens[level]
        block(u, v, 0.88, 0.88, 0, 2, greens[0])
        if not count:
            continue
        h = 10 + round(64 * math.sqrt(count / peak))
        block(u + 0.07, v + 0.07, 0.74, 0.74, 2, h, color)
        # Thin masonry courses on both visible stone faces.
        for z in range(10, h, 9):
            draw.line([point(u + 0.07, v + 0.81, z), point(u + 0.81, v + 0.81, z),
                       point(u + 0.81, v + 0.07, z)], fill=shade(color, 0.47), width=1)
        # Overhanging parapet and four raised corner merlons.
        block(u, v, 0.88, 0.88, h + 2, 3, color)
        block(u + 0.22, v + 0.22, 0.44, 0.44, h + 5, 0, "#102b1d")
        for du, dv in [(0, 0), (0.64, 0), (0, 0.64), (0.64, 0.64)]:
            block(u + du, v + dv, 0.24, 0.24, h + 5, 5, color)
        if h > 25:
            x, y = point(u + 0.44, v + 0.82, h * 0.62)
            draw.rounded_rectangle((x - 1, y - 4, x + 2, y + 3), radius=2, fill="#0d2118")
        if level == 4 and count >= peak * 0.55:
            x, y = point(u + 0.1, v + 0.1, h + 10)
            draw.line((x, y, x, y - 20), fill="#83c99a", width=1)
            draw.polygon([(x + 1, y - 20), (x + 12, y - 17), (x + 1, y - 12)], fill="#39d353")
    draw.text((56, 526), "ONE DAY, ONE TILE. A LITTLE MORE BUILT EACH DAY.", fill="#6e7c76", font_size=13)
    draw.text((1310, 526), "Less", fill="#8b949e", font_size=13)
    for i, color in enumerate(greens):
        x = 1350 + i * 20
        draw.rounded_rectangle((x, 526, x + 13, 539), radius=2, fill=color)
    draw.text((1457, 526), "More", fill="#8b949e", font_size=13)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="amriteshdasgupta")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "skyline.png")
    args = parser.parse_args()
    data = calendar(args.username)
    render(data, args.output)
    print(f"Rendered {len(data['weeks'])} weeks / {data['totalContributions']} contributions to {args.output}")
