# Spain autónomo accounting toolkit

Keep your invoices, income and expenses together, and see how they affect your
accounts and tax preparation in Spain.

This application is for self-employed people (autónomos) who want to review their
records and understand the figures behind them. The browser interface is available
in Russian, English and Spanish.

For complete command-driven accounting flows, read the [agent workflow](docs/AGENT_WORKFLOW.md) and [CLI capability matrix](docs/CLI_CAPABILITIES.md). They cover reviewed income, expenses, depreciation, retries and settings.

## What you can do

- Add income invoices and expense documents, keeping the originals as evidence.
- Review missing details and tax treatment before approving and posting entries.
- See income, expenses and working tax figures for each quarter.
- Track equipment and depreciation, spreading its deductible cost over time.
- Keep tax filing receipts and customer VAT checks alongside your records.

## Installation choices

The core `spain-autonomo-taxes` package provides `autonomo-tax` and installs from
`backend/` without Node or browser assets. The optional
`spain-autonomo-taxes-ui` package provides `autonomo-web` and the compiled
interface. Install both packages at the same version for browser use.

From source, install the core with `python -m pip install -e ./backend`. For the
interface, build `frontend/` first and install `-e ./backend -e ./packages/ui`.
Matching prebuilt wheels do not require Node at runtime.

## Start here

Follow [Local setup](SETUP.md) to install the application and open it in your
browser. The guide covers macOS, Linux and Windows.

Once it is running, add your taxpayer details in
[Account settings](docs/user/ACCOUNT_SETTINGS.md). Then follow the
[accounting workflow](docs/user/ACCOUNTING_WORKFLOW.md) to add a document, check its
details and post the reviewed entry.

See [How Google Drive integration works](docs/user/GOOGLE_DRIVE.md) (Russian) for
existing-file links, local uploads, folder selection, and independent cloud copies.

Routine review and posting happen in the application, without administrator
scripts or a terminal.

## Understanding the figures

AEAT is Spain's tax agency. Posting adds an approved entry to the application's
working accounting calculations. Approval and posting are separate steps;
neither transfers money, proves payment or submits a return to AEAT.

For the annual personal income tax return (Modelo 100), the application provides
business-activity figures to compare with Renta WEB. It does not calculate your
full personal return or final tax bill. Use the
[Renta WEB draft checklist](docs/user/RENTA_DRAFT_CHECKLIST.md) for that review.

RETA is Spain's social security scheme for self-employed people. The
[RETA bracket check](docs/user/RETA_CHECK.md) estimates whether your recorded
contribution bases fit your income so far. It is shown separately from AEAT tax
figures and is a provisional estimate, not an official social security decision or a
projection of future income.

This project does not provide legal or tax advice. Check figures against official
sources and a qualified professional before filing.

## Your records

Invoices, accounting records and credentials belong in private storage outside
this repository. Tax calculations run locally. Selected review and configured
storage services use the network; [Local setup](SETUP.md#outbound-network-use)
explains what is sent and when.

## More guidance

The [public read-only demo](docs/development/PUBLIC_DEMO.md) runs the same application
screens and backend against an isolated synthetic SQLite database. It cannot
upload, edit, post or submit anything. Document links open one blank sample PDF.

Browse the [documentation](docs/README.md) for help with statuses, exchange rates,
VAT checks and accounting corrections.

For development, see [builds and tests](docs/development/UI_DEVELOPMENT.md) and
[repository privacy checks](docs/development/PRIVACY.md). For a server installation,
see [Operations](ops/docs/README.md) and [Disaster recovery](ops/docs/DISASTER_RECOVERY.md).
