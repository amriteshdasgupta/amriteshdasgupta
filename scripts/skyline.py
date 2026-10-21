"""Render the last 20 contribution days as an animated medieval market."""

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


def recent_days(data):
    days = sorted(
        (day for week in data["weeks"] for day in week["contributionDays"]),
        key=lambda day: day["date"],
    )
    return days[-20:]


def render(data, output):
    selected = recent_days(data)
    if not selected:
        raise ValueError("Contribution calendar is empty")
    # Twenty activity buildings share a town with decorative market stalls.
    width, height = 960, 660
    image = Image.new("RGB", (width, height), "#0d1117")
    draw = ImageDraw.Draw(image)
    lights = []
    rng = random.Random(824)
    transform = None
    greens = ["#212830", "#0e4429", "#006d32", "#26a641", "#39d353"]
    levels = {name: i for i, name in enumerate([
        "NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"
    ])}

    def point(u, v, z=0):
        if transform is not None:
            origin_u, origin_v, sx, sy = transform
            u = origin_u + (u - origin_u) * sx + (1 - sx) * 0.44
            v = origin_v + (v - origin_v) * sy + (1 - sy) * 0.44
        return (round(310 + u * 78 - v * 50), round(200 + u * 22 + v * 40 - z))

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

    block(-0.25, -0.25, 7.5, 5.8, -12, 9, "#26352f")
    for u in range(28):
        for v in range(22):
            block(u / 4, v / 4, 0.23, 0.23, -2, 2, rng.choice(["#303c38", "#293630", "#344039"]))
    ground = image.convert("RGBA")
    layout = ["BSBBSBB", "SB.BBSB", "B.SBSB.", "SBBSB.B", "BS.BSBB"]
    plots = [(u, v, kind) for v, row in enumerate(layout) for u, kind in enumerate(row)]
    peak = max([day["contributionCount"] for day in selected] + [1])

    def building(u, v, day):
        nonlocal transform
        count = day["contributionCount"]
        level = levels[day["contributionLevel"]]
        color = greens[level]
        block(u, v, 0.88, 0.88, 0, 2, greens[0])
        if not count:
            return
        activity = count / peak
        # Strong height and footprint variation, while keeping a little
        # artistic exaggeration for the most active days' keeps.
        sx = 0.48 + 0.5 * math.sqrt(activity)
        sy = 0.52 + 0.44 * activity ** 0.65
        transform = (u, v, sx, sy)
        h = 23 + round(132 * activity ** 0.85)
        block(u + 0.07, v + 0.07, 0.74, 0.74, 2, h, color)
        # Thin masonry courses on both visible stone faces.
        for z in range(10, h, 12):
            draw.line([point(u + 0.07, v + 0.81, z), point(u + 0.81, v + 0.81, z),
                       point(u + 0.81, v + 0.07, z)], fill=shade(color, 0.47), width=1)
        if level < 3:
            # Steep green roofs over timber-framed merchant houses.
            ridge_a, ridge_b = point(u + 0.44, v, h + 31), point(u + 0.44, v + 0.9, h + 31)
            draw.polygon([point(u - 0.05, v, h + 2), ridge_a, ridge_b, point(u - 0.05, v + 0.9, h + 2)], fill=color)
            draw.polygon([ridge_a, point(u + 0.94, v, h + 2), point(u + 0.94, v + 0.9, h + 2), ridge_b], fill=shade(color, 0.65))
            draw.line([point(u + 0.1, v + 0.82, 3), point(u + 0.76, v + 0.82, h)], fill="#92754c", width=1)
        else:
            block(u, v, 0.88, 0.88, h + 2, 3, color)
            block(u + 0.22, v + 0.22, 0.44, 0.44, h + 5, 0, "#102b1d")
            for du, dv in [(0, 0), (0.32, 0), (0.64, 0), (0, 0.32), (0.64, 0.32), (0, 0.64), (0.32, 0.64), (0.64, 0.64)]:
                block(u + du, v + dv, 0.19, 0.19, h + 5, 9, color)
        # Lit doorways and rows of leaded windows on both visible faces.
        x, y = point(u + 0.44, v + 0.82, 2)
        draw.rounded_rectangle((x - 5, y - 16, x + 5, y), radius=5, fill="#c8974e")
        draw.line((x, y - 12, x, y), fill="#59432c")
        for z in range(24, h - 5, 20):
            for du in [0.27, 0.62]:
                x, y = point(u + du, v + 0.82, z)
                draw.rounded_rectangle((x - 3, y - 6, x + 3, y + 4), radius=3, fill="#d9a258")
                lamp(x, y, size=2)
                draw.line((x - 3, y - 2, x + 3, y - 2), fill="#665335")
            lamp(*point(u + 0.82, v + 0.4, z), size=2)
        lamp(*point(u + 0.2, v + 0.86, 11), size=2)
        if level == 4 and count >= peak * 0.55:
            x, y = point(u + 0.1, v + 0.1, h + 10)
            draw.line((x, y, x, y - 20), fill="#83c99a", width=1)
            draw.polygon([(x + 1, y - 20), (x + 12, y - 17), (x + 1, y - 12)], fill="#39d353")
        transform = None

    def market(u, v):
        stall(u + 0.04, v + 0.03, rng.choice(["#287846", "#a35943", "#b58b46", "#3e7779"]))
        for du in [0.05, 0.85]:
            x, y = point(u + du, v + 0.92, 0)
            draw.ellipse((x - 6, y - 10, x + 6, y + 2), fill="#805d3a", outline="#b19459")
            draw.line((x - 6, y - 3, x + 6, y - 3), fill="#382f27", width=2)
        end = u + 1
        for pos in [u, end]:
            draw.line([point(pos, v + 0.9), point(pos, v + 0.9, 44)], fill="#977950", width=2)
        cable = [point(u + (end - u) * t / 24, v + 0.9, 44 - 9 * math.sin(math.pi * t / 24)) for t in range(25)]
        draw.line(cable, fill="#8c8260", width=1)
        for t in range(2, 24, 4):
            x, y = cable[t]
            draw.line((x, y, x, y + 4), fill="#b09964")
            lamp(x, y + 6)

    # Cache transparent sprites. Buildings, stalls, and moving inhabitants
    # all share the same depth ordering, including people behind buildings.
    scenery = []
    day_iter = iter(selected)
    for u, v, kind in plots:
        layer = Image.new("RGBA", image.size)
        draw = ImageDraw.Draw(layer)
        if kind == "B":
            day = next(day_iter, None)
            if day is not None:
                building(u, v, day)
        elif kind == "S":
            market(u, v)
        else:
            # Small leafy planters break up the paved courtyards.
            block(u + 0.55, v + 0.1, 0.22, 0.22, 0, 7, "#755c40")
            x, y = point(u + 0.66, v + 0.21, 12)
            draw.ellipse((x - 8, y - 7, x + 8, y + 5), fill="#416b40")
        box = layer.getbbox()
        if box:
            scenery.append((point(u + 0.44, v + 0.82)[1], layer.crop(box), box[:2]))
    scenery.sort(key=lambda item: item[0])
    image = ground.copy()
    for _, sprite, position in scenery:
        image.alpha_composite(sprite, position)
    image = image.convert("RGB")
    # Soft amber bloom adds warmth without flattening the pixel geometry.
    glow = Image.new("RGB", image.size)
    glow_draw = ImageDraw.Draw(glow)
    for x, y, size in lights:
        # Hidden lamps should not shine through a foreground building.
        if image.getpixel((x, y)) != (255, 242, 189):
            continue
        r = size + 3
        glow_draw.ellipse((x - r, y - r, x + r, y + r), fill=(105, 60, 16))
    bloom = glow.filter(ImageFilter.GaussianBlur(4))
    # Walking routes thread between plots and through the pocket squares.
    routes = [(u + 0.45, v + 1.02) for u, v, _ in plots if (u + v) % 2 == 0]
    routes += [(u + 0.32, v + 0.5) for u, v, kind in plots if kind == "."]
    shoppers = [(u, v, rng.random() * math.tau,
                 rng.choice(["#759c8c", "#a46c65", "#bc9d60", "#707fa1"])) for u, v in routes]
    frames = []
    for frame_index in range(40):
        phase = math.tau * frame_index / 40
        frame = ground.copy()
        people = []
        for u, v, offset, coat in shoppers:
            t = phase + offset
            x, y = point(u + 0.2 * math.sin(t), v + 0.035 * math.cos(t))
            layer = Image.new("RGBA", (18, 25))
            painter = ImageDraw.Draw(layer)
            screen_x, screen_y = x, y
            x, y = 9, 22
            stride = round(2 * math.sin(t * 6))
            painter.ellipse((x - 5, y - 2, x + 5, y + 2), fill="#17251f")
            painter.line((x - 2, y - 5, x - 2 + stride, y), fill="#c5b08b", width=2)
            painter.line((x + 2, y - 5, x + 2 - stride, y), fill="#c5b08b", width=2)
            painter.rectangle((x - 3, y - 13, x + 3, y - 5), fill=coat)
            painter.line((x - 4, y - 11, x - 5 + stride, y - 6), fill="#d7b48a", width=2)
            painter.rectangle((x - 2, y - 18, x + 2, y - 14), fill="#d7b48a")
            painter.rectangle((x - 3, y - 19, x + 2, y - 18), fill="#514236")
            people.append((screen_y, layer, (screen_x - 9, screen_y - 22)))
        for _, sprite, position in sorted(scenery + people, key=lambda item: item[0]):
            frame.alpha_composite(sprite, position)
        frame = ImageChops.add(frame.convert("RGB"), bloom.point(lambda p: round(p * (0.94 + 0.06 * math.sin(phase)))))
        frames.append(frame)
    # Shared palette prevents color shimmer between frames.
    palette = frames[0].quantize(colors=256)
    frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE)
              .resize((width * 2, height * 2), Image.Resampling.NEAREST) for frame in frames]
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=100,
                   loop=0, optimize=False, disposal=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="amriteshdasgupta")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "market.gif")
    args = parser.parse_args()
    data = calendar(args.username)
    render(data, args.output)
    days = recent_days(data)
    print(f"Rendered {len(days)} days ({days[0]['date']} to {days[-1]['date']}) to {args.output}")
