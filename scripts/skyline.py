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
from PIL import Image, ImageFilter

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

# 3x5 pixel font for the caption.
FONT = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011",
    "D": "110101101101110", "E": "111100110100111", "F": "111100110100100",
    "G": "011100101101011", "H": "101101111101101", "I": "111010010010111",
    "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010",
    "P": "110101110100100", "Q": "010101101110011", "R": "110101110101101",
    "S": "011100010001110", "T": "111010010010010", "U": "101101101101111",
    "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", "0": "111101101101111",
    "1": "010110010010111", "2": "110001010100111", "3": "110001010001110",
    "4": "101101111001001", "5": "111100110001110", "6": "011100111101111",
    "7": "111001010010010", "8": "111101111101111", "9": "111101111001110",
    "-": "000000111000000", ".": "000000000000010", ":": "000010000010000",
    "/": "001001010100100", " ": "000000000000000", "'": "010010000000000",
    "+": "000010111010000",
}


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

WIDTH, HEIGHT, SCALE = 520, 460, 3
OX, OY = 2 * SY + (WIDTH - 2 * (SX + SY)) // 2 + 4, 205
LIGHT = np.array([-1.0, 0.55, 0.9])


def project(x, y, z):
    return 2 * (x - y) + OX, (x + y) - 2 * z + OY


def hash_noise(*arrays):
    arrays = np.broadcast_arrays(*[np.asarray(a, np.int64) for a in arrays])
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

    base = PALETTE[m].copy()
    jitter = 0.9 + 0.2 * hash_noise(xs, ys, zs)
    base *= jitter[:, None]
    # Underside of the island fades into the night.
    under = np.clip((G - zs) / G, 0, 1)
    base *= (1 - 0.55 * under)[:, None]
    emissive = np.isin(m, list(EMISSIVE))

    canvas = np.zeros((HEIGHT, WIDTH, 3), np.float32)
    depth = np.full((HEIGHT, WIDTH), -1e9, np.float32)
    kind = np.zeros((HEIGHT, WIDTH), np.int32)       # material
    ident = np.zeros((HEIGHT, WIDTH), np.int32)      # light/window id
    sx, sy = project(xs, ys, zs)
    shade = [1.0, 0.74, 0.52]
    moon_tint = np.array([0.80, 0.86, 1.04], np.float32)
    for face, (rows, cols) in enumerate([((0, 1), (0, 1, 2, 3)), ((2, 3), (0, 1)), ((2, 3), (2, 3))]):
        sel = np.nonzero(faces[face])[0]
        if not len(sel):
            continue
        light = shade[face] * (0.55 + 0.45 * lit[sel]) * ao[sel]
        if face == 2:
            light = shade[face] * ao[sel] * (0.8 + 0.2 * lit[sel])
        for r in rows:
            for c in cols:
                col = base[sel] * moon_tint * light[:, None]
                tex = texture(m[sel], face, xs[sel], ys[sel], zs[sel], r, c)
                col *= tex[:, None]
                col += warm[sel] * PALETTE[m[sel]] * 0.6 * (1.0 if face == 0 else 0.8)
                em = emissive[sel]
                col[em] = PALETTE[m[sel][em]] * (1.0 if face != 2 else 0.85)
                X = sx[sel] - 2 + c
                Y = sy[sel] - 2 + r
                dep = (xs[sel] + ys[sel] + zs[sel]).astype(np.float32) + (0.01 if face else 0.0)
                ok = (X >= 0) & (X < WIDTH) & (Y >= 0) & (Y < HEIGHT)
                X, Y, dep, colv = X[ok], Y[ok], dep[ok], col[ok]
                mm, ww = m[sel][ok], wid[sel][ok]
                order = np.argsort(dep, kind="stable")
                X, Y, dep, colv, mm, ww = X[order], Y[order], dep[order], colv[order], mm[order], ww[order]
                # Keep the nearest sample per pixel (last write wins after sort).
                flat = Y * WIDTH + X
                _, last = np.unique(flat[::-1], return_index=True)
                last = len(flat) - 1 - last
                fx, fy, fd = X[last], Y[last], dep[last]
                better = fd >= depth[fy, fx]
                fx, fy, last = fx[better], fy[better], last[better]
                depth[fy, fx] = dep[last]
                canvas[fy, fx] = colv[last]
                kind[fy, fx] = mm[last]
                ident[fy, fx] = ww[last]
    return canvas, depth, kind, ident


