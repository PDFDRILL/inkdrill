#!/usr/bin/env python3
"""layout_hint.py <model.onnx> <doc.pdf> [page] -- the layout hint for `inkcmp lines --hint` (CONTRACT unit 7: author
blocks typed as maths). Runs the DocLayout-YOLO graph exported at 640 x 512 (fetch_weights.sh) on ONE thread, on the
page rendered at 150 dpi (pdftoppm, CropBox) and shrunk as ultralytics does (cv2 INTER_LINEAR, no antialiasing),
letterboxed grey 114. Prints one line per region, `page text|formula x0 y0 x1 y1`, coordinates as fractions of the
page: title / plain text / abandon are `text`, isolate_formula is `formula`, confidence >= 0.2. Meant for a
document's page 1 only (default 1): at 640 x 512 it costs about 1 CPU s, once per document. The run path imports
onnxruntime (MIT), OpenCV and numpy; no AGPL code."""
import io, subprocess, sys
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image
H, W = 640, 512
NAMES = {0: "text", 1: "text", 2: "text", 8: "formula"}     # title, plain text, abandon; isolate_formula


def session(model):
    so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
    return ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])


def page_image(pdf, page):
    """the page at 150 dpi (pdftoppm, CropBox) as an RGB array"""
    png = subprocess.run(["pdftoppm", "-r", "150", "-cropbox", "-png", "-singlefile", "-f", str(page), "-l", str(page), pdf],
                         capture_output=True, check=True).stdout
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


def run_graph(sess, img, h_in, w_in, conf=0.2):
    """one detector pass at h_in x w_in: [(class id, x0, y0, x1, y1)] as fractions of the page; the shrink is cv2's
    INTER_LINEAR as ultralytics does (no antialiasing), letterboxed grey 114, centred"""
    h, w = img.shape[:2]
    r = min(w_in / w, h_in / h)
    nw, nh = round(w * r), round(h * r)
    canvas = np.full((h_in, w_in, 3), 114, np.uint8)
    dx, dy = (w_in - nw) // 2, (h_in - nh) // 2
    canvas[dy:dy + nh, dx:dx + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    out = sess.run(None, {"images": canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0})[0][0]
    return [(int(b[5]), (b[0] - dx) / r / w, (b[1] - dy) / r / h, (b[2] - dx) / r / w, (b[3] - dy) / r / h)
            for b in out if b[4] >= conf]


def hints(model, pdf, page=1):
    return [(NAMES[c],) + tuple(b) for c, *b in run_graph(session(model), page_image(pdf, page), H, W) if c in NAMES]


if __name__ == "__main__":
    pg = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    for c, x0, y0, x1, y1 in hints(sys.argv[1], sys.argv[2], pg):
        print(f"{pg} {c} {x0:.5f} {y0:.5f} {x1:.5f} {y1:.5f}")
