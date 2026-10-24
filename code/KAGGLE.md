# Offline Kaggle experiments

This is the initial retrieval-and-inference pipeline, not a completed challenge solution. No trained model, prediction accuracy, or GPU memory result is claimed. CPU preparation has been exercised on the supplied data. There is no keyword classifier or fabricated prediction baseline.

Use `kaggle/experiment.ipynb` as the driver, and retain ordinary Python source for reproducible terminal execution. Attach the challenge repository/data and a locally supported instruction model as Kaggle inputs. A small multilingual instruction model (for example a Qwen2.5 3B or 7B Instruct attachment) is a candidate, not an evaluated recommendation. Verify the weight license. Supply the directory containing config.json, tokenizer files and weights. Model loading explicitly disables network downloads and remote code execution.

1. Copy the repository/source attachment into `/kaggle/working/project`; preserve the `code/` layout. Set DATASET to the attached `dataset/` directory and PROJECT to the writable project root in the notebook.
2. CPU preparation needs Python, SQLite, pypdf and Tesseract. Use Kaggle's installed packages first. If missing, install `code/requirements-kaggle.txt` during an Internet-enabled setup session or attach a compatible wheel directory and install with `pip --no-index --find-links ...`. Do not replace Kaggle's PyTorch/CUDA build. Tesseract is a system executable; if absent, install it during setup using the system package manager. An offline session must have these prerequisites available before starting.
3. Run CPU preparation and inspect `coverage.json`. PDF text extraction and image OCR are cached by source SHA-256. An extracted document has not necessarily been interpreted correctly: inspect OCR text for identifiers and resource IDs. Scanned PDFs without embedded text are reported as gaps; PDF OCR/vision fallback is not implemented yet.
4. Enable an available GPU only for inference. Check the allocated GPU/memory in the notebook. Start with 3B weights or `--quantize`; 4-bit loading requires compatible CUDA and bitsandbytes. Do not assume a particular GPU or free-tier quota. Try the samples before all 518 cases. This first version generates one case at a time; batching is a later optimization after correctness and memory use are measured.
5. Evaluate sample predictions. Inspect every non-exact case. The report's positional field accuracy compares fields by step position; exact step precision/recall includes all seven fields. Hidden-data accuracy is unknown.
6. Run the full dataset only after reviewing sample errors. All cases must validate before the final output is written. Caches and partial outputs allow recovery across sessions; persist/download `/kaggle/working/artifacts` through Kaggle notebook outputs. Download source, reports, final output, and relevant development traces separately.

CPU preparation (no weights, no API, no predictions):

```bash
python code/main.py --dataset /path/to/dataset --artifacts /kaggle/working/artifacts/sample
```

Sample model experiment:

```bash
python code/main.py --dataset /path/to/dataset --artifacts /kaggle/working/artifacts/sample --model-path /path/to/local/model --quantize --output /kaggle/working/sample_predictions.csv
python code/evaluation/main.py --reference /path/to/dataset/sample_output.csv --predictions /kaggle/working/sample_predictions.csv --report /kaggle/working/project/code/evaluation --usage /kaggle/working/artifacts/sample/usage.json
```

Full run (writes required output at the project root):

```bash
python code/main.py --dataset /path/to/dataset --requests deletion_requests.csv --artifacts /kaggle/working/artifacts/full --model-path /path/to/local/model --quantize --output /kaggle/working/project/output.csv
```

Do not evaluate full predictions against sample labels. The current evaluator creates usage Markdown together with reference-based evaluation; use the full run's usage ledger to prepare the final usage report, and clearly separate sample accuracy from unlabeled full-run statistics. Record per-model token totals, model calls, average tokens per request, $0 external API expenditure, and actual compute time. Distinguish cache hits from newly executed calls.

## Safeguards and remaining limitations

- Inference cannot read sample labels: labels are loaded only by the separate evaluator. Prompts and retrieval use participant-facing data only.
- Evidence must be both captured and observed by `audit_at`. Current identity snapshots after that time are used only for linkage, explicitly marked in the prompt.
- Resource references expand retrieval to anonymized readbacks. A line is indexed with its preceding line for lookup association; unusual log layouts, cross-line association errors and document text extraction need manual review.
- `reported_result` remains a claim. The local model must judge the evidence and policy; a comprehensive deterministic policy/state engine is not yet implemented.
- Context overflow fails explicitly; no silent truncation. Packet-size distributions are recorded in the validation report. If overflow occurs, improve retrieval or use suitable context capacity rather than deleting relevant evidence blindly.
- Schema checks enforce allowed operations, phases, numbering and citations. They do not prove semantic correctness or complete resource discovery. The model is instructed to abstain when unsupported; abstentions block final CSV output.
- No services, external API credentials, fine-tuning, worktrees, or paid endpoints are needed. Kaggle attachments and compatible dependencies remain required.
- Include prompts and required evaluation/usage details in source/Markdown, not only raw JSON. Final reports need investigated failures, measured improvements, and actual final-run usage; scaffolding is not a finished report.

## Submission

Keep `README.md` plus `code/` in `code.zip` and required reports in `code/evaluation/`. Exclude `dataset/`, attached model weights, `.venv`, caches, secrets, and nested archives. Upload root `output.csv` separately. Keep `log.txt` and the separate `agent-trace.zip` as requested by repository instructions. Notebook execution logs do not replace coding-session traces. Do not embed credentials or unrelated sessions.
