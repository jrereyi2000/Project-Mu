#!/usr/bin/env python3
"""
Run Apple Vision OCR (the on-device OCR madden_capture.py already uses) over every
screenshot in a capture folder and save the raw results, for comparing against
extract.py's Gemini CSVs.

  python3 tools/vision_dump.py captures/2026-10-13_1413
  python3 tools/vision_dump.py data/frames/test_output_frames     # any folder of .png/.jpg frames

Writes <folder>/vision_ocr.json: {"<subdir>/<file>.png": {"size": [w, h],
"items": [{"text", "box": [x0, y0, x1, y1], "conf"}, ...]}, ...} in pixel
coordinates, top-left origin. Read-only otherwise: no key presses, no network.
"""
import glob, json, os, sys, time
from multiprocessing import get_context

import Quartz
import Vision
from Foundation import NSURL
from PIL import Image


def ocr(path):
    W, H = Image.open(path).size
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(path), None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    handler.performRequests_error_([req], None)
    items = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        bb = obs.boundingBox()
        x0 = bb.origin.x * W
        y0 = (1 - bb.origin.y - bb.size.height) * H
        items.append({"text": str(cand[0].string()),
                      "box": [round(x0, 1), round(y0, 1),
                              round(x0 + bb.size.width * W, 1), round(y0 + bb.size.height * H, 1)],
                      "conf": round(float(cand[0].confidence()), 3)})
    return {"size": [W, H], "items": items}


def _one(path):
    return path, ocr(path)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = sys.argv[1].rstrip("/")
    paths = sorted(p for sub in ("stats", "team_stats", "schedule")
                   for p in glob.glob(os.path.join(root, sub, "*.png")))
    if not paths:   # a plain folder of frames
        paths = sorted(glob.glob(os.path.join(root, "*.png")) + glob.glob(os.path.join(root, "*.jpg")))
    if not paths:
        sys.exit(f"No screenshots in {root}")
    out, start = {}, time.time()
    with get_context("spawn").Pool(os.cpu_count() or 4) as pool:
        for i, (p, res) in enumerate(pool.imap_unordered(_one, paths, chunksize=4), 1):
            out[os.path.relpath(p, root)] = res
            if i % 50 == 0 or i == len(paths):
                print(f"{i}/{len(paths)} read ({time.time() - start:.0f}s)")
    dest = os.path.join(root, "vision_ocr.json")
    with open(dest, "w") as fh:
        json.dump(out, fh)
    print(f"Wrote {dest}")


if __name__ == "__main__":
    main()
