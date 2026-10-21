"""Render an original contribution-powered medieval night market as a PNG."""

import argparse
import json
import math
import os
import random
from pathlib import Path
import subprocess
import urllib.request

from PIL import Image, ImageChops, ImageDraw, ImageFilter

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
    # Daily buildings keep the calendar structure; the foreground market
    # and its inhabitants are decorative, rather than extra activity days.
    width, height = 1600, 560
    image = Image.new("RGB", (width, height), "#0d1117")
    draw = ImageDraw.Draw(image)
    lights = []
    rng = random.Random(824)
    greens = ["#212830", "#0e4429", "#006d32", "#26a641", "#39d353"]
    levels = {name: i for i, name in enumerate([
        "NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"
    ])}

    def point(u, v, z=0):
        return (round(220 + u * 24 - v * 17), round(105 + u * 4.4 + v * 13 - z))

    def lamp(x, y, size=2):
        draw.rectangle((x - size, y - size, x + size, y + size), fill="#ffc66d")
        draw.point((x, y), fill="#fff2bd")
        lights.append((x, y, size))

    def stall(u, v, color):
        block(u, v, 0.85, 0.7, 0, 8, "#795337")
        for du, dv in [(0, 0), (0.85, 0), (0, 0.7), (0.85, 0.7)]:
            draw.line([point(u + du, v + dv, 0), point(u + du, v + dv, 23)], fill="#ab8251", width=1)
        for stripe in range(5):
            a, b = stripe * 0.2, (stripe + 1) * 0.2
            draw.polygon([point(u + a - 0.08, v - 0.08, 25), point(u + b - 0.08, v - 0.08, 25),
                          point(u + b - 0.08, v + 0.85, 19), point(u + a - 0.08, v + 0.85, 19)],
                         fill=color if stripe % 2 == 0 else "#d8c79b")
        for j in range(4):
            x, y = point(u + j * 0.18 + 0.1, v + 0.72, 10)
            draw.ellipse((x - 2, y - 2, x + 2, y + 1), fill=rng.choice(["#df8e51", "#89b658", "#e1be6e"]))
        lamp(*point(u, v + 0.75, 15))

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

    block(-0.4, -0.4, len(weeks) + 0.8, 11.5, -10, 7, "#26352f")
    # Cobblestone lanes extend in front of the contribution district.
    for u in range(len(weeks)):
        for v in range(7, 11):
            block(u, v, 0.91, 0.91, -2, 2, rng.choice(["#303c38", "#293630", "#344039"]))
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
        if level == 1:
            stall(u, v, color)
            continue
        block(u + 0.07, v + 0.07, 0.74, 0.74, 2, h, color)
        # Thin masonry courses on both visible stone faces.
        for z in range(10, h, 9):
            draw.line([point(u + 0.07, v + 0.81, z), point(u + 0.81, v + 0.81, z),
                       point(u + 0.81, v + 0.07, z)], fill=shade(color, 0.47), width=1)
        if level < 4:
            # Steep green roofs over timber-framed merchant houses.
            ridge_a, ridge_b = point(u + 0.44, v, h + 17), point(u + 0.44, v + 0.9, h + 17)
            draw.polygon([point(u - 0.05, v, h + 2), ridge_a, ridge_b, point(u - 0.05, v + 0.9, h + 2)], fill=color)
            draw.polygon([ridge_a, point(u + 0.94, v, h + 2), point(u + 0.94, v + 0.9, h + 2), ridge_b], fill=shade(color, 0.65))
            draw.line([point(u + 0.1, v + 0.82, 3), point(u + 0.76, v + 0.82, h)], fill="#92754c", width=1)
        else:
            block(u, v, 0.88, 0.88, h + 2, 3, color)
            block(u + 0.22, v + 0.22, 0.44, 0.44, h + 5, 0, "#102b1d")
            for du, dv in [(0, 0), (0.64, 0), (0, 0.64), (0.64, 0.64)]:
                block(u + du, v + dv, 0.24, 0.24, h + 5, 5, color)
        for z in range(12, h, 13):
            for du in [0.27, 0.62]:
                lamp(*point(u + du, v + 0.82, z), size=1)
            lamp(*point(u + 0.82, v + 0.4, z), size=1)
        lamp(*point(u + 0.2, v + 0.86, 7), size=1)
        if level == 4 and count >= peak * 0.55:
            x, y = point(u + 0.1, v + 0.1, h + 10)
            draw.line((x, y, x, y - 20), fill="#83c99a", width=1)
            draw.polygon([(x + 1, y - 20), (x + 12, y - 17), (x + 1, y - 12)], fill="#39d353")
    # A lively promenade: canopies, barrels, shoppers, and hanging lanterns.
    for u in range(len(weeks)):
        if u % 3 != 2:
            stall(u, 7.7, rng.choice(["#287846", "#a35943", "#b58b46", "#3e7779"]))
        if u % 4 == 0:
            x, y = point(u + 0.6, 9, 0)
            draw.ellipse((x - 4, y - 6, x + 4, y + 2), fill="#805d3a", outline="#b19459")
            draw.line((x - 4, y - 2, x + 4, y - 2), fill="#382f27")
        for _ in range(rng.randint(1, 3)):
            x, y = point(u + rng.random(), 9 + rng.random() * 1.5)
            draw.ellipse((x - 3, y - 1, x + 4, y + 2), fill="#17251f")
            draw.line((x - 1, y - 3, x - 1, y + 1), fill="#b3a58a")
            draw.line((x + 2, y - 3, x + 2, y + 1), fill="#b3a58a")
            draw.rectangle((x - 2, y - 8, x + 2, y - 3), fill=rng.choice(["#759c8c", "#a46c65", "#bc9d60", "#707fa1"]))
            draw.rectangle((x - 1, y - 11, x + 1, y - 9), fill="#d7b48a")
    for u in range(0, len(weeks), 4):
        end = min(u + 4, len(weeks))
        for pos in [u, end]:
            draw.line([point(pos, 9.2), point(pos, 9.2, 39)], fill="#977950", width=2)
        cable = [point(u + (end - u) * t / 24, 9.2, 39 - 9 * math.sin(math.pi * t / 24)) for t in range(25)]
        draw.line(cable, fill="#8c8260", width=1)
        for t in range(2, 24, 4):
            x, y = cable[t]
            draw.line((x, y, x, y + 4), fill="#b09964")
            lamp(x, y + 6)
    # Soft amber bloom adds warmth without flattening the pixel geometry.
    glow = Image.new("RGB", image.size)
    glow_draw = ImageDraw.Draw(glow)
    for x, y, size in lights:
        # Hidden lamps should not shine through a foreground building.
        if image.getpixel((x, y)) != (255, 242, 189):
            continue
        r = size + 3
        glow_draw.ellipse((x - r, y - r, x + r, y + r), fill=(105, 60, 16))
    image = ImageChops.add(image, glow.filter(ImageFilter.GaussianBlur(4)))
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
