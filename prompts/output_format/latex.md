- Write final deliverables as **LaTeX**. Produce a self-contained, compilable
  document and save it to `results/<name>.tex` via `fs_write_workspace.write`:
  - Start with `\documentclass[11pt]{article}` and load only widely-available
    packages (`amsmath`, `amssymb`, `graphicx`, `booktabs`, `hyperref`, `siunitx`).
    Do **not** rely on packages that need network fetching or shell-escape.
  - Use real LaTeX structure: `\section{}`/`\subsection{}`, `equation`/`align`
    for math, `tabular`+`booktabs` for tables, and `\includegraphics` for figures.
  - Save any figures as PNGs into `results/` first (matplotlib `Agg` backend),
    then reference them by relative filename so they resolve at compile time.
- After writing the `.tex`, compile it: call `latex_compile.render` with
  `path="results/<name>.tex"`. On success it returns the produced
  `results/<name>.pdf`.
- If the compile **fails**, the tool returns the tail of the LaTeX log. Read it,
  fix the `.tex`, and retry `latex_compile.render` **once**. If it still fails,
  ship the `.tex` as-is and tell the human the PDF didn't compile plus the error —
  **never delete or lose the written `.tex`**.
- In your report to the human, cite the deliverable paths precisely (the `.tex`
  and, when it compiled, the `.pdf`).
