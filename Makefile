# inkdrill: one Makefile, one build folder (DESIGN.md D-6; T-0148 M0).
#   make         checks gd3.pin, then builds every C program into build/
#   make test    the C tests (tests-c/), then the Python suite (tests/)
#   make clean   removes build/
#   make repin   writes gd3.pin from GLYPHDRILL3's checked-out commit (a deliberate commit takes it in)
#
# GLYPHDRILL3's sources are compiled from its own checkout, GD3 (default ../GLYPHDRILL3), into build/;
# they are never copied into inkdrill and GLYPHDRILL3 is never written. gd3.pin holds the sha256 of every
# GLYPHDRILL3 file the build reads, and every make checks it first and stops naming the files that differ.
CC ?= cc
# the minimum every C program here is compiled and linked with (owner, 2026-11-24, drillgroup PROTOCOL). It stands
# after CFLAGS on every line, so a CFLAGS from the environment (-O2 -g, say) cannot lower it: the last -O wins.
# Each program compiles and links in one cc call, so the linker gets it too. Only test-c-asan sets OPT empty, to
# keep its own -O1 -g.
OPT = -O3 -march=native
CFLAGS ?= -std=c17 -Wall -Wextra -Wpedantic -Werror
B = build
GD3 ?= ../GLYPHDRILL3
# a missing checkout fails here, by name, rather than as "No rule to make target '../GLYPHDRILL3/inkcmp/page.c'"
ifeq ($(filter clean,$(MAKECMDGOALS)),)
ifeq ($(wildcard $(GD3)/inkcmp/page.c),)
$(error no GLYPHDRILL3 checkout at GD3=$(GD3): set GD3=<GLYPHDRILL3 checkout>, e.g. make GD3=$$HOME/GLYPHDRILL3)
endif
endif
PIN ?= gd3.pin
GD3_FILES = src/gd3_adapter.c src/gd3_white.c src/gd3_blocks.c src/gd3_rle.h src/layout/blocks.c src/layout/blocks.h \
            inkcmp/lines.c inkcmp/lines.h inkcmp/white.c inkcmp/white.h inkcmp/page.c inkcmp/page.h
PROGRAMS = $(B)/inkcmp $(B)/inkcmp-selftest-two $(B)/gd3-adapter $(B)/gd3-blocks $(B)/gd3-white
all: $(PROGRAMS)
$(B):
	mkdir -p $(B)
# order-only: runs before every build, does not force a rebuild by itself
$(PROGRAMS): | pin $(B)
pin:
	@cd $(GD3) && grep -v '^#' $(abspath $(PIN)) | LC_ALL=C sha256sum -c --quiet - || \
	  { echo "make: GLYPHDRILL3 sources in $(GD3) differ from $(PIN) (files above). Take a new version in with 'make repin' and a commit naming the GLYPHDRILL3 commit." >&2; exit 1; }
repin:
	@git -C $(GD3) diff --quiet HEAD -- $(GD3_FILES) || \
	  { echo "make repin: $(GD3) has uncommitted changes in the pinned files; pin a commit, not a working tree" >&2; exit 1; }
	@{ echo "# GLYPHDRILL3 files the inkdrill build compiles from GD3 (default ../GLYPHDRILL3); written by make repin."; \
	   echo "# commit $$(git -C $(GD3) log -1 --format='%H %ad "%s"' --date=short)"; \
	   (cd $(GD3) && sha256sum $(GD3_FILES)); } > $(PIN).new && mv $(PIN).new $(PIN)
	@cat $(PIN)

# inkcmp: the C judge (c/inkcmp/CONTRACT.md). Units 1, 3 and 7 (page, white, lines) live in GLYPHDRILL3's inkcmp/
# since its 7941726 (coordinator decision (d), 2026-11-17) and are pinned; glyphs, rules and the CLI live here
GD3INK = $(GD3)/inkcmp
INKCMP = c/inkcmp/inkcmp.c $(GD3INK)/page.c $(GD3INK)/white.c $(GD3INK)/lines.c c/inkcmp/glyphs.c c/inkcmp/glyphtab.c \
         c/inkcmp/rules.c $(GD3)/src/layout/blocks.c
INKCMP_H = $(GD3INK)/page.h $(GD3INK)/white.h $(GD3INK)/lines.h c/inkcmp/glyphs.h c/inkcmp/glyphtab.h $(GD3)/src/layout/blocks.h
$(B)/inkcmp: $(INKCMP) $(INKCMP_H)
	$(CC) $(CFLAGS) $(OPT) -I$(GD3)/src -I$(GD3INK) -Ic/inkcmp -o $@ $(INKCMP) $(LDFLAGS) -lm
$(B)/inkcmp-selftest-two: c/inkcmp/selftest_two.c $(GD3INK)/page.c $(GD3INK)/page.h
	$(CC) $(CFLAGS) $(OPT) -I$(GD3INK) -o $@ c/inkcmp/selftest_two.c $(GD3INK)/page.c $(LDFLAGS)
# the three GLYPHDRILL3 programs inkcmp's byte-identity tests compare with, built from the pinned sources
$(B)/gd3-adapter: $(GD3)/src/gd3_adapter.c
	$(CC) $(CFLAGS) $(OPT) -o $@ $< $(LDFLAGS)
$(B)/gd3-white: $(GD3)/src/gd3_white.c $(GD3)/src/gd3_rle.h
	$(CC) $(CFLAGS) $(OPT) -o $@ $< $(LDFLAGS)
$(B)/gd3-blocks: $(GD3)/src/gd3_blocks.c $(GD3)/src/gd3_rle.h $(GD3)/src/layout/blocks.c $(GD3)/src/layout/blocks.h
	$(CC) $(CFLAGS) $(OPT) -I$(GD3)/src -o $@ $(GD3)/src/gd3_blocks.c $(GD3)/src/layout/blocks.c $(LDFLAGS)

test: test-c test-py
test-c: all
	python3 tests-c/test_inkcmp_load.py
	python3 tests-c/test_inkcmp_blocks.py
	python3 tests-c/test_inkcmp_white.py
	python3 tests-c/test_inkcmp_lines.py
	python3 tests-c/test_inkcmp_glyphs.py
	python3 tests-c/test_inkcmp_stamp.py
	python3 tests-c/test_inkcmp_hint.py
	sh tests-c/test_pin.sh
test-py:
	python3 -m unittest discover -s tests -t .
# the C tests with AddressSanitizer/UBSan builds, then a normal rebuild
ASAN = -O1 -g -std=c17 -Wall -Wextra -Wpedantic -Werror -fsanitize=address,undefined -fno-omit-frame-pointer
test-c-asan:
	$(MAKE) -B CFLAGS="$(ASAN)" OPT= all
	$(MAKE) test-c; status=$$?; $(MAKE) -B all; exit $$status
clean:
	rm -rf $(B)
.PHONY: all pin repin test test-c test-py test-c-asan clean