def texture(m, face, x, y, z, r, c):
    t = np.ones(len(m), np.float32)
    along = np.where(face == 1, x, y) if face else x
    # Brick courses on stone walls.
    stone = (m == STONE) | (m == STONE2)
    if face:
        course = z % 2
        joint = ((along * 2 + c + course * 2) % 4 == 0)
        mortar = (r == 3) & (course == 0) | joint & (r == 2)
        brick = hash_noise(along // 2 + course * 7, z // 2 * 3 + along % 2 * 0, x * 0 + y * 0 + 11)
        t = np.where(stone, np.where(mortar, 0.72, 0.92 + 0.16 * brick), t)
        # Roof tile lines.
        roof = np.isin(m, ROOFS + [SLATE])
        t = np.where(roof & (r == 3), 0.72, t)
        # Plaster wear.
        plaster = m == PLASTER
        t = np.where(plaster, 0.95 + 0.07 * hash_noise(x * 3 + c, y * 5 + r, z), t)
        rock = (m == ROCK) | (m == ROCK2)
        t = np.where(rock, 0.85 + 0.25 * hash_noise(x + c * 31, y + r * 17, z), t)
        wood = (m == WOOD) | (m == DOOR) | (m == TIMBER)
        t = np.where(wood & (c == 1 if face == 1 else c == 3), 0.8, t)
    else:
        cob = m == COBBLE
        t = np.where(cob, np.where((c == 0) & (r == 1), 0.7, 0.88 + 0.22 * hash_noise(x + c // 2, y, r)), t)
        roof = np.isin(m, ROOFS + [SLATE])
        t = np.where(roof & (r == 1) & (c % 2 == 0), 0.85, t)
        grass = (m == GRASS) | (m == LEAVES) | (m == PINE) | (m == MOSS)
        t = np.where(grass, 0.82 + 0.36 * hash_noise(x * 4 + c, y * 4 + r, z), t)
        stone_top = stone & (r == 1) & (c == 0)
        t = np.where(stone_top, 0.8, t)
    leaves = (m == LEAVES) | (m == PINE)
    t = np.where(leaves, 0.75 + 0.5 * hash_noise(x * 2 + c, y * 2 + r, z * 3 + face), t)
    return t


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
    bands = 14
    v = t * bands + BAYER[ys % 4, xs % 4] - 0.5
    tq = np.clip(np.floor(v + 0.5) / bands, 0, 1)
    img = np.stack([np.interp(tq, pos, cols[:, k]) for k in range(3)], axis=-1)
    stars = []
    for _ in range(170):
        x, y = rng.randrange(WIDTH), rng.randrange(int(HEIGHT * 0.85))
        stars.append((x, y, rng.random(), rng.choice([0.5, 0.7, 1.0, 1.0, 1.4])))
    return img.astype(np.float32), stars


def moon(img, cx, cy, r):
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    d = np.hypot(xs - cx, ys - cy)
    halo = np.clip(1 - (d - r) / (r * 2.2), 0, 1) ** 2 * 0.45
    halo = np.floor(halo * 6 + BAYER[ys % 4, xs % 4]) / 6
    img += halo[..., None] * np.array([70, 80, 120], np.float32)
    disc = d <= r
    shade = np.clip(1 - np.hypot(xs - cx + r * 0.35, ys - cy + r * 0.35) / (r * 2.4), 0, 1)
    col = np.array([236, 230, 205], np.float32) * (0.78 + 0.22 * shade[..., None])
    for mx, my, mr in [(-4, -3, 3.5), (5, 2, 2.5), (1, 7, 2)]:
        crater = np.hypot(xs - cx - mx, ys - cy - my) <= mr
        col = np.where(crater[..., None], col * 0.9, col)
    img[disc] = col[disc]
    return disc


def clouds(img, rng, y_min, y_max, count, tones, width=(30, 90)):
    """Puffy pixel-art cumulus: unions of circles with a flat base."""
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    mask = np.zeros((HEIGHT, WIDTH), bool)
    for _ in range(count):
        cx, base = rng.uniform(-20, WIDTH + 20), rng.uniform(y_min, y_max)
        span = rng.uniform(*width)
        for _ in range(int(span / 7) + 2):
            px = cx + rng.uniform(-span / 2, span / 2)
            r = rng.uniform(4, 6 + span / 7) * (1 - abs(px - cx) / span)
            if r < 2:
                continue
            py = base - r * rng.uniform(0.2, 0.6)
            mask |= ((xs - px) ** 2 + ((ys - py) * 1.15) ** 2 <= r * r) & (ys <= base)
    lit = mask & ~np.roll(mask, 2, axis=0)
    rim = mask & ~np.roll(mask, 4, axis=0) & ~lit
    dark = mask & ~np.roll(mask, -3, axis=0)
    img[mask] = tones[0]
    img[rim & (BAYER[ys % 4, xs % 4] < 0.5)] = tones[2] * 0.85 + tones[0] * 0.15
    img[lit] = tones[2]
    img[dark] = tones[1]
    return mask


def text(img, s, x, y, color, scale=1):
    for ch in s.upper():
        glyph = FONT.get(ch, FONT[" "])
        for k, bit in enumerate(glyph):
            if bit == "1":
                gx, gy = x + (k % 3) * scale, y + (k // 3) * scale
                img[gy:gy + scale, gx:gx + scale] = color
        x += 4 * scale
    return x


def render(data, output, username=None):
    import random
    selected = recent_days(data)
    if not selected:
        raise ValueError("Contribution calendar is empty")
    rng = random.Random(824)
    world = build_scene(selected)
    canvas, depth, kind, ident = render_static(world)
    background, stars = sky(rng)
    moon_mask = moon(background, 76, 70, 14)
    clouds(background, rng, 95, 150, 7,
           [np.array(rgb(c), np.float32) for c in ("#1a1d3d", "#141733", "#2c3060")], (20, 60))
    clouds(background, rng, HEIGHT - 40, HEIGHT - 20, 12,
           [np.array(rgb(c), np.float32) for c in ("#2a2955", "#211f45", "#4b4c86")], (50, 110))
    clouds(background, rng, HEIGHT + 5, HEIGHT + 25, 14,
           [np.array(rgb(c), np.float32) for c in ("#35336a", "#2a2856", "#5d5f9e")], (60, 130))
    scene = depth > -1e8
    total = sum(day["contributionCount"] for day in selected)

    window_phase = {}
    wrng = random.Random(3)
    for i in range(1, world.windows + 1):
        window_phase[i] = (wrng.random() * math.tau, wrng.choice([1, 1, 1, 2, 3]), wrng.random())
    win_ids = np.unique(ident[(kind == WINDOW) & (ident > 0)])
    window_px = {i: np.nonzero(ident == i) for i in win_ids}
    water = (kind == WATER) & scene
    fall = (kind == FALL) & scene
    sparkle_seed = np.random.default_rng(11).random((HEIGHT, WIDTH))
    ys_grid, xs_grid = np.mgrid[0:HEIGHT, 0:WIDTH]

    bats = [(rng.uniform(40, 140), rng.uniform(40, 100), rng.uniform(14, 30), rng.random() * math.tau,
             rng.choice([1, -1])) for _ in range(4)]

    frames = []
    n_frames = 96
    for f in range(n_frames):
        phase = math.tau * f / n_frames
        img = background.copy()
        for x, y, p, b in stars:
            tw = 0.55 + 0.45 * math.sin(phase * 2 + p * math.tau)
            if moon_mask[y, x]:
                continue
            img[y, x] = np.maximum(img[y, x], np.array([200, 210, 255]) * min(1, b * tw))
            if b > 1.2 and tw > 0.8:
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    if 0 <= x + dx < WIDTH and 0 <= y + dy < HEIGHT:
                        img[y + dy, x + dx] = np.maximum(img[y + dy, x + dx], [90, 100, 150])
        # Bats circling in front of the moon.
        for bx, by, br, bp, bd in bats:
            a = phase * bd + bp
            x = int(bx + br * math.cos(a))
            y = int(by + br * 0.45 * math.sin(a * 2))
            flap = (f // 2 + int(bp * 10)) % 2
            pix = [(0, 0), (-1, -1 + flap), (1, -1 + flap), (-2, -flap), (2, -flap)]
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
            frame[yy, xx] = col
            glow[yy, xx] = col
        glow[(kind == LANTERN) & scene] = PALETTE[LANTERN]
        bk = (kind == BEACON) & scene
        frame[bk] = [255, 150 + 60 * (0.5 + 0.5 * math.sin(phase * 4)), 60]
        glow[bk] = [255, 160, 60]
        glow[moon_mask] = [90, 90, 80]
        # Water sparkle on the moat and a falling waterfall.
        sp = water & (((sparkle_seed + f / n_frames) % 1.0) < 0.06)
        frame[sp] = [150, 190, 220]
        if fall.any():
            stripe = (ys_grid - f * 3 + (sparkle_seed[0, xs_grid] * 9).astype(int)) % 9
            fcol = np.where((stripe < 2)[..., None], [190, 225, 250], np.where((stripe < 5)[..., None],
                            [90, 150, 205], [50, 100, 160]))
            frame[fall] = fcol[fall]
        # Mist where the waterfall leaves the island.
        _, fy0 = project(ISL_X, 48, 2)
        fx0, _ = project(ISL_X, 48, 2)
        mist_rng = random.Random(77)
        for k in range(26):
            p = (f / n_frames + k / 26) % 1.0
            mx = fx0 + mist_rng.uniform(-6, 6) + p * mist_rng.uniform(-8, 8)
            my = fy0 + p * 26 + mist_rng.uniform(-2, 2)
            a = (1 - p) * 0.6
            for dx in range(2):
                for dy in range(2):
                    X, Y = int(mx) + dx, int(my) + dy
                    if 0 <= X < WIDTH and 0 <= Y < HEIGHT:
                        frame[Y, X] = frame[Y, X] * (1 - a) + np.array([170, 200, 230]) * a
        # Waving flags.
        for fx, fy, fz, length, color in world.flags:
            c = np.array(rgb(color), np.float32)
            for i in range(length):
                for j in range(2):
                    wave = math.sin(phase * 2 - i * 0.9)
                    X, Y = project(fx + 0.5 + i * 0.55, fy + 0.5 - i * 0.55, fz - j)
                    Y += int(round(wave * 1.2))
                    shade = 0.8 + 0.25 * wave
                    for dx in range(2):
                        put(frame, frame_depth, X + dx, Y, fx + fy + fz + 50, c * shade)
            # Pole.
            for zz in range(3):
                X, Y = project(fx + 0.5, fy + 0.5, fz - 2 + zz)
                put(frame, frame_depth, X, Y, fx + fy + fz, np.array([120, 110, 100], np.float32))
        # Chimney smoke.
        for k, (cx, cy, cz) in enumerate(world.smoke):
            X0, Y0 = project(cx, cy, cz)
            for puff in range(5):
                p = (f / n_frames * 2 + puff / 5 + k * 0.17) % 1.0
                X = X0 + int(p * 10 + math.sin(p * 6 + k) * 2)
                Y = Y0 - int(p * 22)
                a = 0.5 * (1 - p)
                size = 1 + int(p * 3)
                for dx in range(size):
                    for dy in range(size):
                        if 0 <= X + dx < WIDTH and 0 <= Y + dy < HEIGHT:
                            frame[Y + dy, X + dx] = frame[Y + dy, X + dx] * (1 - a) + np.array([120, 120, 140]) * a
        # People.
        for wk in world.walkers:
            draw_walker(frame, frame_depth, glow, wk, f, n_frames, phase)
        # Bloom.
        g = Image.fromarray(np.clip(glow, 0, 255).astype(np.uint8))
        bloom = (np.asarray(g.filter(ImageFilter.GaussianBlur(3)), np.float32) * 1.4 +
                 np.asarray(g.filter(ImageFilter.GaussianBlur(9)), np.float32) * 0.9)
        frame = frame + bloom * np.array([1.0, 0.85, 0.6])
        # Caption.
        label = f"{(username or '').upper()}  LAST 20 DAYS"
        text(frame, label, 12, HEIGHT - 26, np.array([210, 215, 235], np.float32))
        x = text(frame, f"{total} CONTRIBUTIONS", 12, HEIGHT - 18, np.array([150, 160, 190], np.float32))
        for lvl in range(5):
            frame[HEIGHT - 17:HEIGHT - 13, x + 4 + lvl * 6:x + 8 + lvl * 6] = rgb(GREENS[lvl])
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
        im = Image.fromarray(data.astype(np.uint8), "P")
        im.putpalette(palette.getpalette()[:765] + [0, 0, 0])
        out.append(im.resize((WIDTH * SCALE, HEIGHT * SCALE), Image.Resampling.NEAREST))
    output.parent.mkdir(parents=True, exist_ok=True)
    out[0].save(output, save_all=True, append_images=out[1:], duration=80, loop=0,
                optimize=False, transparency=255, disposal=1)
    return frames


SKIN = np.array([215, 175, 135], np.float32)
BOOTS = np.array([45, 36, 30], np.float32)
STEEL = np.array([175, 182, 196], np.float32)


def draw_walker(frame, depth, glow, wk, f, n, phase):
    """A tiny walking figure: townsfolk or a spear-carrying soldier."""
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
    coat = np.array(rgb(wk["coat"]), np.float32)
    stride = moving and step % 2 == 1
    # Legs scissor on alternate steps.
    if stride:
        lx = (X - 2, X + 1) if step == 1 else (X - 1, X + 2)
    else:
        lx = (X - 1, X + 1)
    put(frame, depth, lx[0], Y - 1, d, BOOTS)
    put(frame, depth, lx[1], Y - 1, d, BOOTS * 0.8)
    put(frame, depth, X - 1, Y - 2, d, BOOTS)
    put(frame, depth, X + 1, Y - 2, d, BOOTS * 0.8)
    if wk["kind"] == "soldier":
        coat = coat * 1.15
    # Body: lit from the moon on the left, shaded on the right.
    for yy in (3, 4, 5):
        put(frame, depth, X - 1, Y - yy, d, np.clip(coat * 1.15, 0, 255))
        put(frame, depth, X, Y - yy, d, coat)
        put(frame, depth, X + 1, Y - yy, d, coat * 0.7)
    if wk["kind"] == "soldier":
        put(frame, depth, X, Y - 4, d, np.array([200, 190, 120], np.float32))  # belt buckle
    # Swinging arms.
    if moving:
        put(frame, depth, X - 2, Y - 4 + (step < 2), d, coat * 0.9)
        put(frame, depth, X + 2, Y - 4 + (step >= 2), d, coat * 0.6)
    put(frame, depth, X - 1, Y - 6, d, SKIN)
    put(frame, depth, X, Y - 6, d, SKIN * 0.92)
    put(frame, depth, X + 1, Y - 6, d, SKIN * 0.75)
    if wk["kind"] == "soldier":
        for xx, k in ((X - 1, 1.1), (X, 1.0), (X + 1, 0.75)):
            put(frame, depth, xx, Y - 7, d, np.clip(STEEL * k, 0, 255))
        put(frame, depth, X, Y - 8, d, np.array([200, 50, 45], np.float32))  # plume
        # Spear held upright, with a glinting tip.
        for yy in range(2, 11):
            put(frame, depth, X + 3, Y - yy, d, np.array([120, 92, 62], np.float32))
        glint = 1.0 + 0.4 * max(0.0, math.sin(phase * 2 + d * 0.3))
        put(frame, depth, X + 3, Y - 11, d, np.clip(STEEL * glint, 0, 255))
        put(frame, depth, X + 3, Y - 12, d, np.clip(STEEL * glint, 0, 255))
    else:
        hair = np.array(rgb(wk.get("hair", "#2a1d14")), np.float32)
        for xx in (X - 1, X, X + 1):
            put(frame, depth, xx, Y - 7, d, hair)
    if wk["torch"]:
        fl = 0.8 + 0.2 * math.sin(phase * 7 + wk["phase"] * 9 + d)
        tcol = np.array([255, 190, 80], np.float32) * fl
        tx = X - 3 if wk["kind"] == "soldier" else X + 3
        put(frame, depth, tx, Y - 5, d, np.array([90, 60, 40], np.float32))
        put(frame, depth, tx, Y - 6, d, np.array([90, 60, 40], np.float32))
        put(frame, depth, tx, Y - 7, d, tcol)
        put(frame, depth, tx, Y - 8, d, np.array([255, 235, 160], np.float32) * fl)
        if 0 <= tx < WIDTH and 0 <= Y - 8 < HEIGHT:
            glow[Y - 8:Y - 6, tx] = tcol * 1.8


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
