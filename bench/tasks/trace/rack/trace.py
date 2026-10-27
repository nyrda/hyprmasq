#!/usr/bin/env python3
"""Traces the rack of the rack variant's photo for its track.json: trace.py PHOTO [--start X0,X1] [--end Y] > track.json

Run it by hand, with Pillow and NumPy, when the photo changes, and check its overlay (trace.py PHOTO --overlay OUT.png)
by eye before committing what it writes. It follows the rack's two edges row by row, from the bottom edge up, as
bright ridges, which merge into one where the rack is far away. Each edge starts where --start says, in photo pixels
of the bottom row, and looks for the nearest ridge within STEP pixels of where its course so far predicts it in the next
row, so it follows the curve without jumping to a neighbour or into the grass. The rack's centre is the middle of its edges, and its half-width half their distance, both smoothed over
SMOOTH rows. The grade measures strokes against the centre, with the half-width as part of the tolerance.
"""

import argparse
import json

import numpy as np
from PIL import Image, ImageDraw

# The rack's edges in the bottom row of photo.png, the default of --start.
START = (99, 113)
STEP = 2
SMOOTH = 9
# Every how many rows track.json keeps a point; the grade interpolates between them.
EVERY = 2


def ridges(gray, y, lo, hi):
    """The local brightness maxima of row y between lo and hi that stand out from their surroundings."""
    w = gray.shape[1]
    row = gray[max(0, y - 1):y + 2].mean(axis=0)
    k = np.convolve(row, np.ones(3) / 3, mode="same")
    return [x for x in range(max(lo, 2), min(hi, w - 2))
            if k[x] >= k[x - 1] and k[x] > k[x + 1] and k[x] - k[max(0, x - 4):x + 5].min() > 12]


def follow(gray, start):
    """The x of a ridge in every row, from the bottom up, with its drift per row smoothed into the prediction."""
    x, drift, out = float(start), 0.0, {}
    for y in range(gray.shape[0] - 1, -1, -1):
        predicted = x + drift
        lo, hi = int(predicted - STEP - 1), int(predicted + STEP + 2)
        near = [c for c in ridges(gray, y, lo, hi) if abs(c - predicted) <= STEP]
        found = min(near, key=lambda c: abs(c - predicted)) if near else predicted
        drift = 0.8 * drift + 0.2 * (found - x)
        x = found
        out[y] = x
    return out


def smooth(values):
    k = SMOOTH // 2
    padded = np.pad(np.asarray(values, dtype=float), k, mode="edge")
    return np.convolve(padded, np.ones(SMOOTH) / SMOOTH, mode="valid")


def trace(path, start=START, end=0):
    """The rack from the bottom row up to row end, where it leaves the photo or goes out of sight."""
    image = Image.open(path)
    gray = np.asarray(image.convert("L"), dtype=float)
    h = gray.shape[0]
    left, right = follow(gray, start[0]), follow(gray, start[1])
    rows = list(range(h))
    centre = smooth([(left[y] + right[y]) / 2 for y in rows])
    half = smooth([(right[y] - left[y]) / 2 for y in rows])
    keep = [y for y in rows if y >= end and (y % EVERY == 0 or y in (end, h - 1))]
    return {
        "image": path.rsplit("/", 1)[-1],
        "size": [image.width, image.height],
        "line": [[round(float(centre[y]), 1), y, max(0.0, round(float(half[y]), 1))] for y in keep],
        "required": [end, h - 1],
    }


def overlay(path, track, out, scale=3):
    image = Image.open(path).convert("RGB")
    big = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
    draw = ImageDraw.Draw(big)
    for x, y, half in track["line"]:
        for px, colour in ((x, (255, 0, 0)), (x - half, (255, 160, 0)), (x + half, (255, 160, 0))):
            draw.point((px * scale + scale // 2, y * scale + scale // 2), fill=colour)
    big.save(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("photo")
    parser.add_argument("--overlay", help="also draw the trace over the photo, enlarged, into this PNG")
    parser.add_argument("--start", default=f"{START[0]},{START[1]}", help="the rack's edges in the bottom row, X0,X1")
    parser.add_argument("--end", type=int, default=0, help="the row where the rack ends: leaves the photo or goes out of sight")
    args = parser.parse_args()
    track = trace(args.photo, tuple(int(v) for v in args.start.split(",")), args.end)
    if args.overlay:
        overlay(args.photo, track, args.overlay)
    print(json.dumps(track, separators=(",", ":")))
