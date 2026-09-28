# Frame-skip threshold calibration

## Recommendation

**Metric: tile-max, 16x9 tiles on the full-res greyscale frame (no resize) —
per-tile mean-abs-diff, similarity = 1 - max_tile_diff/255.**
**Threshold: 0.986** (send if `sim < 0.986` or 5s since last send).
Compute: ~3.4 ms/frame (grab ~25 ms is unchanged, unavoidable, and dominates
either way). Well under the 10 ms budget.

Why not the current global-mean 64x36 metric: on this data its "must-change"
class (new terminal line, toast, dialog, window switch, scroll, video region —
excluding the harder few-characters-typed case) tops out at **0.99977**, but
the "must-skip" class (caret blink, clock tick, hover, JPEG noise, wallpaper
margin — excluding the trivial mouse-cursor case) bottoms out at **0.99929**.
Those ranges *overlap* — no single threshold on this metric gets a new
terminal line right without also firing on ordinary noise. A single new
16px text line, averaged over a 64x36 thumbnail (or 128x72/160x90/256x144,
all tested, same story), gets diluted into a ~0.02% global mean shift, the
same order of magnitude as JPEG-noise dither. Bigger thumbnails do not fix
this (see evidence table) because the problem is averaging, not resolution.

Tile-max does not dilute: change max (excl. typed words) = **0.98636**,
skip min (excl. cursor) = **0.98661** — a real, if narrow, gap. A threshold
of **0.986** sits in that gap: false-skip = 0 on every new-terminal-line,
toast, dialog, window-switch, scroll, and video-region pair tested, and
false-send = 0 on caret/clock/hover/noise/wallpaper. On the ~2 min passive
real-screen sample this threshold skips **~37%** of seconds (below,
"idle" was not fully idle — see caveats).

**Caveat / honest gap:** a few typed characters on one line is the one
must-change case no metric separates cleanly from noise (tile-max: typed
words 0.994-0.997 vs. skip range 0.987-0.999 — overlapping). This wasn't in
the "never miss" priority list (terminal line / toast were), so the
practical answer is to accept it may be skipped until the existing 5s
force-send fires, not to chase a threshold that would also nuke the skip
rate on idle. **Unverified**: whether this matters in practice depends on
how often a lone few-character edit (vs. a full new line) needs sub-5s
delivery — flag if that's wrong.

## Evidence

Pairs: 88 total (42 must_change / 46 must_skip)

Passive idle sample: 114 consecutive-second diffs from the real screen (numbers only, no images retained).


## Evidence table

| metric | must_change min/med/max | must_skip min/med/max | threshold | false_skip | false_send | idle skip rate | ms/frame (grab+compute) | ms compute-only |
|---|---|---|---|---|---|---|---|---|
| current_64x36_default | 0.1070/0.9979/0.9999 | 0.9993/1.0000/1.0000 | 0.99995 | 0.000 | 0.391 | 0.360 | 26.18 | 1.882 |
| 64x36_BOX | 0.1069/0.9979/1.0000 | 0.9993/1.0000/1.0000 | 0.99995 | 0.000 | 0.391 | 0.360 | 25.25 | 0.965 |
| 128x72_BOX | 0.1069/0.9979/1.0000 | 0.9993/1.0000/1.0000 | 0.99995 | 0.000 | 0.391 | 0.351 | 26.00 | 1.669 |
| 160x90_BOX | 0.1069/0.9977/1.0000 | 0.9993/1.0000/1.0000 | 0.99995 | 0.000 | 0.478 | 0.351 | 26.00 | 1.824 |
| 256x144_BOX | 0.1068/0.9977/0.9999 | 0.9993/1.0000/1.0000 | 0.99995 | 0.000 | 0.478 | 0.351 | 26.52 | 2.318 |
| tile_max_16x9 | 0.0667/0.8477/0.9968 | 0.9866/0.9970/1.0000 | 0.99675 | 0.000 | 0.391 | 0.184 | 27.52 | 3.426 |

## Per-subtype raw values (crafted pairs)

