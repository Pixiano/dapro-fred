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

All runs isolated; general = the 10-category suite. Latency is per turn
across the main suite; the baseline's is median 4.9 s, p90 11.4 s.

| rank | folder | model | tools | conv. | deleg. | memory | general | median / p90 | thinking |
|---|---|---|---|---|---|---|---|---|---|
| base | – | Qwen3.5-4B Q6_K | 77.8% | 76.2% | 87.5% | 75% | 87.3% | 4.9 / 11.4 s | FRED rule: on above 175 chars |
| 1 | `3/` | AgentMercury-Qwen3.5-4B Q6_K | **80.2%** | 71.4% | 87.5% | 75% | **90.9%** | 4.6 / 10.2 s | same rule (same template) |
| 2 | `2/` | LFM2.5-2.6B Q8_0 | 72.8% | 66.7% | 87.5% | **100%** | **90.9%** | 9.9 / 39.7 s | **always on**: the template ignores FRED's switch |
| 3 | `1/` | MiniCPM5-2B Q8_0 | 72.8% | 71.4% | 100% | 75% | 80.0% | – | FRED rule |

All four score 100% on injection and links. Results files:
`benchmarking/results/fred-LFM2.5-2.6B-Q8_0_20261001_*.json` and
`fred-AgentMercury-Qwen3.5-4B.Q6_K_20261001_*.json`.

The ranking is only re-ordered once a contender has been benchmarked.
Weights are restored with `../../fetch_model.py minicpm5|lfm25|mercury`.
