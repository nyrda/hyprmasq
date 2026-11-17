"""Makes the pano task's panorama from its source: prepare.py SOURCE OUT, which needs Pillow and NumPy.

SOURCE is the original of "360-Grad-Panorama von Borkum" by Thomas Kniess on Wikimedia Commons, 11497×2000 pixels:
https://commons.wikimedia.org/wiki/File:360-Grad-Panorama_von_Borkum.jpg. OUT is the task's photo.jpg.

The panorama covers 360°, so its left and right edges show the same direction, but the sky is brighter on the right
of that seam than on the left, which a viewer that wraps around shows as an edge. Each row gets half the difference
between its two edges added on one side and taken away on the other, fading out over BLEND columns: the difference of
the columns next to the seam, as the median over SMOOTH rows, so that what the photo shows there does not count. Then
the rows ROWS are kept, the horizon with the sky above it and the town below, and the photo is scaled to WIDTH.

A column's direction is its place in the width, 0° at the left edge, which neither the crop nor the scale changes:
the cargo ship on the horizon, which the task asks for, is at columns 2300 to 2368 of the source, 73.1°.
"""

import sys

import numpy as np
from PIL import Image

BLEND = 1400
SMOOTH = 151
EDGE = 12
ROWS = (380, 1820)
WIDTH = 4800
QUALITY = 82


def main(source, out):
    Image.MAX_IMAGE_PIXELS = None
    photo = np.asarray(Image.open(source).convert("RGB"), dtype=np.float64)
    height, width, _ = photo.shape
    step = photo[:, :EDGE].mean(axis=1) - photo[:, -EDGE:].mean(axis=1)
    pad = SMOOTH // 2
    padded = np.pad(step, ((pad, pad), (0, 0)), mode="edge")
    step = np.stack([np.median(padded[row:row + SMOOTH], axis=0) for row in range(height)])
    fade = (1 - np.arange(BLEND) / BLEND)[None, :, None]
    photo[:, :BLEND] -= step[:, None, :] / 2 * fade
    photo[:, -BLEND:] += step[:, None, :] / 2 * fade[:, ::-1]
    image = Image.fromarray(np.clip(photo[ROWS[0]:ROWS[1]], 0, 255).round().astype(np.uint8))
    image = image.resize((WIDTH, round((ROWS[1] - ROWS[0]) * WIDTH / width)), Image.LANCZOS)
    image.save(out, quality=QUALITY, optimize=True, progressive=True)
    print(f"{out}: {image.size[0]}x{image.size[1]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
