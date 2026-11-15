#!/bin/sh
# gd3.pin, both sides (T-0148 M0): the real pin passes; a copy with one hash changed stops make with
# exit != 0 and names the file. The copy lives in build/ (removed by make clean); GLYPHDRILL3 is not touched.
set -u
cd "$(dirname "$0")/.."
fail=0
if make -s pin >/dev/null 2>build/pin-ok.err; then
  echo "test_pin accepting side: ok"
else
  echo "test_pin accepting side: FAIL (the committed gd3.pin does not match)"; cat build/pin-ok.err; fail=1
fi
# change the first hex digit of the hash of src/layout/blocks.c
awk '!/^#/ && $2 == "src/layout/blocks.c" { c = substr($1, 1, 1); $1 = (c == "0" ? "1" : "0") substr($1, 2); print $1 "  " $2; next } { print }' \
  gd3.pin > build/pin-red.pin
if make -s pin PIN=build/pin-red.pin >build/pin-red.out 2>&1; then
  echo "test_pin red side: FAIL (make accepted a changed pin)"; fail=1
elif grep -q 'src/layout/blocks.c: FAILED' build/pin-red.out && grep -q 'differ from build/pin-red.pin' build/pin-red.out; then
  echo "test_pin red side: ok (make stopped and named src/layout/blocks.c)"
else
  echo "test_pin red side: FAIL (stopped, but did not name the file)"; cat build/pin-red.out; fail=1
fi
rm -f build/pin-ok.err build/pin-red.pin build/pin-red.out
exit $fail
