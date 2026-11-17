"""export_onnx.py <weights.pt> <out.onnx> <H> <W> -- ONE-TIME export of DocLayout-YOLO to an ONNX graph at a fixed
H x W, outside the run path (coordinator's licence ruling, 2026-11-17): it imports `doclayout_yolo` (AGPL-3.0), which
is never vendored into or linked by inkdrill. The run path reads only the ONNX graph (onnxruntime, MIT). The graph is
the detection head's end-to-end output (1 x 300 x 6: x0 y0 x1 y1 score class), constant-folded by onnxruntime's basic
optimisation. Deterministic for a given torch and onnxruntime: fetch_weights.sh checks its sha256."""
import sys, torch, onnxruntime as ort
from doclayout_yolo import YOLOv10
pt, out, H, W = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
m = YOLOv10(pt).model.eval()
for mod in m.modules():
    if hasattr(mod, "export"): mod.export = True; mod.format = "onnx"
    if hasattr(mod, "max_det"): mod.max_det = 300
torch.onnx.export(m, torch.zeros(1, 3, H, W), out + ".raw", opset_version=17, dynamo=False, input_names=["images"], output_names=["out"])
so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC; so.optimized_model_filepath = out
ort.InferenceSession(out + ".raw", so, providers=["CPUExecutionProvider"])
