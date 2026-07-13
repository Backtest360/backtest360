# Contributing

Thanks for using `backtest360` and for taking the time to help improve it.

## How contributions work here

`backtest360` is the official Python client for the Backtest360 API. Its public
surface tracks the API and is maintained and released centrally to stay in sync
with it.

For that reason this is an **issues-only** project: bug reports and feature
requests are very welcome, but we generally **do not accept pull requests**. The
best way to contribute is to open a clear issue — it goes straight onto our
roadmap and is released as a normal versioned update.

## Reporting a bug

Please use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). A
good report includes:

- **Client version** — `python -c "import backtest360; print(backtest360.__version__)"`
- **Python version and OS**
- A **minimal reproducible example** — the smallest snippet that triggers the issue
- The **full traceback**, if any

The more precise the report, the faster we can fix it.

## Requesting a feature

Please use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
Suggestions about the shape of the API — method names, arguments, return types —
are especially helpful. Show us how you'd like to call it.

## Security issues

Please **do not** open a public issue for security reports. See
[SECURITY.md](SECURITY.md) for how to report vulnerabilities privately.

## Questions and feedback

Have a question, or feedback that isn't a bug or feature request? Email us at
**hello@backtest360.com** — we read everything.
