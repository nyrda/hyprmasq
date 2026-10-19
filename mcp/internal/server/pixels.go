package server

import (
	"bufio"
	"bytes"
	"compress/flate"
	"compress/zlib"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"image"
	"image/draw"
	"io"
	"os"
	"sync"
)

// encodePNG encodes an image for the agent as the plugin does: zlib at its fastest, with the Sub filter on every row.
// image/png tries every filter on every row, which takes four times as long.
func encodePNG(src image.Image) ([]byte, error) {
	img, ok := src.(*image.NRGBA)
	if !ok {
		img = image.NewNRGBA(src.Bounds())
		draw.Draw(img, img.Rect, src, img.Rect.Min, draw.Src)
	}
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	var out bytes.Buffer
	out.WriteString("\x89PNG\r\n\x1a\n")
	header := make([]byte, 13)
	binary.BigEndian.PutUint32(header[0:], uint32(w))
	binary.BigEndian.PutUint32(header[4:], uint32(h))
	// 8 bits per channel, RGBA, deflate, adaptive filtering, no interlace.
	header[8], header[9] = 8, 6
	writeChunk(&out, "IHDR", header)
	var data bytes.Buffer
	zw, err := zlib.NewWriterLevel(&data, zlib.BestSpeed)
	if err != nil {
		return nil, err
	}
	row := make([]byte, 1+4*w)
	row[0] = 1
	for y := range h {
		pix := img.Pix[img.PixOffset(b.Min.X, b.Min.Y+y):][:4*w]
		copy(row[1:5], pix[:4])
		for i := 4; i < len(pix); i++ {
			row[1+i] = pix[i] - pix[i-4]
		}
		if _, err := zw.Write(row); err != nil {
			return nil, err
		}
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	writeChunk(&out, "IDAT", data.Bytes())
	writeChunk(&out, "IEND", nil)
	return out.Bytes(), nil
}

func writeChunk(out *bytes.Buffer, kind string, data []byte) {
	var n [4]byte
	binary.BigEndian.PutUint32(n[:], uint32(len(data)))
	out.Write(n[:])
	crc := crc32.NewIEEE()
	crc.Write([]byte(kind))
	crc.Write(data)
	out.WriteString(kind)
	out.Write(data)
	binary.BigEndian.PutUint32(n[:], crc.Sum32())
	out.Write(n[:])
}

// errDetailGone means that the pixels of a detail image are gone, with its shot, or because they could not be written
// when they left memory.
var errDetailGone = errors.New("the detail image is gone")

// residentDetails is how many detail images keep their pixels in memory: the latest one of each window, of the windows
// whose latest one is newest. A capture compares the window with the shot that the agent last got of it, almost always,
// and so does the check of targets. The others are written to a file when they leave memory, and read from it when a
// call uses an older shot. At 2560×1440, an image takes 15 MB.
const residentDetails = 4

// keepPixels holds the pixels of a detail image in memory, which the store lets go once newer ones take their place.
func (d *detail) keepPixels(img *image.NRGBA, spillPath string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.img, d.spillPath = img, spillPath
}

// image returns the pixels of the detail image, from memory or from the file that holds them.
func (d *detail) image() (image.Image, error) {
	d.mu.Lock()
	img, spilled, gone := d.img, d.spilled, d.gone
	d.mu.Unlock()
	switch {
	case gone:
		return nil, errDetailGone
	case img != nil:
		return img, nil
	case spilled != "":
		return readSpill(spilled, d.Width, d.Height)
	case d.Path != "":
		return readPNG(d.Path)
	}
	return nil, errDetailGone
}

// inMemory tells whether the store has to let go of the pixels some time.
func (d *detail) inMemory() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.img != nil && !d.gone && !d.leaving
}

// pendingSpills is how many detail images may be on their way to their spill files at once. A release waits for one of
// them to be written before it starts another, so that captures that come faster than the writes do not hold more
// pixels in memory than that.
const pendingSpills = 2

// A spiller writes detail images to their spill files in the background.
type spiller struct {
	wg    sync.WaitGroup
	slots chan struct{}
	// Called after a file is written, without a lock held, or nil.
	written func()
}

func newSpiller() *spiller {
	return &spiller{slots: make(chan struct{}, pendingSpills)}
}

// wait waits for the writes that have started.
func (sp *spiller) wait() {
	sp.wg.Wait()
}

// release lets the pixels leave memory. A detail that is the shot's own image has them in its PNG already; the others
// are written, compressed, to their spill file, in the background so that no call waits for it unless pendingSpills
// writes are still running. Until then, they stay in memory. Pixels that cannot be written are gone, so that the shot is
// not compared, as when its file is gone.
func (d *detail) release(sp *spiller) {
	d.mu.Lock()
	if d.img == nil || d.gone || d.leaving {
		d.mu.Unlock()
		return
	}
	d.leaving = true
	if d.Path != "" || d.spillPath == "" {
		if d.Path == "" {
			d.gone = true
		}
		d.img = nil
		d.mu.Unlock()
		return
	}
	img, path := d.img, d.spillPath
	d.mu.Unlock()
	sp.slots <- struct{}{}
	sp.wg.Go(func() {
		defer func() { <-sp.slots }()
		n, err := writeSpill(path, img)
		d.mu.Lock()
		switch {
		case d.gone || err != nil:
			os.Remove(path)
			d.gone = true
		default:
			d.spilled, d.spilledBytes = path, n
		}
		d.img = nil
		written := !d.gone
		d.mu.Unlock()
		if written && sp.written != nil {
			sp.written()
		}
	})
}

// spillBytes is the size of the spill file, once it is written.
func (d *detail) spillBytes() int64 {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.spilled == "" {
		return 0
	}
	return d.spilledBytes
}

// drop forgets the pixels of a detail whose shot is gone, and deletes their file.
func (d *detail) drop() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.gone, d.img = true, nil
	if d.spilled != "" {
		os.Remove(d.spilled)
	}
}

// A spill file holds the pixels as they are in memory, compressed with flate at its fastest: three to four times as fast
// to write as a PNG, and about as small for a window's content. It returns the size of the file.
func writeSpill(path string, img *image.NRGBA) (int64, error) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return 0, err
	}
	bw := bufio.NewWriter(f)
	zw, _ := flate.NewWriter(bw, flate.BestSpeed)
	_, err = zw.Write(img.Pix)
	err = errors.Join(err, zw.Close(), bw.Flush())
	var n int64
	if err == nil {
		n, err = f.Seek(0, io.SeekCurrent)
	}
	if err = errors.Join(err, f.Close()); err != nil {
		os.Remove(path)
		return 0, err
	}
	return n, nil
}

func readSpill(path string, width, height int) (*image.NRGBA, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	img := image.NewNRGBA(image.Rect(0, 0, width, height))
	zr := flate.NewReader(bufio.NewReader(f))
	defer zr.Close()
	if _, err := io.ReadFull(zr, img.Pix); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return img, nil
}
