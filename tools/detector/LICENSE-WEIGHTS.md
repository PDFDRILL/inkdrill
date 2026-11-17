# The figure detector's weights

- Model: DocLayout-YOLO, DocStructBench weights, `doclayout_yolo_docstructbench_imgsz1024.pt`
  (sha256 9a2ee0220fe3d9ad31b47e1d9f1282f46959a54e4618fce9cffcc9715b8286e2), from Hugging Face
  juliozhao/DocLayout-YOLO-DocStructBench. Its model card: "License: apache-2.0" (read by the coordinator, 2026-11-17).
- The code that trained and exports them (opendatalab/DocLayout-YOLO, built on ultralytics) is AGPL-3.0. It is used only
  by `export_onnx.py`, once, outside the run path; it is not vendored into or linked by inkdrill. The run path reads the
  exported ONNX graph with onnxruntime (MIT), OpenCV's resize (Apache-2.0) and numpy.
