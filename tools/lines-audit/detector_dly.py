"""scratch rule-6 experiment: DocLayout-YOLO (DocStructBench weights) as the figure source -- CPU s/page on ONE thread
and figure IoU against MathPix, at several input sizes, on the pages with MathPix figures of the 3 development docs"""
import json, os, subprocess, sys, time, io
import torch
torch.set_num_threads(1)
from doclayout_yolo import YOLOv10
from PIL import Image
W8 = os.path.expanduser("~/.pix2text/1.1/layout-docyolo/doclayout_yolo_docstructbench_imgsz1024.pt")
model = YOLOv10(W8)
LIB = os.path.expanduser("~/pdfdrill-library")
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - ix*iy
    return ix*iy/u if u > 0 else 0
pages = []
for doc in sys.argv[2:]:
    mp = json.load(open(f"{LIB}/{doc}/{doc}.lines.json"))
    for pg in mp["pages"]:
        figs = [(l["region"]["top_left_x"], l["region"]["top_left_y"], l["region"]["top_left_x"] + l["region"]["width"], l["region"]["top_left_y"] + l["region"]["height"]) for l in pg["lines"] if l["type"] in ("diagram", "chart") and isinstance(l.get("region"), dict)]
        if figs:
            W, H = pg["page_width"], pg["page_height"]
            png = subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", "-f", str(pg["page"]), "-l", str(pg["page"]), f"{LIB}/{doc}/{doc}.pdf"], capture_output=True).stdout
            img = Image.open(io.BytesIO(png)).convert("RGB")
            pages.append((doc, pg["page"], img, figs, W / img.width, H / img.height))
for size in [int(s) for s in sys.argv[1].split(",")]:
    model.predict(pages[0][2], imgsz=size, conf=0.2, verbose=False)        # warm up
    cpu = 0.0; hit = n = 0
    for doc, p, img, figs, sx, sy in pages:
        t = time.process_time()
        r = model.predict(img, imgsz=size, conf=0.2, verbose=False)[0]
        cpu += time.process_time() - t
        names = r.names
        boxes = [tuple(float(v) for v in b.xyxy[0]) for b in r.boxes if names[int(b.cls)] == "figure"]
        boxes = [(b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy) for b in boxes]
        for f in figs:
            n += 1; hit += max((iou(f, b) for b in boxes), default=0) >= 0.5
    print(f"imgsz {size}: figures {hit}/{n} = {hit/max(1,n):.0%}; CPU {cpu/len(pages):.2f} s/page (1 thread, {len(pages)} pages)")
