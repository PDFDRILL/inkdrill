"""scratch: DocLayout-YOLO ONNX at 320 with onnxruntime, ONE thread: figure IoU vs MathPix and CPU s/page"""
import io, json, os, subprocess, sys, time
import numpy as np, onnxruntime as ort
from PIL import Image
so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
sess = ort.InferenceSession(sys.argv[1], so, providers=["CPUExecutionProvider"])
S = int(sys.argv[2]); LIB = os.path.expanduser("~/pdfdrill-library")
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - ix*iy
    return ix*iy/u if u > 0 else 0
def detect(img):
    w, h = img.size; r = min(S / w, S / h); nw, nh = round(w * r), round(h * r)
    canvas = Image.new("RGB", (S, S), (114, 114, 114)); dx, dy = (S - nw) // 2, (S - nh) // 2
    canvas.paste(img.resize((nw, nh), Image.BILINEAR), (dx, dy))
    x = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    out = sess.run(None, {"images": x})[0][0]
    return [((b[0] - dx) / r, (b[1] - dy) / r, (b[2] - dx) / r, (b[3] - dy) / r) for b in out if b[4] >= 0.2 and int(b[5]) == 3]
hit = n = 0; cpu = 0.0; pages = 0
for doc in sys.argv[3:]:
    mp = json.load(open(f"{LIB}/{doc}/{doc}.lines.json"))
    for pg in mp["pages"]:
        figs = [(l["region"]["top_left_x"], l["region"]["top_left_y"], l["region"]["top_left_x"] + l["region"]["width"], l["region"]["top_left_y"] + l["region"]["height"]) for l in pg["lines"] if l["type"] in ("diagram", "chart") and isinstance(l.get("region"), dict)]
        if not figs: continue
        png = subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", "-f", str(pg["page"]), "-l", str(pg["page"]), f"{LIB}/{doc}/{doc}.pdf"], capture_output=True).stdout
        img = Image.open(io.BytesIO(png)).convert("RGB")
        sx, sy = pg["page_width"] / img.width, pg["page_height"] / img.height
        detect(img) if pages == 0 else None
        t = time.process_time(); boxes = detect(img); cpu += time.process_time() - t; pages += 1
        boxes = [(b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy) for b in boxes]
        for f in figs:
            n += 1; hit += max((iou(f, b) for b in boxes), default=0) >= 0.5
print(f"onnx imgsz {S}: figures {hit}/{n} = {hit/max(1,n):.0%}; CPU {cpu/pages:.3f} s/page incl. resize (1 thread, {pages} pages)")
