"""tests/fixtures/type3-mpl.pdf: a matplotlib page whose text is Type 3 (pdf.fonttype 3), for JudgeType3Snap.

DejaVuSerif "Divers" 16 times upright and 16 times rotated 90 degrees, each origin a quarter pixel (400 dpi) further
on in x and y, so every fraction of a pixel occurs; and a Nimbus Roman block, so the page family is Times. Run once,
by hand (matplotlib is not a dependency of inkdrill): python3 tools/make_type3_fixture.py tests/fixtures/type3-mpl.pdf
"""
import sys
import matplotlib
matplotlib.use("pdf")
matplotlib.rcParams["pdf.fonttype"] = 3
matplotlib.rcParams["font.family"] = "DejaVu Serif"
import matplotlib.pyplot as plt
fig = plt.figure(figsize=(8.5, 11))
# 400 dpi px = 72/400 pt; a figure inch = 72 pt. Steps of a quarter px: 0.045 pt = 0.000625 in
q = 0.25 * 72 / 400 / 72 / 8.5     # quarter px as a fraction of the figure width
qy = 0.25 * 72 / 400 / 72 / 11
n = 0
for i in range(4):
    for j in range(4):
        fig.text(0.10 + 0.2 * i + i * q, 0.90 - 0.05 * j - j * qy, "Divers", fontsize=9, rotation=0)
        fig.text(0.12 + 0.2 * i + i * q, 0.30 - 0.05 * j - j * qy, "Divers", fontsize=9, rotation=90)
for k in range(8):
    fig.text(0.1, 0.55 - 0.02 * k, "The body of this page is set in a Times face for the judge" , fontsize=9,
             family="Nimbus Roman")
fig.savefig(sys.argv[1])