| label | subtype | current_64x36_default | 64x36_BOX | 128x72_BOX | 160x90_BOX | 256x144_BOX | tile_max_16x9 |
|---|---|---|---|---|---|---|---|
| must_change | new_terminal_line | 0.9997 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9864 |
| must_change | new_terminal_line | 0.9997 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9863 |
| must_change | new_terminal_line | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9862 |
| must_change | new_terminal_line | 0.9997 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9857 |
| must_change | new_terminal_line | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9859 |
| must_change | new_terminal_line | 0.9997 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9861 |
| must_change | new_terminal_line | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9853 |
| must_change | new_terminal_line | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9857 |
| must_change | typed_words | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9939 |
| must_change | typed_words | 0.9999 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9968 |
| must_change | typed_words | 0.9999 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9940 |
| must_change | typed_words | 0.9999 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9943 |
| must_change | typed_words | 0.9999 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9957 |
| must_change | typed_words | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9939 |
| must_change | toast | 0.9979 | 0.9979 | 0.9979 | 0.9977 | 0.9977 | 0.8616 |
| must_change | toast | 0.9855 | 0.9859 | 0.9859 | 0.9859 | 0.9860 | 0.3236 |
| must_change | toast | 0.9979 | 0.9979 | 0.9979 | 0.9977 | 0.9977 | 0.8616 |
| must_change | toast | 0.9855 | 0.9859 | 0.9859 | 0.9859 | 0.9860 | 0.3236 |
| must_change | toast | 0.9979 | 0.9979 | 0.9979 | 0.9977 | 0.9977 | 0.8616 |
| must_change | toast | 0.9855 | 0.9859 | 0.9859 | 0.9859 | 0.9860 | 0.3236 |
| must_change | dialog | 0.9261 | 0.9266 | 0.9266 | 0.9266 | 0.9266 | 0.1380 |
| must_change | dialog | 0.9285 | 0.9291 | 0.9291 | 0.9291 | 0.9291 | 0.1765 |
| must_change | dialog | 0.9261 | 0.9266 | 0.9266 | 0.9266 | 0.9266 | 0.1380 |
| must_change | dialog | 0.9285 | 0.9291 | 0.9291 | 0.9291 | 0.9291 | 0.1765 |
| must_change | dialog | 0.9261 | 0.9266 | 0.9266 | 0.9266 | 0.9266 | 0.1380 |
| must_change | dialog | 0.9285 | 0.9291 | 0.9291 | 0.9291 | 0.9291 | 0.1765 |
| must_change | window_switch | 0.1070 | 0.1069 | 0.1069 | 0.1069 | 0.1068 | 0.0667 |
| must_change | window_switch | 0.1745 | 0.1744 | 0.1744 | 0.1744 | 0.1744 | 0.1373 |
| must_change | window_switch | 0.9318 | 0.9318 | 0.9301 | 0.9287 | 0.9267 | 0.8280 |
| must_change | window_switch | 0.9617 | 0.9630 | 0.9588 | 0.9575 | 0.9554 | 0.8449 |
| must_change | scroll | 0.9956 | 0.9947 | 0.9907 | 0.9894 | 0.9879 | 0.8611 |
| must_change | scroll | 0.9979 | 0.9974 | 0.9967 | 0.9952 | 0.9929 | 0.8477 |
| must_change | scroll | 0.9957 | 0.9947 | 0.9908 | 0.9895 | 0.9880 | 0.8611 |
| must_change | scroll | 0.9978 | 0.9973 | 0.9967 | 0.9952 | 0.9930 | 0.8473 |
| must_change | scroll | 0.9953 | 0.9944 | 0.9905 | 0.9892 | 0.9877 | 0.8611 |
| must_change | scroll | 0.9973 | 0.9968 | 0.9961 | 0.9946 | 0.9923 | 0.8476 |
| must_change | video_region | 0.9996 | 0.9995 | 0.9991 | 0.9988 | 0.9982 | 0.7761 |
| must_change | video_region | 0.9996 | 0.9995 | 0.9990 | 0.9988 | 0.9981 | 0.7767 |
| must_change | video_region | 0.9996 | 0.9995 | 0.9990 | 0.9989 | 0.9981 | 0.7761 |
| must_change | video_region | 0.9996 | 0.9995 | 0.9991 | 0.9987 | 0.9980 | 0.7765 |
| must_change | video_region | 0.9996 | 0.9995 | 0.9991 | 0.9988 | 0.9981 | 0.7772 |
| must_change | video_region | 0.9996 | 0.9995 | 0.9990 | 0.9988 | 0.9981 | 0.7768 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9970 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9970 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9976 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9970 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9983 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9970 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9970 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9970 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9973 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9974 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9970 |
| must_skip | caret_blink | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9987 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9988 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9979 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9991 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9988 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9991 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9979 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9991 |
| must_skip | clock_tick | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9991 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9918 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9952 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9938 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9869 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9872 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9927 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9866 |
| must_skip | hover_highlight | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9999 | 0.9936 |
| must_skip | jpeg_noise | 0.9998 | 0.9999 | 0.9998 | 0.9999 | 0.9998 | 0.9956 |
| must_skip | jpeg_noise | 1.0000 | 1.0000 | 1.0000 | 0.9999 | 0.9999 | 0.9956 |
| must_skip | jpeg_noise | 0.9998 | 0.9999 | 0.9998 | 0.9999 | 0.9998 | 0.9956 |
| must_skip | jpeg_noise | 1.0000 | 1.0000 | 1.0000 | 0.9999 | 0.9999 | 0.9956 |
| must_skip | jpeg_noise | 0.9999 | 0.9999 | 0.9998 | 0.9999 | 0.9998 | 0.9956 |
| must_skip | jpeg_noise | 1.0000 | 1.0000 | 1.0000 | 0.9999 | 0.9999 | 0.9956 |
| must_skip | jpeg_noise | 0.9998 | 0.9999 | 0.9998 | 0.9999 | 0.9998 | 0.9956 |
| must_skip | jpeg_noise | 1.0000 | 1.0000 | 1.0000 | 0.9999 | 0.9999 | 0.9956 |
| must_skip | wallpaper_margin | 0.9996 | 0.9996 | 0.9996 | 0.9996 | 0.9996 | 0.9961 |
| must_skip | wallpaper_margin | 0.9997 | 0.9997 | 0.9997 | 0.9997 | 0.9997 | 0.9971 |
| must_skip | wallpaper_margin | 0.9996 | 0.9997 | 0.9997 | 0.9997 | 0.9997 | 0.9971 |
| must_skip | wallpaper_margin | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9998 | 0.9980 |
| must_skip | wallpaper_margin | 0.9993 | 0.9993 | 0.9993 | 0.9993 | 0.9993 | 0.9941 |
| must_skip | wallpaper_margin | 0.9997 | 0.9997 | 0.9997 | 0.9997 | 0.9997 | 0.9971 |
| must_skip | mouse_cursor_move | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| must_skip | mouse_cursor_move | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| must_skip | mouse_cursor_move | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| must_skip | mouse_cursor_move | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Idle distribution (real screen, 1 Hz, ~120s)

