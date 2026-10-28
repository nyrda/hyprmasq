package server

import (
	"bytes"
	"image"
	"image/draw"
	"image/png"
)

// maxMagnification bounds the factor that enlarges a small zoom: beyond it, the image grows without letting the agent
// aim any finer than a quarter of a captured pixel.
const maxMagnification = 4

// magnification is the whole factor that enlarges a zoom of width × height pixels to minZoomSize on its long side, or
// less when that would pass maxImage, or 1: the smallest that reaches minZoomSize, at most maxMagnification, and the
// largest within maxImage if it is set. A whole factor maps each captured pixel to a block of the same size, so a zoom
// whose long side lies between maxImage/2 and minZoomSize stays as captured.
func magnification(width, height, minZoomSize, maxImage int) int {
	long := max(width, height)
	if minZoomSize <= 0 || long <= 0 || long >= minZoomSize {
		return 1
	}
	k := min(maxMagnification, (minZoomSize+long-1)/long)
	if maxImage > 0 {
		k = min(k, maxImage/long)
	}
	return max(k, 1)
}

// magnify enlarges a PNG by the whole factor k with nearest neighbour, each pixel to a k × k block, so that a point in
// the enlarged image divided by k is the pixel it shows.
func magnify(data []byte, k int) ([]byte, error) {
	src, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	b := src.Bounds()
	in := image.NewNRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(in, in.Rect, src, b.Min, draw.Src)
	out := image.NewNRGBA(image.Rect(0, 0, b.Dx()*k, b.Dy()*k))
	for y := 0; y < b.Dy(); y++ {
		row := out.Pix[y*k*out.Stride : (y*k+1)*out.Stride]
		for x := 0; x < b.Dx(); x++ {
			p := in.Pix[y*in.Stride+4*x : y*in.Stride+4*x+4]
			for i := 0; i < k; i++ {
				copy(row[4*(x*k+i):], p)
			}
		}
		for i := 1; i < k; i++ {
			copy(out.Pix[(y*k+i)*out.Stride:], row)
		}
	}
	return encodePNG(out)
}
