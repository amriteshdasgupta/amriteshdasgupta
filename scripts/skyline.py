"""Render the last 20 contribution days as a floating pixel-art castle town.

Every day becomes a building inside the castle walls: the more you
contributed, the taller it gets, and its roof takes that day's GitHub green.
Quiet days are left as overgrown ruins. The scene is built from voxels,
lit by the moon and by thousands of candle-lit windows, and animated with
waving flags, a waterfall, chimney smoke, bats and townsfolk.
"""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

(EMPTY, GRASS, DIRT, ROCK, ROCK2, COBBLE, STONE, STONE2, PLASTER, TIMBER,
 ROOF1, ROOF2, ROOF3, ROOF4, WINDOW, DOOR, WATER, WOOD, AWN_A, AWN_B, LEAVES,
 TRUNK, LANTERN, SLATE, MOSS, FALL, GOODS1, GOODS2, GOODS3, PINE, BEACON,
 PATH, ROOF0, DARK, IRON) = range(35)

BASE = {
    GRASS: "#3f6b3a", DIRT: "#5a4231", ROCK: "#5b5f6b", ROCK2: "#454956",
    COBBLE: "#6d6a68", STONE: "#a3a2a6", STONE2: "#bdb9ae", PLASTER: "#e6dcc0",
    TIMBER: "#4f3523", ROOF1: "#1d6b39", ROOF2: "#22903f", ROOF3: "#2fb84f",
    ROOF4: "#4be26a", WINDOW: "#ffcf6e", DOOR: "#5b3a22", WATER: "#1f4a6b",
    WOOD: "#7a5434", AWN_A: "#b8433a", AWN_B: "#e3d3a6", LEAVES: "#2c5a33",
    TRUNK: "#4a3426", LANTERN: "#ffe08a", SLATE: "#4a4f63", MOSS: "#556b3a",
    FALL: "#7fb8e0", GOODS1: "#d98a3d", GOODS2: "#9cc25a", GOODS3: "#c9a04a",
    PINE: "#21452f", BEACON: "#ff9a3c", PATH: "#7b6a55", ROOF0: "#355441",
    DARK: "#1b1712", IRON: "#3a3a40",
}
EMISSIVE = {WINDOW, LANTERN, BEACON}
ROOFS = [ROOF0, ROOF1, ROOF2, ROOF3, ROOF4]
GREENS = ["#2d333b", "#0e4429", "#006d32", "#26a641", "#39d353"]
LEVELS = {name: i for i, name in enumerate([
    "NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"
])}


def rgb(hex_color):
    return [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]


PALETTE = np.zeros((40, 3), np.float32)
for _mat, _hex in BASE.items():
    PALETTE[_mat] = rgb(_hex)


class World:
    """A dense voxel grid with a material and a light id per voxel."""

    def __init__(self, sx, sy, sz):
        self.mat = np.zeros((sx, sy, sz), np.uint8)
        self.wid = np.zeros((sx, sy, sz), np.int32)
        self.shape = (sx, sy, sz)
        self.lights = []      # (x, y, z, radius, strength, (r, g, b))
        self.windows = 0
        self.flags = []       # (x, y, z, length, color)
        self.smoke = []       # (x, y, z)
        self.walkers = []     # dicts: kind, mode, x0, y0, x1, y1, z, coat, phase, torch

    def box(self, mat, x0, x1, y0, y1, z0, z1):
        sx, sy, sz = self.shape
        x0, x1 = max(0, x0), min(sx, x1)
        y0, y1 = max(0, y0), min(sy, y1)
        z0, z1 = max(0, z0), min(sz, z1)
        if x0 < x1 and y0 < y1 and z0 < z1:
            self.mat[x0:x1, y0:y1, z0:z1] = mat
            self.wid[x0:x1, y0:y1, z0:z1] = 0

    def set(self, x, y, z, mat, wid=0):
        sx, sy, sz = self.shape
        if 0 <= x < sx and 0 <= y < sy and 0 <= z < sz:
            self.mat[x, y, z] = mat
            self.wid[x, y, z] = wid

    def window(self, x, y, z, rng, tall=2, lit_chance=0.82):
        lit = rng.random() < lit_chance
        self.windows += 1
        for k in range(tall):
            self.set(x, y, z + k, WINDOW if lit else DARK, self.windows if lit else 0)
        if lit:
            self.lights.append((x + 0.5, y + 0.5, z + 1, 4.5, 0.55, (255, 170, 80)))

    def lantern(self, x, y, z, radius=7.0, strength=1.0):
        self.set(x, y, z, LANTERN, -1)
        self.lights.append((x + 0.5, y + 0.5, z + 0.5, radius, strength, (255, 160, 70)))


# ---------------------------------------------------------------------------
# Scene construction
# ---------------------------------------------------------------------------

SX, SY, SZ = 140, 106, 150
G = 36                      # z of the first voxel above the ground surface
PLOT, PITCH, PLOT0 = 12, 15, 16
LAYOUT = ["BSBBSBB", "SB.BBSB", "B.SBSB.", "SBBSB.B", "BS.BSBB"]
ISL_X, ISL_Y = 134, 104     # island footprint


