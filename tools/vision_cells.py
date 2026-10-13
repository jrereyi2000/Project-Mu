#!/usr/bin/env python3
"""
Second OCR experiment: read every table cell on its own with Apple Vision.

  python3 tools/vision_cells.py captures/2026-10-13_1413

Needs <capture>/cell_plan.json (one crop box per cell, computed from vision_ocr.json).
Each cell is cropped, enlarged 3x and read twice: as-is ("plain") and as dark text on a
white background ("inverted"). Writes <capture>/vision_cells.json:
{"<subdir>/<file>.png": {"plain": [[row, col, text, conf], ...], "inverted": [...]}}.
Read-only otherwise: no key presses, no network. Uses one process per CPU core.
"""
import io, json, os, sys, time
from multiprocessing import get_context

SCALE, PAD = 3, 24


def _vision(img):
    import Quartz, Vision
    from Foundation import NSData
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    handler.performRequests_error_([req], None)
    best_text, best_conf = [], 1.0
    for obs in sorted(req.results() or [], key=lambda o: o.boundingBox().origin.x):
        cand = obs.topCandidates_(1)
        if cand:
            best_text.append(str(cand[0].string()))
            best_conf = min(best_conf, float(cand[0].confidence()))
    return " ".join(best_text), (round(best_conf, 3) if best_text else 0.0)


def do_page(args):
    from PIL import Image, ImageOps
    path, cells = args
    im = Image.open(path).convert("L")
    out = {"plain": [], "inverted": []}
    for r, c, (x0, y0, x1, y1) in cells:
        crop = im.crop((x0, y0, x1, y1))
        crop = crop.resize((crop.width * SCALE, crop.height * SCALE), Image.LANCZOS)
        plain = ImageOps.expand(crop, PAD, fill=0)
        inv = ImageOps.autocontrast(ImageOps.expand(ImageOps.invert(crop), PAD, fill=255), cutoff=1)
        for key, img in (("plain", plain), ("inverted", inv)):
            t, conf = _vision(img)
            out[key].append([r, c, t, conf])
    return path, out


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = sys.argv[1].rstrip("/")
    plan = json.load(open(os.path.join(root, "cell_plan.json")))
    jobs = [(os.path.join(root, p), v["cells"]) for p, v in sorted(plan.items()) if v]
    n_cells = sum(len(c) for _, c in jobs)
    print(f"{len(jobs)} screenshots, {n_cells} cells, read twice each")
    start, result = time.time(), {}
    with get_context("spawn").Pool(os.cpu_count() or 4) as pool:
        for i, (path, out) in enumerate(pool.imap_unordered(do_page, jobs), 1):
            result[os.path.relpath(path, root)] = out
            if i % 20 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} screenshots ({time.time() - start:.0f}s)")
    dest = os.path.join(root, "vision_cells.json")
    with open(dest, "w") as fh:
        json.dump(result, fh)
    print(f"Wrote {dest}")


if __name__ == "__main__":
    main()
