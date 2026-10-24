# Initial infrastructure validation

CPU validation completed on the supplied participant dataset. This is not a prediction accuracy report.

- Sample preparation: 32 cases.
- Full preparation: 518 cases.
- Evidence ingestion: 8,142 units from the 94-file manifest plus system activity.
- Extraction gaps: zero. OCR/text extraction was executed; content accuracy still requires review.
- Ten authored tests passed (`python3 -m unittest discover -s code/tests -v`).
- Compilation and `git diff --check` passed.
- Local model inference, GPU memory fit, sample prediction accuracy, and final output are unrun. This runtime has no installed PyTorch/Transformers/model weights; the notebook is prepared for Kaggle execution.

The authored tests exercise historical email resolution, exclusion of future evidence, linking anonymized resources, service-name isolation, identifier-prefix isolation, non-deletion serialization, rejection of unsupported completion, missing/extra predictions, and invalid state scoring. They are synthetic software tests, not additional scored benchmark cases. Add richer policy/state cases after the inference baseline is measured.

During validation, an SQL import restriction initially rejected the dataset's `foreign_keys` pragma; that specific pragma is now allowed while attached databases and extension loading remain blocked. A resource-linking bug initially joined unrelated evidence through service names; restricting links to numeric resource identifiers fixed it, with a regression test.

## Retrieval size and coverage

- sample: minimum/median/maximum packet characters 2834/5589/8529; 3 cases have no retrieved evidence units. These require intent/evidence investigation, not an automatic COMPLETE decision.
- full: minimum/median/maximum packet characters 2808/5283/16855; 9 cases have no retrieved evidence units. These require intent/evidence investigation, not an automatic COMPLETE decision.

The decoder is deterministic (greedy), but GPU kernels and software versions may affect reproducibility. Model caches retain prompt/model identity and token counts. Final semantic correctness depends on retrieval coverage and model reasoning; schema validity does not establish accuracy.
