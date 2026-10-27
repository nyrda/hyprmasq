#!/usr/bin/env python3
"""Traces the waterline of the river variant's left bank for its track.json: trace.py PHOTO > track.json

Run it by hand, with Pillow and NumPy, when the photo changes, and check the waterline on enlargements of the photo by
eye before committing what it writes. Each row from the top of SEEDS to its bottom is scanned leftwards from the open
water, 22 pixels right of a rough waterline that SEEDS gives, read off gridded enlargements, to the first two pixels
that differ from the water there, the mean of the next 14 pixels, by more than THRESHOLD in a colour channel: the
waterline, where the stones begin. Piles of stones and a jetty stick out of the bank and pull the scan inland in a
few rows, while the bank is a smooth curve at this scale, so the rows are smoothed with a median over SMOOTH rows,
which those few rows cannot move, and a mean over as many.

The bank is a strip of stones about INLAND pixels wide in a row, between the water and the path, and a stroke along
the embankment anywhere on it is on the line, so the band reaches from INLAND pixels inland to WATER pixels into the
water. Below row BAND_FROM a band of dark rocks of the water's colour lies in front of the stones, which some read
as the waterline, so the band reaches ROCKS pixels into the water there. The grade
requires the rows REQUIRED, where the shadow of the hill lies on the bank; the line above them, in the sun, may be
drawn but need not be.
"""

import json
import sys

import numpy as np
from PIL import Image

SEEDS = [(298, 63), (277, 77), (247, 90), (193, 103), (160, 123), (137, 150), (128, 173), (130, 197), (137, 223),
         (143, 253), (157, 283), (173, 307), (200, 330), (208, 340), (218, 350), (225, 360), (232, 370), (255, 400),
         (275, 420), (297, 440), (318, 460), (343, 480), (368, 500), (398, 520), (428, 540), (462, 560), (497, 580),
         (532, 599)]
THRESHOLD = 22
FIRST_ROW = 120
BAND_FROM = 480
REQUIRED = [230, 480]
SMOOTH = 31
INLAND = 10
WATER = 2
ROCKS = 30


def scan(a):
    out = []
    for y in range(SEEDS[0][1], SEEDS[-1][1] + 1):
        start = int(np.interp(y, [s[1] for s in SEEDS], [s[0] for s in SEEDS])) + 22
        row = a[y]
        water = row[start:start + 14].mean(axis=0)
        x, run = start, 0
        while x > start - 60:
            run = run + 1 if np.abs(row[x] - water).max() > THRESHOLD else 0
            if run == 2:
                break
            x -= 1
        out.append((x + 1.5, y))
    return out


def smooth(points):
    xs = np.array([p[0] for p in points], float)
    k = SMOOTH // 2
    med = np.array([np.median(xs[max(0, i - k):i + k + 1]) for i in range(len(xs))])
    return np.convolve(np.pad(med, k, mode="edge"), np.ones(SMOOTH) / SMOOTH, mode="valid")


def main(path):
    image = Image.open(path)
    points = scan(np.asarray(image.convert("RGB"), dtype=float))
    xs = smooth(points)
    line = []
    for x, (_, y) in zip(xs, points):
        if y < FIRST_ROW or (y % 2 and y != points[-1][1]):
            continue
        rocks = min(1.0, max(0.0, (y - BAND_FROM) / 10))
        # The band from the stones inland to the water's edge, and below BAND_FROM over the rocks too.
        lo, hi = x - INLAND, x + WATER + ROCKS * rocks
        line.append([round(float(lo + hi) / 2, 1), y, round(float(hi - lo) / 2, 1)])
    print(json.dumps({"image": path.rsplit("/", 1)[-1], "size": [image.width, image.height], "line": line,
                      "required": REQUIRED}, separators=(",", ":")))


if __name__ == "__main__":
    main(sys.argv[1])