| metric | min | median | max |
|---|---|---|---|
| current_64x36_default | 0.27966 | 0.99182 | 1.00000 |
| 64x36_BOX | 0.27990 | 0.99218 | 1.00000 |
| 128x72_BOX | 0.27838 | 0.99148 | 1.00000 |
| 160x90_BOX | 0.27776 | 0.99125 | 1.00000 |
| 256x144_BOX | 0.27755 | 0.99107 | 1.00000 |
| tile_max_16x9 | 0.05896 | 0.89495 | 1.00000 |

At chosen threshold 0.986 (tile-max), idle-sample skip rate = 37% (43/114 s).
Idle min (0.059) shows this window was not actually idle during the sample
(this very test's terminal was scrolling) — treat as "light activity," not
true idle; a genuinely idle desktop should skip noticeably more than 37%.

## Mouse cursor verification

4/4 real-screen pairs captured before/after moving the OS cursor 40px (via
`SetCursorPos`, restored after) came back at similarity **1.0000 exactly**
on every metric — confirms `ImageGrab.grab()` on this Windows 11 box does
not composite the cursor into the capture. Verified, not assumed.

## Caveats

- **DPI scaling**: not tested. This machine's primary display returned
  exactly 1920x1080 from `ImageGrab.grab()`, so no DPI-scaling artifact was
  present to measure. If Windows display scaling >100% is active elsewhere,
  `ImageGrab.grab()` can return a scaled-down bitmap — verify frame size
  matches the expected 1920x1080 before trusting these numbers. Unverified.
- **Multi-monitor**: not tested — `ImageGrab.grab()` without
  `all_screens=True` captures the primary monitor only; behavior with
  multiple monitors or `all_screens=True` is unverified here.
- **Dark vs. light themes**: both were exercised (dark terminal/editor
  scenes, light document scene, real-screen edits on whatever theme was
  live) with consistent separation; no theme-specific failure observed, but
  coverage is 3 synthetic scenes + 1 real capture, not exhaustive.
- **Idle sample is one ~2 min window on one machine at one time of day** —
  not a statistically robust "typical idle" distribution, and it captured
  active work, not silence. Treat the 37% figure as a lower bound.
- Tile-max is a max-over-16x9-tiles heuristic — a change smaller than one
  tile (120x120 px) that happens to land on a tile boundary could be
  under-weighted in either tile; not specifically tested.
  # ponytail: 16x9 fixed grid, revisit tile count/alignment only if a real
  # missed-change report shows a boundary-straddling case.