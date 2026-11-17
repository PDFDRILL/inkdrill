#!/bin/sh
# fetch_weights.sh [dir] -- the figure detector's model for the run path (rule 7: over 20 MB, so fetched, not in git).
# 1. download DocLayout-YOLO DocStructBench weights from Hugging Face and check their sha256;
# 2. export them ONCE to an ONNX graph at 320 x 256 (export_onnx.py; needs the doclayout_yolo package, AGPL-3.0,
#    used only here, never by the run path);
# 3. check the graph's sha256 (exact for torch 2.14.1+cpu, onnxruntime 1.30.0, onnx 1.23.2; another version may write
#    other bytes: then the check reports it and the graph is kept for a measured re-check, not trusted silently).
# Licence of the weights: Apache-2.0 (their card, LICENSE-WEIGHTS.md here). Writes <dir>/dly320x256.onnx (figure
# regions) and <dir>/dly640x512.onnx (layout_hint.py: the typing hint for a document's page 1).
set -e
D=${1:-$(dirname "$0")/../../models}
mkdir -p "$D"
PT="$D/doclayout_yolo_docstructbench_imgsz1024.pt"
PT_SHA=9a2ee0220fe3d9ad31b47e1d9f1282f46959a54e4618fce9cffcc9715b8286e2
ONNX_SHA=c5950c422dba62e7cfb722ddb6ffa7708d6f28066fca0c4209b16ea5a3e10790
ONNX640_SHA=efe1580e60c3f9284c68eabee84f192e5a11a80778078ddff4f69020bc938893
URL=https://huggingface.co/juliozhao/DocLayout-YOLO-DocStructBench/resolve/main/doclayout_yolo_docstructbench_imgsz1024.pt
[ -f "$PT" ] || curl -sSL -o "$PT" "$URL"
echo "$PT_SHA  $PT" | sha256sum -c - || { echo "fetch_weights: weights do not match their sha256" >&2; exit 1; }
python3 "$(dirname "$0")/export_onnx.py" "$PT" "$D/dly320x256.onnx" 320 256
rm -f "$D/dly320x256.onnx.raw"
if echo "$ONNX_SHA  $D/dly320x256.onnx" | sha256sum -c -; then :; else
  echo "fetch_weights: the exported graph differs from the measured one (another torch/onnxruntime?): re-measure before use" >&2
  exit 2
fi
python3 "$(dirname "$0")/export_onnx.py" "$PT" "$D/dly640x512.onnx" 640 512
rm -f "$D/dly640x512.onnx.raw"
if echo "$ONNX640_SHA  $D/dly640x512.onnx" | sha256sum -c -; then :; else
  echo "fetch_weights: the exported 640 x 512 graph differs from the measured one: re-measure before use" >&2
  exit 2
fi
