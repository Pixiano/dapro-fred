# Assembled FRED: brain model testing (still in testing)

Which text LLM should be the brain of the assembled FRED
(`../../ASSEMBLED_FRED.md`)? Every contender runs **sandbox only**. FRED's
full pipeline is unchanged; `benchmarking/run.py` swaps only the main
tier's weights in memory (`FRED_LLM_MODEL`, plus `FRED_LLM_PATCH` for a
parser). Nothing in `Core/` is edited.

## Baseline

**Current FRED: Qwen3.5-4B Q6_K.** Isolated run 2026-09-30
(`benchmarking/results/fred-current_20260930_220102.json`):

| suite | score |
|---|---|
| tools end to end | 77.8% |
| injection | 100% |
| links | 100% |
| conversation | 76.2% |
| delegation | 87.5% |
| memory | 75.0% |
| general (10 categories) | 87.3% |

## Contenders, ranked by how likely each is to replace the baseline

| rank | folder | model | status |
|---|---|---|---|
| 1 | `1/` | MiniCPM5-2B Q8_0 | tested: tools 72.8%, conversation 71.4%, delegation 100%, general 80.0%; below the baseline, fine-tune target |
| – | `2/` | LFM2.5-2.6B Q8_0 | untested; needs a tool-call parser |
| – | `3/` | AgentMercury-Qwen3.5-4B Q6_K | untested; drop-in (same format as the baseline) |

The ranking is only re-ordered once a contender has been benchmarked.
Weights are restored with `../../fetch_model.py minicpm5|lfm25|mercury`.
