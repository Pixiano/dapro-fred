# Done 2026-10-05

## Phase 8: deep self-analysis
- `selfanalysis.py` + `schema_selfanalysis.sql`: read-only whole-memory agent (subclass of the year agent) that answers what it understands about Vatsal that he may not; four labelled claim kinds with evidence rules enforced in code; saved as a report (`latest_report`), on request and quarterly (`run_selfanalysis`, `run_selfanalysis_pending`); strong inference claims go straight into the personal model (`PersonalModel.add_analysis`, source `analysis`). Design: [phase8_self_analysis_design.md](phase8_self_analysis_design.md). Self-check: `python selfanalysis.py`.
- Not covered yet: backup module does not have a dedicated part for the self-analysis reports (they are in the sqlite snapshot); the report is not yet in the md/pdf parts.
