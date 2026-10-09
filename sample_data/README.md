# Sample datasets

Three synthetic datasets of 25 rows × 50 columns, each exported to every format the upload accepts:
`.csv`, `.tsv`, `.xlsx`, `.parquet`, `.json` (array of records) and `.jsonl` (one record per line).
No real people: names, emails, phones and licence numbers are made up.

Every file is well formed. The difficulty is in the *values*, not in broken files. All six formats of
one dataset hold the same values. A missing value is an empty CSV/TSV cell, JSON `null`, an empty
Excel cell and Parquet `NULL`. A missing *token* such as `N/A` is text in every format. Clean columns
keep native types (numbers, booleans, dates) in Excel, Parquet and JSON. Messy columns are text everywhere.

Some type differences between formats are expected. CSV/TSV sniffing may read `Yes`/`No` columns as
BOOLEAN, while JSON and Parquet keep them as text. Excel dates come back as TIMESTAMP.

## 1_simple · `store_performance_2025`

One row per store across 25 real Australian cities. No missing values, one type per column, ISO dates.

- Types: IDs, categories, integer counts, money and rates as plain numbers (rates are fractions,
  e.g. `0.31`), 2 dates, 1 boolean, latitude and longitude.
- Geography: real city, state code and postcode, with coordinates. All of them should resolve.
- Patterns to find: December revenue peak (seasonality); capital-city stores earn more per m²;
  revenue scales with floor area; NPS rises with customer satisfaction; audit score falls as shrinkage rises.
- Derived columns: `total_staff` = full time + part time; `total_revenue_2025` = sum of the 12 months.

## 2_medium · `customer_subscriptions`

One row per customer of a subscription app. Target: `churned` (11 of 25 churned).

- Missing data: blanks across many columns (date of birth and age together, gender, satisfaction,
  support tickets, payment method, income, occupation …), plus the literal token `NA` in `income_band`.
- Missing for a reason: `churn_date` and `cancellation_reason` are only filled when the customer churned.
  `last_ticket_date` is only filled when there was a ticket, and `contract_end` only for annual plans.
- Mixed representations: `monthly_fee` as currency text (`$24.99`), `discount` as percentage text
  (`10%`), booleans written as `Yes`/`No` and also as true/false.
- Inconsistent categories: `state` (`NSW`, `nsw`, ` NSW`), `plan` (`Premium`, `premium`, `Premium `),
  `preferred_contact` (`Email`/`email`).
- Personal data to flag: `full_name`, `email`, `phone`, `date_of_birth`.
- Patterns: churners have more late payments and support tickets and lower satisfaction.
  `account_status = Cancelled` is the same as churned (a leakage candidate).

## 3_complex · `field_service_jobs`

Home-maintenance jobs exported from a field-service system. Target: `Complaint Raised` (11 Yes / 14 No).
Column names contain spaces and symbols (`Job #`, `GST %`, `Paid?`, `Lat/Lon`).

- Dates: `Created` mixes ISO, `dd/mm/yyyy` (always Australian, and days over 12 show it), `3 Feb 2025 7:15 AM`
  and Excel serial numbers stored as text. `Scheduled Start` mixes ISO with offset `+09:30` and `dd/mm/yyyy`.
  Some `Created` values have a day of 12 or less, so their day/month order is ambiguous and should go to review.
- Missing tokens: `""`, `N/A`, `n/a`, `-`, `null`, `NULL`, `?`, `unknown`, `NaN`, `none`.
- Sentinels and invalid values: `Labour Hours = -999` (2 rows), `Distance km = -3`, one row with latitude and
  longitude swapped, postcodes that lost their leading zero (`800` for `0800`), and 2 invalid emails
  (`custNN at example.com`).
- Mixed units and formats: `Duration` (`95 min`, `1.6 h`, `1h 35m`, `01:35:00`, `95`),
  `Temperature` (°C and °F), `Parts Cost` (`$1,234.50`, `AUD 85`, `85.00`), `Adjustment` with
  accounting negatives `(45.00)`, `GST %` (`10%`, `0.1`, `10`), `Customer Rating` (`4`, `4/5`, `★★★★`),
  `Contract Value` (`12,500`, `1.2k`), `Equipment Age` (`10+`, `<1`, `unknown`).
- Booleans: `Paid?` mixes `TRUE`, `1`, `✓`, `No`, `n`, `0`, `FALSE` and blank; `First-Time Fix` mixes Yes/Y/true/No/N/false.
- Near-duplicate categories (to review, never auto-merge): `Service Type` (Plumbing / `plumbing ` /
  Plumber; Electrical / Electrician / ELECTRICAL; HVAC / Air-con / A/C / Air Conditioning),
  `Priority` (P1 / High / 1 / urgent), `Technician` (Sam Brooks / sam brooks / S. Brooks),
  `State` (NSW / new south wales; Vic / Victoria; QLD / `QLD ` / Qld; NT / N.T. / Northern Territory),
  `Suburb` (Mt Gambier / Mount Gambier, Darwin City / darwin city).
- Ambiguous places: `Richmond` (VIC, NSW, TAS) and `Newtown` (NSW, VIC), resolved by state and postcode.
- Text and Unicode: the same name in composed and decomposed Unicode (`Zoë` / `Zoë`), `’` vs `'`,
  a non-breaking space, leading spaces, macrons, CJK, emoji, and cells with commas, quotes and line breaks.
- Duplicates: 2 exact duplicate rows (23 distinct rows), plus one conflicting duplicate
  (same `Job ID`/`Job #`, different name case and total). That leaves 22 distinct job IDs.
- Column roles: `Source System` is constant; `Job ID`, `Record Hash`, `Tech ID` and
  `Licence No. (synthetic)` are identifiers; names, emails, phones and licence numbers are personal data.
- Patterns: complaints follow longer `Wait Days` (6.1 vs 3.1 days on average) and failed first-time fixes
  (45% vs 21%), and also higher cost. `Technician Notes` is missing or `n/a` in 10 of 11 complaint rows but only 3 of 14
  others (missing-as-signal). `Invoice Status = Refunded` exactly when a complaint was raised (leakage).
  One `Total (AUD)` of 48,250 is an extreme outlier.
