# SY CSC CENTRE

Offline-first CSC management system — customers, applications, payments,
accounting, reports, operators and backups — built with **Python Flask + MySQL**.
Runs on one Windows PC and is used from that PC, from Android phones and from
other computers on the same Wi-Fi / hotspot / LAN. No internet, no CDN, no
Node.js, no Docker.

---

## 1. What is inside

| Area | What it does |
|------|--------------|
| **Admin portal** | Home, Customer, Operator, Services, Report, Messages (MSJ), Account Sheet, Application Archive, Profile, Backup / Restore / Reset |
| **Operator portal** | Home, Customer, Services (view), Report (if permitted), Messages (MSJ), Profile — with per-operator permissions enforced on the server |
| **Customers** | Full entry form in five sections (personal, parents, contact, address, additional), photo + signature + documents, masked Aadhaar, archive instead of delete |
| **Applications** | Five separate PDF-based forms (PAN, Ration, Ayushman, ABHA, Voting), Charge / Government Charge / CSC Charge / Total, automatic Total Days, duplicate warning, archive, cancel, soft delete |
| **Payments** | Partial payments, allocation across the three charge columns, printable receipt, accounting reversal for refunds |
| **Accounts** | Cash / Bank / UPI ledger, opening balances, paired transfers, income & expense categories, vouchers, government-charge payable |
| **Reports** | 21 report types, daily / weekly / monthly / yearly / custom range, print, CSV (Excel-compatible) |
| **Operators** | Accounts, permissions, attendance, salary with ledger posting, leave with approval |
| **Messages** | Admin ↔ operator messaging, broadcast, read / unread |
| **Backup** | One-click JSON backup, validated restore with automatic safety backup, password + phrase guarded reset |

---

## 2. Requirements

* Windows 7 / 8 / 10 (or any Linux PC), 2 GB RAM is enough
* **Python 3.8** (32-bit or 64-bit) — https://www.python.org/downloads/release/python-3810/
* **MySQL 5.7 / 8.0** or **MariaDB 10.x** — https://dev.mysql.com/downloads/installer/
* No internet connection is needed after installation

All Python packages are pinned in `requirements.txt` for Python 3.8 / Windows 7:

```
Flask==2.0.3      Werkzeug==2.0.3    Jinja2==3.0.3     MarkupSafe==2.0.1
itsdangerous==2.0.1  click==8.0.4     PyMySQL==1.0.2    Pillow==9.0.1
waitress==2.1.2   pytest==7.1.3
```

---

## 3. Installation (Windows, step by step)

Open **CMD** (Start → type `cmd`) and run:

```bat
cd C:\
mkdir SYCSC_CENTRE
:: copy the project folder here, then:
cd C:\SYCSC_CENTRE

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
notepad .env
```

Edit `.env` and set at least `SECRET_KEY`, `DB_USER`, `DB_PASSWORD`.

### 3.1 Create the MySQL database

Open **MySQL Command Line Client** (or `mysql -u root -p`) and run:

```sql
CREATE DATABASE sycsc_centre CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sycsc'@'localhost' IDENTIFIED BY 'sycsc';
GRANT ALL PRIVILEGES ON sycsc_centre.* TO 'sycsc'@'localhost';
FLUSH PRIVILEGES;
```

`init_db.py` can also create the database itself if the MySQL user is allowed to.

### 3.2 Create the tables and the first admin

```bat
python init_db.py
```

Optional demo rows (one operator + one customer):

```bat
python init_db.py --demo
```

First login: **admin / Admin@123** — change this password immediately
(Profile → Change Password).

### 3.3 Start the server

```bat
python app.py
```

The console prints the exact addresses to use:

```
this PC          : http://127.0.0.1:5000
phone / other PC : http://192.168.1.7:5000
```

For a shop PC it is better to use the bundled production server:

```bat
python run_server.py
```

(uses **waitress**, no reloader, no debug output, quieter than the dev server)

---

## 4. Using it from an Android phone or another PC

1. Keep the host PC switched on, MySQL running and `python app.py` running.
2. Connect the phone and the PC to the **same** Wi-Fi or hotspot.
3. Find the host PC's IP: `ipconfig` → look for *IPv4 Address* (e.g. `192.168.1.7`).
4. On the phone open `http://192.168.1.7:5000`.
5. First time only: allow Python through the firewall —

```bat
netsh advfirewall firewall add rule name="SY CSC CENTRE" dir=in action=allow protocol=TCP localport=5000
```

**Important:** devices cannot share live data without a working local network.
If the phone cannot connect, check: same network? correct IP? firewall? port 5000
free (`netstat -ano | findstr :5000`)? server window still open?

Never expose port 5000 or MySQL port 3306 to the public internet.

---

## 5. Money rules used by the system

The PDF application form has three financial columns. The system keeps all
three and never mixes them:

```
Total = Charge + Government Charge + CSC Charge
```

* **CSC revenue** = `payments.csc_amount` **only** (+ `payments.charge_amount`
  when the *Charge* column is classified as `csc_income`).
* **Government charge** is a liability, never income. It is tracked as
  *collected − paid to government = payable* in the Account Sheet.
