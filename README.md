# WE CARE HOME HEALTHCARE — Billing & Management Software

A complete, production-ready **Home Healthcare Billing & Management Software** built with
FastAPI, Jinja2 templates, vanilla JS/CSS, and **Apache Feather files** (pandas + pyarrow)
as the primary database.

Every screen is fully functional: patients, services, equipment (rent/sale/return),
service + equipment + combined billing, payments, advances/deposits, refunds, quotations,
reports, branded print/PDF documents, WhatsApp sharing, staff permissions, activity logs,
settings, backup, and global search.

## Features

- **Dashboard** — live KPIs, date filters (today → custom range), billing-vs-collection chart
- **Patients** — full history: bills, payments, services, equipment, deposits, quotations, activity
- **Services master** — Per Day / Per Visit / Per Hour billing
- **Equipment master** — daily/weekly/monthly rent + sale, serial tracking, issue/return workflow
  with Good/Damaged/Lost conditions and automatic status sync
- **Bills** — service / equipment / combined, auto bill numbers (`WC-INV-YYYY-000001`),
  server-side totals (Decimal math), preview-before-save, optional payment at creation,
  edit with before/after audit, duplicate (new number, no payment copy), cancel (never delete)
- **Back-date billing** — permission-gated, every back-dated bill logged
- **Payments** — Cash/UPI/Bank (txn ID enforced), auto Paid/Partial/Pending status,
  full payment history, receipts
- **Advances & deposits** — adjust to bills, refund, availability tracking
- **Refunds** — against payments or deposits, originals never deleted
- **Quotations** — create/edit/duplicate/convert-to-bill (original preserved), validity, terms
- **Reports** — daily/monthly collection, pending, service-wise, equipment rental+returns,
  cash-vs-online, profit (revenue vs cost) — all excludable-canceled, refund-aware,
  with print + PDF
- **Documents** — 13 document types (invoices, receipts, statements, handover/return,
  agreement, patient sheet, quotation) sharing **one** A4 design; browser print + real PDF
- **The We Care tax invoice** — logo left / company block right, green section bands,
  Bill To + Invoice Details, Services/Equipment table with Starting Date, Till Date, Days,
  Price, Amount, **invoice amount in words**, payment type, terms, bank details with UPI QR,
  amounts panel, optional signature, page footer — millimetre-identical on screen, in print
  and in the downloaded PDF (same layout engine, `app/services/invoice_layout.py`)
- **WhatsApp share** — pre-filled `wa.me` message with bill/patient/amounts (honest about
  manual PDF attach — browsers can't auto-attach files)
- **Staff & permissions** — 25 granular permissions enforced on every route + UI
- **Activity logs** — every important action with before/after snapshots and IP
- **Settings** — brand name/address/contact/website, **brand colour**, **invoice number prefix**,
  GST, structured bank details (bank, A/c name, A/c no, IFSC, branch), UPI ID,
  **logo / signature image / UPI QR / footer image uploads**, terms, declaration, footer note,
  plus an **Invoice Design** tab (invoice heading, extra header line, footer line, invoice and
  link colour, show/hide PO date, time, QR, page number and signature).
  Every document printed afterwards picks up the latest values automatically.
- **Backup** — one-click timestamped backup of Feather files + uploads
- **Security** — bcrypt passwords, signed sessions, CSRF tokens, validation everywhere

## Installation

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env            # then edit SECRET_KEY and admin password
```

## Database initialization

Feather files are created automatically with correct schemas on first run.
No manual step is needed — just start the server.

Optional demo data (clearly marked `[DEMO]`, development only):

```bash
python seed.py
```

## Run the server

```bash
python run.py
# or
uvicorn app.main:app --reload
```

Open: **http://127.0.0.1:8000**

Default login (change immediately in production):

- Username: `admin` (or `DEFAULT_ADMIN_USERNAME` from `.env`)
- Password: `Admin@123` (or `DEFAULT_ADMIN_PASSWORD` from `.env`)

## Project structure

```
app/
  main.py            FastAPI app, startup init, error handlers
  config.py          env-based configuration
  database.py        Feather DB layer (atomic writes, schemas, numbering)
  security.py        bcrypt + sessions + CSRF
  permissions.py     RBAC matrix
  audit.py           activity logging
  calculations.py    Decimal billing engine (final authority)
  utils.py           formatting, validation, pagination, WhatsApp links
  dependencies.py    login/permission guards, template context
  routers/           auth, dashboard, patients, services, equipment, bills,
                     payments, reports, quotations, settings, staff, documents
  services/          billing, payment, equipment, report, document logic
                     invoice_layout.py  one A4 model for every billing document
                     invoice_pdf.py     draws that model with fpdf2
                     document_service.py data -> model -> HTML / PDF
  templates/         Jinja2 server-rendered UI
  static/            css (incl. invoice_a4.css — the invoice stylesheet),
                     js (vanilla), fonts, uploads
data/                *.feather database files (+ backups/)
documents/pdf/       saved PDFs
logs/application.log error log
tests/               pytest suite for critical business logic
run.py  seed.py  requirements.txt  .env.example
```

## Testing

```bash
pytest -q
```

Covers patient creation, bill numbering, service/equipment calculations, discount/tax,
partial/full/pending payments, refunds, cancellation, duplication, issue/return,
the invoice document model, print page, PDF download and the Invoice Design settings,
permissions, back-date rule, deposits, and activity logging.

## Backup & restore

- **Backup:** Settings → Backup → *Create Backup Now* (`backups/backup_YYYY_MM_DD_HHMMSS/`).
- **Restore:** stop the server, copy the `*.feather` files back into `data/`, restart.

## Production deployment

1. Set a strong `SECRET_KEY` and `DEFAULT_ADMIN_PASSWORD` in `.env`.
2. Run behind a reverse proxy (nginx/caddy) with HTTPS:
   `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2`
3. Schedule regular backups (Settings → Backup, or copy `data/` + `app/static/uploads/`).
4. Restrict file permissions on `data/`, `backups/` and `.env`.

## Deployment

See **[DEPLOY.md](DEPLOY.md)** for a step-by-step PM2 + nginx + HTTPS production setup
(`ecosystem.config.js` is included). Run a single instance only — the Feather-file
storage does not support concurrent writers.