def build_island(w, rng):
    xs, ys = np.meshgrid(np.arange(SX), np.arange(SY), indexing="ij")
    # Rounded-rectangle footprint with a ragged edge.
    r = 13
    cx = np.clip(xs, r, ISL_X - 1 - r)
    cy = np.clip(ys, r, ISL_Y - 1 - r)
    dist_out = np.hypot(xs - cx, ys - cy)
    jitter = rng.random((SX, SY)) * 1.6
    inside = (xs < ISL_X) & (ys < ISL_Y) & (dist_out + jitter * 0.6 < r + 0.5)
    # Distance to the edge sets how far the rocky underside hangs down.
    rect_edge = np.minimum.reduce([xs, ys, ISL_X - 1 - xs, ISL_Y - 1 - ys]).astype(np.float32)
    edge = np.clip(np.where(dist_out > 0, r - dist_out, rect_edge), 0, None)
    depth = np.clip(4 + edge * 0.85 + rng.random((SX, SY)) * 4, 0, G - 4)
    # Long stalactite-like spurs under the island.
    spur = (rng.random((SX, SY)) < 0.025) & (edge > 6)
    depth = np.where(spur, np.minimum(depth + 5 + rng.random((SX, SY)) * 9, G - 2), depth)
    for x in range(SX):
        for y in range(SY):
            if not inside[x, y]:
                continue
            bottom = int(G - 1 - depth[x, y])
            w.mat[x, y, bottom:G - 3] = ROCK
            w.mat[x, y, G - 3:G - 1] = DIRT
            w.mat[x, y, G - 1] = GRASS
    # Rock strata with darker bands.
    zs = np.arange(SZ)[None, None, :]
    band = ((zs + (xs[:, :, None] // 7)) % 5 == 0)
    w.mat[(w.mat == ROCK) & band] = ROCK2
    return inside


def carve_moat(w):
    z = G - 1
    ring = [(6, 128, 6, 10), (6, 128, 94, 98), (6, 10, 6, 98), (124, 128, 6, 98)]
    for x0, x1, y0, y1 in ring:
        w.mat[x0:x1, y0:y1, z - 1:z + 1] = EMPTY
        w.mat[x0:x1, y0:y1, z - 2] = WATER
    # Stone embankments.
    for x0, x1, y0, y1 in [(5, 129, 5, 6), (5, 129, 98, 99), (5, 6, 5, 99), (128, 129, 5, 99),
                           (10, 124, 10, 11), (10, 124, 93, 94), (10, 11, 10, 94), (123, 124, 10, 94)]:
        w.mat[x0:x1, y0:y1, z - 1:z + 1] = np.where(w.mat[x0:x1, y0:y1, z - 1:z + 1] == EMPTY,
                                                     EMPTY, STONE)
    # Overflow channel through the rim and a waterfall off the island edge.
    w.mat[128:ISL_X + 1, 46:51, z - 1:z + 1] = EMPTY
    w.mat[128:ISL_X, 46:51, z - 2] = WATER
    w.mat[128:ISL_X, 45, z - 2:z + 1] = STONE
    w.mat[128:ISL_X, 51, z - 2:z + 1] = STONE
    for zz in range(2, z - 1):
        w.mat[ISL_X, 46:51, zz] = FALL
        w.wid[ISL_X, 46:51, zz] = -2


def tower_top(w, x0, x1, y0, y1, h, rng, roof=None, spire=0, steep=1):
    """Machicolated parapet with merlons, optionally capped with a roof."""
    w.box(STONE2, x0 - 1, x1 + 1, y0 - 1, y1 + 1, h, h + 1)
    w.box(STONE, x0 - 1, x1 + 1, y0 - 1, y1 + 1, h + 1, h + 2)
    w.box(EMPTY, x0, x1, y0, y1, h + 1, h + 2)
    for x in range(x0 - 1, x1 + 1):
        for y in range(y0 - 1, y1 + 1):
            border = x in (x0 - 1, x1) or y in (y0 - 1, y1)
            if border and (x + y) % 2 == 0:
                w.set(x, y, h + 2, STONE)
    # Corbels beneath the overhang.
    for x in range(x0, x1, 2):
        w.set(x, y1, h - 1, STONE2)
    for y in range(y0, y1, 2):
        w.set(x1, y, h - 1, STONE2)
    if roof is None:
        return h + 2
    # Stepped pyramid roof inset from the parapet.
    k, z = 0, h + 1
    rx0, rx1, ry0, ry1 = x0, x1, y0, y1
    while rx0 < rx1 and ry0 < ry1:
        w.box(roof, rx0, rx1, ry0, ry1, z, z + steep)
        z += steep
        rx0, rx1, ry0, ry1 = rx0 + 1, rx1 - 1, ry0 + 1, ry1 - 1
        k += 1
    top = z
    if spire:
        mx, my = (x0 + x1) // 2, (y0 + y1) // 2
        for zz in range(top, top + spire):
            w.set(mx, my, zz, IRON)
        return top + spire
    return top


def build_tower(w, ox, oy, activity, level, rng, peak):
    s = int(round(6 + 4 * math.sqrt(activity)))
    h = G + 10 + int(round(56 * activity ** 0.85))
    x0 = ox + (PLOT - s) // 2
    y0 = oy + (PLOT - s) // 2
    x1, y1 = x0 + s, y0 + s
    # Plinth and body.
    w.box(STONE2, x0 - 1, x1 + 1, y0 - 1, y1 + 1, G, G + 2)
    w.box(STONE, x0, x1, y0, y1, G + 2, h)
    # Corner quoins.
    for zz in range(G + 2, h):
        if zz % 2 == 0:
            for cx, cy in [(x1 - 1, y1 - 1), (x0, y1 - 1), (x1 - 1, y0)]:
                w.set(cx, cy, zz, STONE2)
    # Ivy creeping up the shaded face.
    ivy_h = rng.randint(4, 12)
    for zz in range(G + 2, G + 2 + ivy_h):
        for yy in range(y0, y1):
            if rng.random() < 0.5 - (zz - G) / (ivy_h * 2.4):
                w.set(x1 - 1, yy, zz, MOSS)
    # Door on the +y face with a lantern.
    mx = (x0 + x1) // 2
    w.box(DOOR, mx - 1, mx + 1, y1 - 1, y1, G + 2, G + 5)
    w.set(mx - 1, y1 - 1, G + 5, STONE2)
    w.set(mx, y1 - 1, G + 5, STONE2)
    w.lantern(mx + 1, y1, G + 5)
    # Windows on both visible faces.
    for n, zz in enumerate(range(G + 9, h - 4, 7)):
        for xx in range(x0 + 2, x1 - 2, 4):
            if (n + xx) % 3:
                w.window(xx, y1 - 1, zz, rng, lit_chance=0.7)
            else:
                w.set(xx, y1 - 1, zz, DARK)
                w.set(xx, y1 - 1, zz + 1, DARK)
        for yy in range(y0 + 2, y1 - 2, 4):
            if (n + yy) % 2:
                w.window(x1 - 1, yy, zz + 3, rng, lit_chance=0.7)
            else:
                w.set(x1 - 1, yy, zz + 3, DARK)
                w.set(x1 - 1, yy, zz + 4, DARK)
    roof = ROOFS[level]
    top = tower_top(w, x0, x1, y0, y1, h, rng, roof=roof, spire=4 if level == 4 else 0,
                    steep=3 if level == 4 else 2)
    # A green banner hanging from the parapet.
    for zz in range(h - 7, h):
        w.set(x0 + 1, y1, zz, roof)
        w.set(x0 + 2, y1, zz, roof)
    w.set(x0 + 1, y1, h - 8, roof)
    if level == 4:
        w.flags.append((mx, (y0 + y1) // 2, top, 6 if activity >= 0.99 else 4, GREENS[4]))
    else:
        w.flags.append((x0 - 1, y1, h + 6, 3, GREENS[level]))
        for zz in range(h + 2, h + 6):
            w.set(x0 - 1, y1, zz, IRON)
    if activity >= 0.99:
        # The busiest day carries a beacon on its parapet.
        w.set(x1, y0 - 1, h + 2, BEACON, -1)
        w.lights.append((x1 + 0.5, y0 - 0.5, h + 3, 14, 1.6, (255, 150, 60)))


def build_house(w, ox, oy, activity, level, rng):
    along_x = rng.random() < 0.5
    lx, ly = (10, 7) if along_x else (7, 10)
    x0 = ox + (PLOT - lx) // 2
    y0 = oy + (PLOT - ly) // 2
    x1, y1 = x0 + lx, y0 + ly
    hw = G + 6 + int(round(18 * activity ** 0.85))
    w.box(STONE, x0, x1, y0, y1, G, G + 3)
    w.box(PLASTER, x0, x1, y0, y1, G + 3, hw)
    # Jettied upper storey.
    if hw - G > 10:
        w.box(PLASTER, x0, x1 + 1, y0, y1 + 1, G + 8, hw)
        x1e, y1e = x1 + 1, y1 + 1
    else:
        x1e, y1e = x1, y1
    # Timber framing on visible faces.
    for zz in list(range(G + 3, hw, 5)) + [hw - 1]:
        w.box(TIMBER, x0, x1e, y1e - 1, y1e, zz, zz + 1)
        w.box(TIMBER, x1e - 1, x1e, y0, y1e, zz, zz + 1)
    for xx in range(x0, x1e, 3):
        w.box(TIMBER, xx, xx + 1, y1e - 1, y1e, G + 3, hw)
    for yy in range(y0, y1e, 3):
        w.box(TIMBER, x1e - 1, x1e, yy, yy + 1, G + 3, hw)
    for zz in range(G + 4, hw - 2, 5):
        for xx in range(x0 + 1, x1e - 1, 3):
            w.window(xx, y1e - 1, zz + 1, rng)
        for yy in range(y0 + 1, y1e - 1, 3):
            w.window(x1e - 1, yy, zz + 1, rng)
    # Door and stoop lantern.
    dx = x0 + 2
    w.box(DOOR, dx, dx + 2, y1 - 1, y1, G, G + 3)
    w.lantern(dx + 2, y1, G + 3, radius=6)
    # Steep gable roof in the day's green.
    roof = ROOFS[level]
    k = 0
    while True:
        if along_x:
            a0, a1 = y0 - 1 + k, y1e + 1 - k
            if a0 >= a1:
                break
            w.box(PLASTER, x0, x1e, a0, a1, hw + k, hw + k + 1)
            w.box(roof, x0 - 1, x1e + 1, a0, a0 + 1, hw + k, hw + k + 1)
            w.box(roof, x0 - 1, x1e + 1, a1 - 1, a1, hw + k, hw + k + 1)
        else:
            a0, a1 = x0 - 1 + k, x1e + 1 - k
            if a0 >= a1:
                break
            w.box(PLASTER, a0, a1, y0, y1e, hw + k, hw + k + 1)
            w.box(roof, a0, a0 + 1, y0 - 1, y1e + 1, hw + k, hw + k + 1)
            w.box(roof, a1 - 1, a1, y0 - 1, y1e + 1, hw + k, hw + k + 1)
        k += 1
    ridge = hw + k
    # Gable window in the visible end.
    if along_x:
        w.window(x1e - 1, (y0 + y1e) // 2, hw + 1, rng, tall=1)
    else:
        w.window((x0 + x1e) // 2, y1e - 1, hw + 1, rng, tall=1)
    # Chimney with smoke.
    cx, cy = x0 + 1, y0 + 1
    w.box(STONE, cx, cx + 2, cy, cy + 2, G, ridge + 2)
    w.smoke.append((cx + 1, cy + 1, ridge + 2))


def build_ruin(w, ox, oy, rng):
    x0, y0, x1, y1 = ox + 2, oy + 2, ox + 10, oy + 10
    for x in range(x0, x1):
        for y in range(y0, y1):
            if x in (x0, x1 - 1) or y in (y0, y1 - 1):
                for zz in range(G, G + rng.choice([0, 1, 1, 2, 3])):
                    w.set(x, y, zz, MOSS if rng.random() < 0.3 else STONE)
    w.box(GRASS, x0 + 1, x1 - 1, y0 + 1, y1 - 1, G - 1, G)
    tree(w, ox + 9, oy + 3, rng, small=True)


def tree(w, x, y, rng, small=False, pine=False):
    if pine:
        h = rng.randint(7, 11)
        w.box(TRUNK, x, x + 1, y, y + 1, G, G + 2)
        for k in range(h):
            rad = max(0, int((h - k) * 0.38))
            for dx in range(-rad, rad + 1):
                for dy in range(-rad, rad + 1):
                    if abs(dx) + abs(dy) <= rad + (k % 2):
                        w.set(x + dx, y + dy, G + 2 + k, PINE)
        return
    th = 3 if small else rng.randint(4, 6)
    w.box(TRUNK, x, x + 1, y, y + 1, G, G + th)
    rad = 2.2 if small else rng.uniform(2.8, 3.6)
    cz = G + th + rad * 0.6
    r = int(rad) + 1
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            for dz in range(-r, r + 1):
                d = math.sqrt(dx * dx + dy * dy + (dz * 1.2) ** 2)
                if d < rad + rng.uniform(-0.5, 0.4):
                    w.set(x + dx, y + dy, int(cz + dz), LEAVES)


def build_market(w, ox, oy, rng):
    for i, (sx0, sy0) in enumerate([(ox + 1, oy + 1), (ox + 6, oy + 6)]):
        col = rng.choice([AWN_A, ROOF2, GOODS3, WATER])
        # Counter and goods.
        w.box(WOOD, sx0, sx0 + 5, sy0 + 3, sy0 + 4, G, G + 2)
        for xx in range(sx0, sx0 + 5):
            w.set(xx, sy0 + 3, G + 2, rng.choice([GOODS1, GOODS2, GOODS3, AWN_A]))
        # Posts.
        for px, py in [(sx0, sy0), (sx0 + 4, sy0), (sx0, sy0 + 4), (sx0 + 4, sy0 + 4)]:
            w.box(WOOD, px, px + 1, py, py + 1, G, G + 5)
        # Striped sloping awning.
        for k, yy in enumerate(range(sy0 - 1, sy0 + 6)):
            zz = G + 6 - (k // 3)
            for xx in range(sx0 - 1, sx0 + 6):
                w.set(xx, yy, zz, col if xx % 2 == 0 else AWN_B)
        w.lantern(sx0 + 4, sy0 + 5, G + 4, radius=6)
    # Barrels and crates.
    for bx, by in [(ox + 9, oy + 1), (ox + 10, oy + 2), (ox + 1, oy + 9)]:
        w.box(WOOD, bx, bx + 1, by, by + 1, G, G + 2)
    w.box(GOODS3, ox + 2, ox + 4, oy + 10, oy + 11, G, G + 1)
    # A string of festival lights across the street.
    w.box(WOOD, ox + 11, ox + 12, oy + 11, oy + 12, G, G + 9)
    for k in range(0, 12, 3):
        sag = int(round(2 * math.sin(math.pi * k / 12)))
        w.lantern(ox + k, oy + 12 + 1, G + 8 - sag, radius=4, strength=0.6)


def build_square(w, ox, oy, rng, well):
    w.box(PATH, ox, ox + PLOT, oy, oy + PLOT, G - 1, G)
    if well:
        cx, cy = ox + 6, oy + 6
        w.box(STONE, cx - 2, cx + 2, cy - 2, cy + 2, G, G + 2)
        w.box(WATER, cx - 1, cx + 1, cy - 1, cy + 1, G, G + 2)
        for px, py in [(cx - 2, cy - 2), (cx + 1, cy - 2), (cx - 2, cy + 1), (cx + 1, cy + 1)]:
            w.box(WOOD, px, px + 1, py, py + 1, G + 2, G + 6)
        for k in range(3):
            w.box(SLATE, cx - 3 + k, cx + 3 - k, cy - 3 + k, cy + 3 - k, G + 6 + k, G + 7 + k)
        w.lantern(cx + 2, cy + 2, G + 3, radius=6)
    else:
        tree(w, ox + 6, oy + 5, rng)
        w.box(WOOD, ox + 2, ox + 5, oy + 10, oy + 11, G, G + 1)
    for _ in range(10):
        w.set(ox + rng.randrange(PLOT), oy + rng.randrange(PLOT), G - 1, GRASS)


def build_walls(w, rng):
    # Curtain wall: tall at the back, low at the front so the town shows.
    back, front = G + 11, G + 6
    segments = [  # x0, x1, y0, y1, top
        (11, 123, 11, 13, back), (11, 13, 11, 93, back),
        (11, 123, 91, 93, front), (121, 123, 11, 93, front),
    ]
    for x0, x1, y0, y1, top in segments:
        w.box(STONE, x0, x1, y0, y1, G, top)
        for x in range(x0, x1):
            for y in range(y0, y1):
                if (x + y) % 2 == 0 and (x in (x0, x1 - 1) and x1 - x0 == 2 or y in (y0, y1 - 1) and y1 - y0 == 2):
                    w.set(x, y, top, STONE)
    # Wall torches and patrolling guards.
    for x in range(20, 120, 14):
        w.lantern(x, 93, G + 4, radius=6, strength=0.8)
    for y in range(20, 90, 14):
        w.lantern(123, y, G + 4, radius=6, strength=0.8)
    # Soldiers march the wall walks in an endless patrol, popping in and
    # out of the towers at each end of a segment.
    for x0, y0, x1, y1, z, spacing in [
        (16, 11.5, 118, 11.5, back, 13), (11.5, 88, 11.5, 16, back, 15),
        (16, 91.5, 59, 91.5, front, 11), (118, 91.5, 76, 91.5, front, 11),
        (121.5, 16, 121.5, 88, front, 12),
    ]:
        count = int(math.hypot(x1 - x0, y1 - y0) // spacing) + 1
        for k in range(count):
            w.walkers.append(dict(kind="soldier", mode="march", x0=x0, y0=y0, x1=x1, y1=y1, z=z,
                                  spacing=spacing, span=count * spacing, offset=k * spacing,
                                  coat=GREENS[3],
                                  phase=0.0, torch=k % 3 == 0))
    # Corner towers.
    for cx, cy, tall in [(12, 12, 20), (122, 12, 14), (12, 92, 14), (122, 92, 10)]:
        x0, y0 = cx - 3, cy - 3
        w.box(STONE, x0, x0 + 6, y0, y0 + 6, G - 2, G + tall)
        for zz in range(G + 5, G + tall - 2, 5):
            w.set(x0 + 5, y0 + 2, zz, DARK)
            w.set(x0 + 2, y0 + 5, zz, DARK)
            w.set(x0 + 5, y0 + 2, zz + 1, DARK)
            w.set(x0 + 2, y0 + 5, zz + 1, DARK)
        tower_top(w, x0, x0 + 6, y0, y0 + 6, G + tall, rng, roof=SLATE, steep=2)
    # Gatehouse on the front wall.
    gx = 61
    for tx in (gx, gx + 9):
        w.box(STONE, tx, tx + 5, 89, 95, G - 2, G + 13)
        tower_top(w, tx, tx + 5, 89, 95, G + 13, rng, roof=SLATE, steep=2)
        w.window(tx + 2, 94, G + 8, rng, tall=2, lit_chance=1)
    w.box(STONE, gx + 5, gx + 9, 89, 95, G + 6, G + 10)
    w.box(EMPTY, gx + 5, gx + 9, 89, 95, G, G + 6)
    w.box(IRON, gx + 5, gx + 9, 92, 93, G + 3, G + 6)
    for xx in range(gx + 5, gx + 9):
        w.set(xx, 94, G + 6, STONE2)
        if xx % 2 == 0:
            w.set(xx, 94, G + 10, STONE)
    w.lantern(gx - 1, 95, G + 5, radius=9, strength=1.2)
    # Sentries at the gate, and a squad drilling up and down the main street.
    for sx_ in (gx + 4, gx + 9):
        w.walkers.append(dict(kind="soldier", mode="stand", x0=sx_, y0=96, x1=sx_, y1=96, z=G,
                              coat=GREENS[4], phase=sx_ * 0.1, torch=True))
    for k in range(5):
        w.walkers.append(dict(kind="soldier", mode="pingpong", x0=gx + 6.5, y0=60 + k * 2,
                              x1=gx + 6.5, y1=30 + k * 2, z=G, coat=GREENS[3], phase=0.0,
                              torch=False, cycles=1))
    w.lantern(gx + 14, 95, G + 5, radius=9, strength=1.2)
    # Drawbridge across the moat.
    w.box(WOOD, gx + 5, gx + 9, 93, 99, G - 1, G)
    w.box(PATH, gx + 5, gx + 9, 99, ISL_Y, G - 1, G)


def build_scene(selected, seed=1729):
    import random
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    w = World(SX, SY, SZ)
    build_island(w, nrng)
    # Town ground: cobbled streets inside the walls.
    w.box(COBBLE, 13, 121, 13, 91, G - 1, G)
    carve_moat(w)
    peak = max([day["contributionCount"] for day in selected] + [1])
    days = iter(selected)
    wells = 0
    for j, row in enumerate(LAYOUT):
        for i, kind in enumerate(row):
            ox, oy = PLOT0 + i * PITCH, PLOT0 + j * PITCH
            if kind == "B":
                day = next(days, None)
                if day is None or not day["contributionCount"]:
                    build_ruin(w, ox, oy, rng)
                    continue
                level = max(1, LEVELS[day["contributionLevel"]])
                activity = day["contributionCount"] / peak
                w.box(GRASS, ox, ox + PLOT, oy, oy + PLOT, G - 1, G)
                if level >= 3:
                    build_tower(w, ox, oy, activity, level, rng, peak)
                else:
                    build_house(w, ox, oy, activity, level, rng)
            elif kind == "S":
                build_market(w, ox, oy, rng)
            else:
                build_square(w, ox, oy, rng, well=wells == 0)
                wells += 1
    build_walls(w, rng)
    # Pines and boulders on the island rim outside the walls.
    for _ in range(46):
        x, y = rng.randrange(1, ISL_X - 1), rng.randrange(1, ISL_Y - 1)
        rim = x < 5 or y < 5 or x > 128 or y > 98
        if rim and w.mat[x, y, G - 1] == GRASS and w.mat[x, y, G] == EMPTY:
            if rng.random() < 0.75:
                tree(w, x, y, rng, pine=True)
            else:
                w.set(x, y, G, ROCK)
    # Townsfolk walking the streets.
    coats = ["#c0564c", "#5a86c0", "#c8a548", "#6aa070", "#9a6ac0", "#d0803a", "#d8d0c0"]
    for k in range(44):
        i, j = rng.randrange(7), rng.randrange(5)
        ox, oy = PLOT0 + i * PITCH, PLOT0 + j * PITCH
        if rng.random() < 0.5:
            x0, y0 = ox - 1.5, oy + rng.uniform(0, 8)
            x1, y1 = x0, y0 + rng.uniform(4, 8)
        else:
            x0, y0 = ox + rng.uniform(0, 8), oy - 1.5
            x1, y1 = x0 + rng.uniform(4, 8), y0
        w.walkers.append(dict(kind="folk", mode="pingpong", x0=x0, y0=y0, x1=x1, y1=y1, z=G,
                              coat=rng.choice(coats), phase=rng.random(), torch=rng.random() < 0.15,
                              hair=rng.choice(["#2a1d14", "#5a3a20", "#a08050", "#1a1a1a", "#7a2a1a"]),
                              cycles=rng.choice([1, 2])))
    # Travellers outside the walls, and folk crossing the drawbridge.
    hairs = ["#2a1d14", "#5a3a20", "#a08050", "#1a1a1a", "#7a2a1a"]
    outside = [(20, 101, 50, 101), (80, 101, 112, 101), (40, 102.5, 58, 102.5),
               (130.5, 20, 130.5, 40), (130.5, 58, 130.5, 84), (66.5, 104, 66.5, 86),
               (68, 86, 68, 103)]
    for x0, y0, x1, y1 in outside:
        w.walkers.append(dict(kind="folk", mode="pingpong", x0=x0, y0=y0, x1=x1, y1=y1, z=G,
                              coat=rng.choice(coats), phase=rng.random(), torch=rng.random() < 0.4,
                              hair=rng.choice(hairs), cycles=1))
    # Shoppers milling about the market stalls and squares.
    for j, row in enumerate(LAYOUT):
        for i, kind in enumerate(row):
            if kind in "S.":
                ox, oy = PLOT0 + i * PITCH, PLOT0 + j * PITCH
                for _ in range(2):
                    x0, y0 = ox + rng.uniform(2, 8), oy + rng.uniform(9, 11)
                    w.walkers.append(dict(kind="folk", mode="pingpong", x0=x0, y0=y0,
                                          x1=x0 + rng.uniform(-3, 3), y1=y0, z=G,
                                          coat=rng.choice(coats), phase=rng.random(),
                                          torch=False, hair=rng.choice(hairs), cycles=1))
    return w


# ---------------------------------------------------------------------------
# Rasteriser
# ---------------------------------------------------------------------------

K = 3                       # screen pixels per unit of the isometric grid
NW, NH = 520, 460           # canvas size in grid units
WIDTH, HEIGHT = NW * K, NH * K
OX, OY = (2 * SY + (NW - 2 * (SX + SY)) // 2 + 4) * K, 205 * K
LIGHT = np.array([-1.0, 0.55, 0.9])
PX = 1.0 / (2 * K)          # one screen pixel, measured in voxels


def project(x, y, z):
    return 2 * K * (x - y) + OX, K * (x + y) - 2 * K * z + OY


def cube_template():
    """Pixels of one voxel's on-screen hexagon as (face, dx, dy, s, t).

    Face 0 is the top, 1 the +y side (left) and 2 the +x side (right). s and t
    place the pixel on its face, each running 0..1: along x and y on the top,
    along the face and down from its top edge on the sides.
    """
    cells = []
    for dy in range(4 * K):
        for dx in range(-2 * K, 2 * K):
            a, b = dx + 0.5, dy + 0.5
            if abs(a) / (2 * K) + abs(b - K) / K < 1:
                cells.append((0, dx, dy, (a / (2 * K) + b / K) / 2, (b / K - a / (2 * K)) / 2))
            elif a < 0 and 2 * K + a / 2 < b < 4 * K + a / 2:
                s = 1 + a / (2 * K)
                cells.append((1, dx, dy, s, (b - K * (1 + s)) / (2 * K)))
            elif a > 0 and 2 * K - a / 2 < b < 4 * K - a / 2:
                s = 1 - a / (2 * K)
                cells.append((2, dx, dy, s, (b - K * (1 + s)) / (2 * K)))
    return cells


def hash_noise(*arrays):
    arrays = np.broadcast_arrays(*[np.asarray(a).astype(np.int64) for a in arrays])
    h = np.zeros(arrays[0].shape, np.uint32)
    for k, a in enumerate(arrays):
        h ^= (a * [73856093, 19349663, 83492791, 2654435761][k] & 0xFFFFFFFF).astype(np.uint32)
        h = (h ^ (h >> 13)) * np.uint32(1274126177)
    return (h & 0xFFFF).astype(np.float32) / 65535.0


def render_static(w):
    mat = w.mat
    solid = mat != EMPTY
    pad = np.pad(solid, ((0, 1), (0, 1), (0, 1)))
    open_x = ~pad[1:, :-1, :-1]
    open_y = ~pad[:-1, 1:, :-1]
    open_z = ~pad[:-1, :-1, 1:]
    visible = solid & (open_x | open_y | open_z)
    xs, ys, zs = np.nonzero(visible)
    m = mat[xs, ys, zs]
    wid = w.wid[xs, ys, zs]
    faces = [open_z[xs, ys, zs], open_y[xs, ys, zs], open_x[xs, ys, zs]]

    # Moon shadows via a height map march.
    height = np.where(solid.any(axis=2), SZ - np.argmax(solid[:, :, ::-1], axis=2), 0)
    lit = np.ones(len(xs), np.float32)
    px, py, pz = xs + 0.5, ys + 0.5, zs + 1.0
    for k in np.arange(1.0, 90.0, 1.0):
        qx = np.floor(px + LIGHT[0] * k).astype(int)
        qy = np.floor(py + LIGHT[1] * k).astype(int)
        qz = pz + LIGHT[2] * k
        ok = (qx >= 0) & (qx < SX) & (qy >= 0) & (qy < SY)
        hit = np.zeros(len(xs), bool)
        hit[ok] = height[qx[ok], qy[ok]] > qz[ok]
        lit = np.where(hit, 0.0, lit)
        if qz.min() > SZ:
            break

    # Ambient occlusion from the 3x3 cells above each voxel.
    occ = np.zeros(len(xs), np.float32)
    padded = np.pad(solid, 1)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            occ += padded[xs + 1 + dx, ys + 1 + dy, np.minimum(zs + 2, SZ + 1)]
    ao = 1.0 - 0.06 * occ

    # Warm light from lanterns and windows.
    warm = np.zeros((len(xs), 3), np.float32)
    for lx, ly, lz, radius, strength, color in w.lights:
        near = (np.abs(xs - lx) < radius) & (np.abs(ys - ly) < radius) & (np.abs(zs - lz) < radius)
        idx = np.nonzero(near)[0]
        if not len(idx):
            continue
        d = np.sqrt((xs[idx] + 0.5 - lx) ** 2 + (ys[idx] + 0.5 - ly) ** 2 + (zs[idx] + 0.5 - lz) ** 2)
        f = np.clip(1 - d / radius, 0, 1) ** 2 * strength
        warm[idx] += f[:, None] * np.array(color, np.float32)[None, :] / 255.0

    base = PALETTE[m] * (0.95 + 0.1 * hash_noise(xs, ys, zs))[:, None]
    # Underside of the island fades into the night.
    under = np.clip((G - zs) / G, 0, 1)
    base *= (1 - 0.55 * under)[:, None]
    emissive = np.isin(m, list(EMISSIVE))

    canvas = np.zeros((HEIGHT, WIDTH, 3), np.float32)
    depth = np.full((HEIGHT, WIDTH), -1e9, np.float32)
    kind = np.zeros((HEIGHT, WIDTH), np.int32)       # material
    ident = np.zeros((HEIGHT, WIDTH), np.int32)      # light/window id
    glaze = np.ones((HEIGHT, WIDTH), np.float32)     # surface detail under glowing pixels
    sx, sy = project(xs, ys, zs)
    top = sy - 2 * K
    shade = [1.0, 0.74, 0.52]
    moon_tint = np.array([0.80, 0.86, 1.04], np.float32)
    cells = cube_template()
    for face in range(3):
        sel = np.nonzero(faces[face])[0]
        if not len(sel):
            continue
        light = shade[face] * (0.55 + 0.45 * lit[sel]) * ao[sel]
        if face == 2:
            light = shade[face] * ao[sel] * (0.8 + 0.2 * lit[sel])
        ms, fx, fy, fz = m[sel], xs[sel], ys[sel], zs[sel]
        lit_col = base[sel] * moon_tint * light[:, None]
        warm_col = warm[sel] * PALETTE[ms] * 0.6 * (1.0 if face == 0 else 0.8)
        own = PALETTE[ms] * (1.0 if face != 2 else 0.85)
        em = emissive[sel]
        dep_all = (fx + fy + fz).astype(np.float32)
        edge_x, edge_y = faces[2][sel], faces[1][sel]
        for f, dx, dy, s, t in cells:
            if f != face:
                continue
            X = sx[sel] + dx
            Y = top[sel] + dy
            tex = texture(ms, face, fx, fy, fz, s, t, X, Y, edge_x, edge_y)
            col = lit_col * tex[:, None] + warm_col
            col[em] = own[em] * tex[em, None]
            ok = (X >= 0) & (X < WIDTH) & (Y >= 0) & (Y < HEIGHT)
            X, Y, dep, colv = X[ok], Y[ok], dep_all[ok], col[ok]
            mm, ww, tt = ms[ok], wid[sel][ok], tex[ok]
            order = np.argsort(dep, kind="stable")
            X, Y, dep, colv, mm, ww, tt = (a[order] for a in (X, Y, dep, colv, mm, ww, tt))
            # Keep the nearest sample per pixel (last write wins after sort).
            flat = Y * WIDTH + X
            _, last = np.unique(flat[::-1], return_index=True)
            last = len(flat) - 1 - last
            px_, py_, fd = X[last], Y[last], dep[last]
            better = fd >= depth[py_, px_]
            px_, py_, last = px_[better], py_[better], last[better]
            depth[py_, px_] = dep[last]
            canvas[py_, px_] = colv[last]
            kind[py_, px_] = mm[last]
            ident[py_, px_] = ww[last]
            glaze[py_, px_] = tt[last]
    return canvas, depth, kind, ident, glaze


def texture(m, face, x, y, z, s, t, X, Y, edge_x, edge_y):
    """Brightness of the surface detail at one pixel of each voxel's face."""
    tex = np.ones(len(m), np.float32)
    grain = hash_noise(X, Y, 7)
    stone = (m == STONE) | (m == STONE2)
    roofs = np.isin(m, ROOFS + [SLATE])
    # The face's point in world space, for patterns that run across voxels.
    if face == 0:
        wx, wy, wz = x + s, y + t, z + 1.0
    elif face == 1:
        wx, wy, wz = x + s, y + 1.0, z + 1 - t
    else:
        wx, wy, wz = x + 1.0, y + s, z + 1 - t
    clumps = hash_noise(np.floor(wx * 3), np.floor(wy * 3), np.floor(wz * 3))
    if face:
        along = wx if face == 1 else wy
        # Ashlar in half-voxel courses, laid in running bond.
        course = np.floor(wz * 2)
        cu = wz * 2 - course
        run = along + 0.5 * (course % 2)
        block = np.floor(run)
        joint = (cu < 2 * PX) | (run - block < PX)
        ashlar = 0.9 + 0.16 * hash_noise(block, course, face) + 0.05 * grain
        ashlar = np.where(cu > 1 - 2 * PX, ashlar * 1.1, ashlar)
        tex = np.where(stone, np.where(joint, 0.68, ashlar), tex)
        # Overlapping roof tiles.
        tile = np.where(cu < 2 * PX, 0.7, np.where((run * 2) % 1 < 2 * PX, 0.85, 0.95 + 0.08 * grain))
        tex = np.where(roofs, tile, tex)
        tex = np.where(m == PLASTER, 0.95 + 0.07 * grain, tex)
        rock = (m == ROCK) | (m == ROCK2)
        tex = np.where(rock, 0.8 + 0.22 * clumps + 0.08 * grain, tex)
        planks = (m == WOOD) | (m == DOOR)
        seam = (along * 2) % 1 < 2 * PX
        streak = hash_noise(np.floor(along * 2 * K), face, 9)
        tex = np.where(planks, np.where(seam, 0.75, 0.9 + 0.14 * streak), tex)
        tex = np.where(m == TIMBER, 0.88 + 0.14 * streak, tex)
        # Leaded panes, and lanterns under an iron cap.
        lead = (s < PX) | (s > 1 - PX) | (t > 1 - 2 * PX)
        tex = np.where(m == WINDOW, np.where(lead, 0.32, 1.06 - 0.12 * t), tex)
        tex = np.where(m == LANTERN, np.where(t < 2 * PX, 0.4, 1.05 - 0.15 * t), tex)
        if face == 2:
            # A crisp highlight down each exposed front corner.
            rim = (s > 1 - PX) & edge_y & ~np.isin(m, list(EMISSIVE) + [LEAVES, PINE])
            tex = np.where(rim, tex * 1.16, tex)
    else:
        # Cobbles in staggered half-voxel rows.
        row = np.floor(wy * 2)
        run = wx * 2 + 0.5 * (row % 2)
        cell = np.floor(run)
        gap = (wy * 2 - row < 2 * PX) | (run - cell < 2 * PX)
        tex = np.where(m == COBBLE, np.where(gap, 0.6, 0.84 + 0.24 * hash_noise(cell, row, 3)), tex)
        # Flagstones on wall walks, tile rows on roofs.
        slab = (wx % 1 < PX) | (wy % 1 < PX)
        tex = np.where(stone, np.where(slab, 0.8, 0.94 + 0.08 * grain), tex)
        tiles = ((wx * 2) % 1 < 2 * PX) | ((wy * 2) % 1 < 2 * PX)
        tex = np.where(roofs, np.where(tiles, 0.84, 0.95 + 0.08 * grain), tex)
        grassy = (m == GRASS) | (m == MOSS)
        tex = np.where(grassy, 0.8 + 0.22 * clumps + 0.16 * grain, tex)
        tex = np.where(m == PATH, 0.88 + 0.2 * grain, tex)
        tex = np.where((m == WOOD) | (m == DOOR), np.where((wx * 2) % 1 < 2 * PX, 0.78, 0.95), tex)
        tex = np.where(m == LANTERN, 0.5, tex)
        # Light catches the front edges of every exposed top.
        rim = ((s > 1 - PX) & edge_x) | ((t > 1 - PX) & edge_y)
        rim &= ~np.isin(m, list(EMISSIVE) + [LEAVES, PINE, WATER])
        tex = np.where(rim, tex * 1.18, tex)
    leaves = (m == LEAVES) | (m == PINE)
    tex = np.where(leaves, 0.72 + 0.4 * clumps + 0.16 * grain, tex)
    return tex


# ---------------------------------------------------------------------------
# Sky, effects and animation
# ---------------------------------------------------------------------------

BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16.0


def sky(rng):
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    t = ys / HEIGHT
    stops = [(0.0, "#05070f"), (0.45, "#0d1430"), (0.78, "#1c1f45"), (1.0, "#2b2552")]
    cols = np.array([rgb(c) for _, c in stops], np.float32)
    pos = np.array([p for p, _ in stops])
    # Quantise to bands, dithered between neighbours.
    bands = 28
    v = t * bands + BAYER[ys % 4, xs % 4] - 0.5
    tq = np.clip(np.floor(v + 0.5) / bands, 0, 1)
    img = np.stack([np.interp(tq, pos, cols[:, k]) for k in range(3)], axis=-1)
    stars = []
    for _ in range(170):
        x, y = rng.randrange(NW) * K + 1, rng.randrange(int(NH * 0.85)) * K + 1
        stars.append((x, y, rng.random(), rng.choice([0.5, 0.7, 1.0, 1.0, 1.4])))
    return img.astype(np.float32), stars


def moon(img, cx, cy, r):
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    d = np.hypot(xs - cx, ys - cy)
    halo = np.clip(1 - (d - r) / (r * 2.2), 0, 1) ** 2 * 0.45
    halo = np.floor(halo * 12 + BAYER[ys % 4, xs % 4]) / 12
    img += halo[..., None] * np.array([70, 80, 120], np.float32)
    shade = np.clip(1 - np.hypot(xs - cx + r * 0.35, ys - cy + r * 0.35) / (r * 2.4), 0, 1)
    col = np.array([236, 230, 205], np.float32) * (0.78 + 0.22 * shade[..., None])
    for mx, my, mr in [(-4, -3, 3.5), (5, 2, 2.5), (1, 7, 2)]:
        k = r / 14
        dist = np.hypot(xs - cx - mx * k, ys - cy - my * k)
        crater = np.clip(mr * k - dist + 0.5, 0, 1) * 0.1
        col *= (1 - crater)[..., None]
    # Anti-aliased rim.
    cover = np.clip(r - d + 0.5, 0, 1)[..., None]
    img[:] = img * (1 - cover) + col * cover
    return d <= r


def clouds(img, rng, y_min, y_max, count, tones, width=(30, 90)):
    """Puffy cumulus: unions of circles with a flat base, lit from above."""
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    mask = np.zeros((HEIGHT, WIDTH), bool)
    for _ in range(count):
        cx, base = rng.uniform(-20, NW + 20), rng.uniform(y_min, y_max)
        span = rng.uniform(*width)
        for _ in range(int(span / 7) + 2):
            px = cx + rng.uniform(-span / 2, span / 2)
            r = rng.uniform(4, 6 + span / 7) * (1 - abs(px - cx) / span)
            if r < 2:
                continue
            py = base - r * rng.uniform(0.2, 0.6)
            x0, x1 = max(0, int((px - r) * K)), min(WIDTH, int((px + r) * K) + 1)
            y0, y1 = max(0, int((py - r) * K)), min(HEIGHT, int(base * K) + 1)
            if x0 >= x1 or y0 >= y1:
                continue
            sx, sy = xs[y0:y1, x0:x1], ys[y0:y1, x0:x1]
            mask[y0:y1, x0:x1] |= (((sx - px * K) ** 2 + ((sy - py * K) * 1.15) ** 2 <= (r * K) ** 2)
                                   & (sy <= base * K))
    lit = mask & ~np.roll(mask, 2 * K, axis=0)
    rim = mask & ~np.roll(mask, 4 * K, axis=0) & ~lit
    dark = mask & ~np.roll(mask, -3 * K, axis=0)
    img[mask] = tones[0]
    img[rim & (BAYER[ys % 4, xs % 4] < 0.5)] = tones[2] * 0.85 + tones[0] * 0.15
    img[lit] = tones[2]
    img[dark] = tones[1]
    return mask


def caption(username, total):
    """The label as an anti-aliased overlay: (alpha, colour, top-left)."""
    layer = Image.new("RGBA", (WIDTH, 30 * K))
    draw = ImageDraw.Draw(layer)
    title = ImageFont.load_default(size=7 * K)
    small = ImageFont.load_default(size=6 * K)
    x, y = 12 * K, 2 * K
    draw.text((x, y), f"{(username or '').lower()}  ·  last 20 days", font=title, fill=(214, 219, 238, 255))
    label = f"{total} contributions"
    draw.text((x, y + 10 * K), label, font=small, fill=(150, 160, 192, 255))
    x += draw.textlength(label, font=small) + 4 * K
    for lvl in range(5):
        x0 = int(x + lvl * 6 * K)
        draw.rounded_rectangle((x0, y + 11 * K + 2, x0 + 4 * K, y + 15 * K + 2), radius=2,
                               fill=tuple(rgb(GREENS[lvl])) + (255,))
    pixels = np.asarray(layer, np.float32)
    return pixels[..., 3:] / 255.0, pixels[..., :3], HEIGHT - 30 * K


def disc(frame, cx, cy, radius, color, alpha):
    """Blend a soft round puff into the frame."""
    x0, x1 = max(0, int(cx - radius - 1)), min(WIDTH, int(cx + radius + 2))
    y0, y1 = max(0, int(cy - radius - 1)), min(HEIGHT, int(cy + radius + 2))
    if x0 >= x1 or y0 >= y1:
        return
    ys, xs = np.mgrid[y0:y1, x0:x1]
    dist = np.hypot(xs + 0.5 - cx, ys + 0.5 - cy)
    a = np.clip((radius - dist) / (radius * 0.6) + 0.2, 0, 1)[..., None] * alpha
    frame[y0:y1, x0:x1] = frame[y0:y1, x0:x1] * (1 - a) + np.asarray(color, np.float32) * a


def render(data, output, username=None):
    import random
    selected = recent_days(data)
    if not selected:
        raise ValueError("Contribution calendar is empty")
    rng = random.Random(824)
    world = build_scene(selected)
    canvas, depth, kind, ident, glaze = render_static(world)
    background, stars = sky(rng)
    moon_mask = moon(background, 76 * K, 70 * K, 14 * K)
    clouds(background, rng, 95, 150, 7,
           [np.array(rgb(c), np.float32) for c in ("#1a1d3d", "#141733", "#2c3060")], (20, 60))
    clouds(background, rng, NH - 40, NH - 20, 12,
           [np.array(rgb(c), np.float32) for c in ("#2a2955", "#211f45", "#4b4c86")], (50, 110))
    clouds(background, rng, NH + 5, NH + 25, 14,
           [np.array(rgb(c), np.float32) for c in ("#35336a", "#2a2856", "#5d5f9e")], (60, 130))
    scene = depth > -1e8
    total = sum(day["contributionCount"] for day in selected)
    label_alpha, label_color, label_y = caption(username, total)

    window_phase = {}
    wrng = random.Random(3)
    for i in range(1, world.windows + 1):
        window_phase[i] = (wrng.random() * math.tau, wrng.choice([1, 1, 1, 2, 3]), wrng.random())
    win_ids = np.unique(ident[(kind == WINDOW) & (ident > 0)])
    window_px = {i: np.nonzero(ident == i) for i in win_ids}
    lantern = (kind == LANTERN) & scene
    beacon = (kind == BEACON) & scene
    water = (kind == WATER) & scene
    fall_y, fall_x = np.nonzero((kind == FALL) & scene)
    ys_grid, xs_grid = np.mgrid[0:HEIGHT, 0:WIDTH]
    glints = hash_noise(ys_grid, xs_grid // 4, 11)
    fall_seed = (hash_noise(fall_x // 2, 0, 5) * 9 * K).astype(int)

    bats = [(rng.uniform(40, 140) * K, rng.uniform(40, 100) * K, rng.uniform(14, 30) * K,
             rng.random() * math.tau, rng.choice([1, -1])) for _ in range(4)]
    fx0, fy0 = project(ISL_X, 48, 2)

    frames = []
    n_frames = 96
    for f in range(n_frames):
        phase = math.tau * f / n_frames
        img = background.copy()
        for x, y, p, b in stars:
            tw = 0.55 + 0.45 * math.sin(phase * 2 + p * math.tau)
            if moon_mask[y, x]:
                continue
            col = np.array([200, 210, 255], np.float32) * min(1, b * tw)
            img[y, x] = np.maximum(img[y, x], col)
            if b >= 1.0:
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    img[y + dy, x + dx] = np.maximum(img[y + dy, x + dx], col * 0.45)
            if b > 1.2 and tw > 0.8:
                for k in (2, 3):
                    for dx, dy in [(-k, 0), (k, 0), (0, -k), (0, k)]:
                        if 0 <= x + dx < WIDTH and 0 <= y + dy < HEIGHT:
                            img[y + dy, x + dx] = np.maximum(img[y + dy, x + dx], col * (0.5 / k))
        # Bats circling in front of the moon.
        for bx, by, br, bp, bd in bats:
            a = phase * bd + bp
            x = int(bx + br * math.cos(a))
            y = int(by + br * 0.45 * math.sin(a * 2))
            up = (f // 2 + int(bp * 10)) % 2
            wing = [(1, 0), (2, -1), (3, -2), (4, -2)] if up else [(1, 0), (2, 0), (3, 1), (4, 2)]
            pix = [(0, 0), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1)]
            pix += [(dx, dy) for dx, dy in wing] + [(-dx, dy) for dx, dy in wing]
            for dx, dy in pix:
                if 0 <= x + dx < WIDTH and 0 <= y + dy < HEIGHT:
                    img[y + dy, x + dx] = [12, 10, 18]
        frame = np.where(scene[..., None], canvas, img)
        frame_depth = depth.copy()
        glow = np.zeros_like(frame)
        # Candle flicker.
        for i, (yy, xx) in window_px.items():
            p, speed, base = window_phase.get(i, (0, 1, 0.5))
            k = 0.86 + 0.1 * math.sin(phase * speed + p) + 0.04 * math.sin(phase * speed * 3 + p * 2)
            col = np.array([255, 196 + 20 * base, 100 + 30 * base], np.float32) * k
            lit = col[None, :] * glaze[yy, xx][:, None]
            frame[yy, xx] = lit
            glow[yy, xx] = lit
        glow[lantern] = PALETTE[LANTERN] * glaze[lantern][:, None]
        frame[beacon] = [255, 150 + 60 * (0.5 + 0.5 * math.sin(phase * 4)), 60]
        glow[beacon] = [255, 160, 60]
        glow[moon_mask] = [90, 90, 80]
        # Glints on the moat and a falling waterfall.
        sp = water & (((glints + f / n_frames) % 1.0) < 0.04)
        frame[sp] = [150, 190, 220]
        if len(fall_y):
            stripe = (fall_y - f * 3 * K + fall_seed) % (9 * K)
            frame[fall_y, fall_x] = np.where((stripe < 2 * K)[:, None], [190, 225, 250],
                                             np.where((stripe < 5 * K)[:, None], [90, 150, 205],
                                                      [50, 100, 160]))
        # Mist where the waterfall leaves the island.
        mist_rng = random.Random(77)
        for k in range(26):
            p = (f / n_frames + k / 26) % 1.0
            mx = fx0 + (mist_rng.uniform(-6, 6) + p * mist_rng.uniform(-8, 8)) * K
            my = fy0 + (p * 26 + mist_rng.uniform(-2, 2)) * K
            disc(frame, mx, my, K * (1.2 + 2 * p), [170, 200, 230], (1 - p) * 0.3)
        # Waving flags.
        for fx, fy, fz, length, color in world.flags:
            draw_flag(frame, frame_depth, fx, fy, fz, length, color, phase)
        # Chimney smoke.
        for k, (cx, cy, cz) in enumerate(world.smoke):
            X0, Y0 = project(cx, cy, cz)
            for puff in range(5):
                p = (f / n_frames * 2 + puff / 5 + k * 0.17) % 1.0
                X = X0 + (p * 10 + math.sin(p * 6 + k) * 2) * K
                Y = Y0 - p * 22 * K
                disc(frame, X, Y, K * (0.6 + p * 1.6), [120, 120, 140], 0.5 * (1 - p))
        # People.
        for wk in world.walkers:
            draw_walker(frame, frame_depth, glow, wk, f, n_frames, phase)
        # Bloom, blurred at grid resolution and scaled back up smoothly.
        g = Image.fromarray(np.clip(glow, 0, 255).astype(np.uint8)).resize((NW, NH), Image.Resampling.BOX)
        bloom = (np.asarray(g.filter(ImageFilter.GaussianBlur(3)), np.float32) * 1.9 +
                 np.asarray(g.filter(ImageFilter.GaussianBlur(9)), np.float32) * 1.2)
        bloom = np.asarray(Image.fromarray(np.clip(bloom, 0, 255).astype(np.uint8))
                           .resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR), np.float32)
        frame = frame + bloom * np.array([1.0, 0.85, 0.6])
        # Caption.
        band = frame[label_y:]
        band[:] = band * (1 - label_alpha) + label_color * label_alpha
        frames.append(Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8)))

    # Shared palette prevents colour shimmer between frames.
    sample = Image.new("RGB", (WIDTH, HEIGHT * 3))
    for k, idx in enumerate([0, n_frames // 3, 2 * n_frames // 3]):
        sample.paste(frames[idx], (0, HEIGHT * k))
    palette = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    indexed = [np.asarray(fr.quantize(palette=palette, dither=Image.Dither.NONE)) for fr in frames]
    # Unchanged pixels become transparent so each frame stores only what moved.
    out = []
    for k, idx in enumerate(indexed):
        data = idx.copy()
        if k:
            data[idx == indexed[k - 1]] = 255
        im = Image.frombytes("P", (WIDTH, HEIGHT), data.astype(np.uint8).tobytes())
        im.putpalette(palette.getpalette()[:765] + [0, 0, 0])
        out.append(im)
    output.parent.mkdir(parents=True, exist_ok=True)
    out[0].save(output, save_all=True, append_images=out[1:], duration=80, loop=0,
                optimize=False, transparency=255, disposal=1)
    return frames


def draw_flag(frame, depth, fx, fy, fz, length, color, phase):
    """A pennant rippling from the top of its pole."""
    c = np.array(rgb(color), np.float32)
    X, Y = (int(round(v)) for v in project(fx + 0.5, fy + 0.5, fz))
    d = fx + fy + fz
    pole = np.array([120, 110, 100], np.float32)
    for yy in range(-K, 4 * K):
        put(frame, depth, X, Y + yy, d, pole)
    put(frame, depth, X, Y - K - 1, d, np.array([220, 200, 120], np.float32))
    width, tall = int(length * 2.2 * K), 2 * K + 1
    for i in range(1, width):
        wave = math.sin(phase * 2 - i / (2.2 * K) * 0.9)
        lift = int(round(wave * 1.2 * K * min(1.0, i / (2 * K))))
        k = 0.8 + 0.25 * wave
        rows = tall - int(round(i / width * K))    # tapering to the tip
        for j in range(rows):
            edge = 1.15 if j == 0 else 0.85 if j == rows - 1 else 1.0
            put(frame, depth, X + i, Y + j + lift, d + 50, np.clip(c * k * edge, 0, 255))


SKIN = np.array([215, 175, 135], np.float32)
HOSE = np.array([62, 52, 46], np.float32)
BOOTS = np.array([45, 36, 30], np.float32)
STEEL = np.array([175, 182, 196], np.float32)
SHAFT = np.array([120, 92, 62], np.float32)


def draw_walker(frame, depth, glow, wk, f, n, phase):
    """A small walking figure: townsfolk or a spear-carrying soldier."""
    x0, y0, x1, y1 = wk["x0"], wk["y0"], wk["x1"], wk["y1"]
    length = math.hypot(x1 - x0, y1 - y0)
    moving = wk["mode"] != "stand"
    if wk["mode"] == "march":
        # Each soldier advances exactly two gaps per loop, so the patrol tiles seamlessly.
        pos = (wk["offset"] + 2 * wk["spacing"] * f / n) % wk["span"]
        if pos < 1.5 or pos > length - 1.5:
            return
        t = pos / length
        sx_, sy_ = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        step = (f // 2) % 4
    elif wk["mode"] == "pingpong":
        t = (wk.get("cycles", 1) * f / n + wk["phase"]) % 1.0
        s = 1 - abs(2 * t - 1)
        sx_, sy_ = x0 + (x1 - x0) * s, y0 + (y1 - y0) * s
        step = (f // 2 + int(wk["phase"] * 8)) % 4
    else:
        sx_, sy_, step = x0, y0, 0
    X, Y = (int(round(v)) for v in project(sx_ + 0.5, sy_ + 0.5, wk["z"]))
    d = sx_ + sy_ + wk["z"] + 0.6
    soldier = wk["kind"] == "soldier"
    coat = np.array(rgb(wk["coat"]), np.float32) * (1.15 if soldier else 1.0)

    def dot(dx, h, color):
        put(frame, depth, X + dx, Y - h, d, np.clip(color, 0, 255))

    def span(h, a, b, color, lit=True):
        # Lit from the moon on the left, shaded on the right.
        for dx in range(a, b + 1):
            k = (dx - a) / max(1, b - a)
            dot(dx, h, color * ((1.15 if k < 0.3 else 1.0 if k < 0.7 else 0.72) if lit else 1.0))

    # Legs scissor as the figure walks.
    swing = [0, 2, 0, -2][step] if moving else 0
    for h in range(1, 7):
        off = int(round(swing * (7 - h) / 6))
        leg = BOOTS if h <= 2 else HOSE
        for dx in (-2, -1):
            dot(dx + off, h, leg)
        for dx in (1, 2):
            dot(dx - off, h, leg * 0.8)
    # Coat, belt and shoulders.
    for h in range(7, 15):
        span(h, -3, 3, coat * (0.8 if h == 9 else 1.0))
    span(15, -2, 2, coat)
    if soldier:
        dot(0, 9, np.array([200, 190, 120], np.float32))  # belt buckle
    # Arms swing opposite the legs; a soldier's right hand grips the spear.
    arm = -swing // 2 if moving else 0
    for h in range(9, 15):
        dot(-4 + int(round(arm * (15 - h) / 6)), h, coat * 0.9)
    dot(-4 + arm, 8, SKIN * 0.9)
    if soldier:
        for h, dx in ((14, 4), (13, 4), (12, 5)):
            dot(dx, h, coat * 0.6)
        dot(5, 11, SKIN * 0.8)
    else:
        for h in range(9, 15):
            dot(4 - int(round(arm * (15 - h) / 6)), h, coat * 0.6)
        dot(4 - arm, 8, SKIN * 0.7)
    # Head.
    for h, (a, b) in zip(range(16, 22), [(-1, 1), (-2, 2), (-2, 2), (-2, 2), (-2, 2), (-1, 1)]):
        span(h, a, b, SKIN)
    if soldier:
        for h, (a, b) in zip(range(20, 24), [(-3, 3), (-2, 2), (-2, 2), (-1, 1)]):
            span(h, a, b, STEEL)
        plume = np.array([200, 50, 45], np.float32)
        for dx, h in ((0, 24), (1, 24), (1, 25), (2, 25), (2, 26), (3, 26)):
            dot(dx, h, plume)
        # Spear held upright, with a glinting tip.
        for h in range(3, 34):
            dot(6, h, SHAFT)
        glint = 1.0 + 0.4 * max(0.0, math.sin(phase * 2 + d * 0.3))
        for h, (a, b) in zip(range(34, 39), [(5, 7), (5, 7), (6, 6), (6, 6), (6, 6)]):
            span(h, a, b, STEEL * glint, lit=False)
    else:
        hair = np.array(rgb(wk.get("hair", "#2a1d14")), np.float32)
        for h, (a, b) in zip(range(20, 23), [(-2, 2), (-2, 2), (-1, 1)]):
            span(h, a, b, hair)
        dot(2, 19, hair * 0.8)
        dot(2, 18, hair * 0.8)
    if wk["torch"]:
        fl = 0.8 + 0.2 * math.sin(phase * 7 + wk["phase"] * 9 + d)
        flame = np.array([255, 190, 80], np.float32) * fl
        core = np.array([255, 235, 160], np.float32) * fl
        tx = -6 if soldier else 6
        for h in range(11, 18):
            dot(tx, h, np.array([90, 60, 40], np.float32))
        for h, (a, b), col in ((18, (-1, 1), flame), (19, (-1, 1), core), (20, (-1, 1), flame),
                               (21, (0, 0), flame), (22, (0, 0), flame * 0.8)):
            for dx in range(a, b + 1):
                dot(tx + dx, h, col)
        if 1 <= X + tx < WIDTH - 1 and 22 <= Y < HEIGHT:
            glow[Y - 22:Y - 17, X + tx - 1:X + tx + 2] = flame * 1.8


def put(frame, depth, x, y, d, color):
    x, y = int(x), int(y)
    if 0 <= x < WIDTH and 0 <= y < HEIGHT and d >= depth[y, x]:
        frame[y, x] = color
        depth[y, x] = d


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="amriteshdasgupta")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "market.gif")
    parser.add_argument("--fixture", type=Path, help="Render from a saved calendar JSON instead of the API")
    args = parser.parse_args()
    data = json.loads(args.fixture.read_text()) if args.fixture else calendar(args.username)
    render(data, args.output, args.username)
    days = recent_days(data)
    print(f"Rendered {len(days)} days ({days[0]['date']} to {days[-1]['date']}) to {args.output}")