* Unpaid amounts are never counted as collected revenue.
* The meaning of the PDF *Charge* column is configurable:
  `.env → CHARGE_CLASSIFICATION` or Account Sheet → *PDF "Charge"
  classification* (`csc_income` | `govt_charge` | `other_income`).

**Payment allocation:** when a customer pays less than the total, the amount is
split across the three pending components in proportion to what is still
outstanding, so a partial payment never inflates CSC income. The split is
stored on the payment row and is visible on the receipt.

**Ledger sign convention:** `credit` = money in, `debit` = money out,
`balance = opening + credits − debits`. Refunds post a **negative payment plus a
debit ledger entry**, so history is never rewritten.

---

## 6. IDs

| Record | Format | Example |
|--------|--------|---------|
| Customer | `SYCSC/CUST/<last 5 mobile digits>/<sequence>` | `SYCSC/CUST/43210/0001` |
| Application | `AP/<last 5 mobile digits>/<sequence>` | `AP/43210/00001` |
| Payment | `PAY/<YYYYMMDDHHMM>/<last 5 mobile digits>/<sequence>` | `PAY/202610271635/43210/001` |
| Voucher | `VCH/<YYYYMM>/<sequence>` | `VCH/202610/00001` |

Sequences live in the `id_sequences` table inside a database transaction
(`SELECT … FOR UPDATE` on MySQL, `BEGIN IMMEDIATE` on SQLite) and every code
column has a `UNIQUE` constraint — never `COUNT(*) + 1`.

---

## 7. Project structure

```
SYCSC_CENTRE/
├── app.py                  Flask application factory + entry point
├── run_server.py           waitress production entry point
├── config.py               configuration (.env loader)
├── db.py                   portable database layer (PyMySQL / SQLite)
├── init_db.py              database creation, schema, seed, --demo
├── schema.sql              full MySQL schema (keys, FKs, uniques)
├── requirements.txt        pinned packages (Python 3.8 / Windows 7)
├── .env.example            copy to .env
├── README.md               this file
├── INSTALL_GUJARATI.md     step-by-step Gujarati installation guide
├── TESTING_REPORT.md       honest test report
├── modules/
│   ├── auth.py             login, session, profile, password
│   ├── dashboard.py        admin + operator dashboards
│   ├── customers.py        customer CRUD, documents, archive
│   ├── applications.py     five PDF forms, duplicates, archive, cancel
│   ├── services.py         services, sub-services, activate/deactivate
│   ├── payments.py         payments, receipts, refunds
│   ├── accounts.py         ledger, balances, transfers, CSC settings
│   ├── reports.py          21 reports + CSV export
│   ├── messages.py         MSJ
│   ├── operators.py        operators, permissions, attendance, salary, leave
│   ├── backup.py           JSON backup / restore / reset
│   ├── security.py         hashing, CSRF, decorators, uploads, audit
│   ├── utils.py            IDs, validation, dates, allocation
│   └── i18n.py             Gujarati / English translations
├── templates/              31 Jinja templates (admin + operator + print)
├── static/css/style.css    navy + teal theme, responsive, print styles
├── static/js/app.js        vanilla JS (totals, total days, search, tabs)
├── static/images/logo.svg
├── uploads/                photos, signatures, documents, backups
└── tests/                  pytest suite (83 tests)
```

---

## 8. Tests

```bat
venv\Scripts\activate
python -m pytest tests -v
```

The suite covers ID generation, customer validation, all five service forms,
totals, total days, duplicate detection and override, archive/restore, delete
permissions, partial payments, CSC vs government income, ledger balances,
operator permissions, JSON backup, restore validation, reset guards, CSRF,
password hashing and Aadhaar masking. See `TESTING_REPORT.md` for the exact
result of the run.

Tests use SQLite so they need no MySQL server; the application itself is
MySQL-first. To verify your real MySQL installation run:

```bat
python tests\smoke_mysql.py
```

---

## 9. Backup and restore

* **Backup** → Admin → Backup → *Download JSON Backup* (all tables + metadata).
* Copy the `uploads` folder separately (photos, signatures, documents).
* **Restore** → choose the JSON, tick the confirmation. A safety backup is
  written to `uploads/backups/` before anything is replaced, and the whole
  restore runs in one transaction.
* **Reset** → requires the admin password **and** the phrase `RESET-SYCSC`;
  it clears customers, applications, payments, ledger, messages, attendance,
  salary, leave and audit logs but keeps logins, services and settings.

---

## 10. Known limitations

* One host PC serves the application; if it is switched off or Flask is not
  running, no other device can use the system (that is inherent to an offline
  LAN setup).
* PDF **export** of reports is not generated server-side (no offline PDF engine
  is bundled); reports print to PDF through the browser's *Print → Save as PDF*.
  CSV export is built in and opens directly in Excel.
* Uploaded files are not stored inside the JSON backup — copy `uploads/`.
* The five PDF forms are implemented from the field lists supplied in the
  specification; the original PDF file was not available while building, so if
  it contains extra columns they should be added to `application_details`.
