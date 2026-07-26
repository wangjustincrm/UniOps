# Remittance Email Template Customization

Date: 2026-07-23
Branch: `feature/batch-payment-remittance` (continues the remittance feature)
Worktree: `C:/Project/uniops-remittance`

## Problem

The remittance advice email is entirely hardcoded in
`finance-api/app/services/remittance_template.py`. Changing the heading, greeting, intro
sentence, footer/disclaimer, subject line, brand colour, or adding a logo requires a code
change and redeploy. An administrator should be able to customize the parts of the email
that surround the payment table, from Settings, without touching code.

## Goal

In Portal → Admin → Remittance Advice, let an administrator customize every part of the
remittance email **except the core payment table**, which stays code-generated. Customizable:
subject, heading, greeting, intro, footer, brand colour, and whether the company logo appears.
The editor shows a live preview of the non-table parts.

## Scope

In scope:

- Customizable text fields (subject, heading, greeting, intro, footer) with `{{placeholder}}`
  substitution done on the backend at render time.
- A brand colour field (affects heading colour and table-header background).
- A logo toggle that reuses the existing `company_config.logo_data_url`.
- A frontend-only live preview of the customizable parts.
- Every field falls back to the current hardcoded default when blank/absent, so an
  unconfigured company gets exactly today's email.

Out of scope (YAGNI):

- Changing the payment table itself (columns, ordering, per-line format) — deliberately fixed.
- Separate templates per recipient type — a single shared template handles vendor and
  employee via the `{{doc_type}}` placeholder (decided during brainstorming).
- Uploading a new logo here (reuses the company logo already in `company_config`).
- Rich-text/WYSIWYG editing — fields are plain text with `{{placeholders}}`, matching the
  existing PO Email Template convention.
- Per-language templates.
- A server-rendered preview endpoint — the preview is pure frontend.

## Design

### 1. Data model — no migration

The template nests inside the existing `remittance_config` JSONB blob. epms-api's
`PATCH /config` already accepts an arbitrary dict for `remittance_config`, so no backend
schema change and no migration are needed.

```json
"remittance_config": {
  "enabled": true,
  "from_email": "...", "from_name": "...", "cc_email": "...",
  "smtp_user": null, "smtp_password": null,
  "template": {
    "subject":     "Remittance Advice — {{company_name}} — {{reference}}",
    "heading":     "Remittance Advice",
    "greeting":    "Dear {{payee_name}},",
    "intro":       "The following {{doc_type}} have been paid.",
    "footer":      "Reference: {{reference}}\nPayment method: {{payment_method}}\n\nThis is an automated notification from {{company_name}}. Please do not reply to this message; contact your accounts payable representative with any questions.",
    "brand_color": "#085E5E",
    "show_logo":   true
  }
}
```

Every key under `template` is optional. Each missing or blank value falls back to the
current hardcoded default (the strings already in `remittance_template.py`). `template`
absent entirely reproduces today's email exactly — the same "defaults to `{}` → current
behaviour" property the rest of `remittance_config` already has.

### 2. Placeholders

Substituted on the backend at render time (the email is sent per-payee automatically, with
no per-email edit dialog, so substitution cannot live on the frontend the way the PO
template's does). Supported in `subject`, `heading`, `greeting`, `intro`, `footer`:

| Placeholder | Value |
| --- | --- |
| `{{company_name}}` | company name from `company_config` |
| `{{payee_name}}` | vendor name, or employee name for a claim group |
| `{{doc_type}}` | `invoices` (vendor) / `expense claims` (employee) |
| `{{reference}}` | batch number, or the single payment's reference |
| `{{total}}` | group total, formatted with currency (e.g. `1,234.56 CAD`) |
| `{{payment_method}}` | friendly label (`Bank Transfer`, `EFT`, …) |
| `{{currency}}` | currency code |

Unknown placeholders are left as-is (mirrors `epms-api/app/services/notification.py`'s
substitution behaviour — an admin typo shows the literal `{{foo}}` rather than blanking).

### 3. Escaping and safety

This is the part most likely to go wrong, so it is specified exactly.

- **Template text is admin-authored** (the settings section is `system_admin`-gated) and is
  treated as trusted HTML. Multi-line fields (`intro`, `footer`) convert `\n` → `<br>`.
  `subject` is single-line plain text (no HTML, no `<br>`).
- **Placeholder VALUES are escaped** (`html.escape`) before substitution, because they come
  from data — a vendor named `<script>…` must not inject through `{{payee_name}}`. This
  matches what the current template already does for `party_name`.
- **Brand colour is validated** against `^#[0-9a-fA-F]{3,8}$` before it is interpolated into
  a `style="color:…"` / `background:…` attribute. An invalid value falls back to the default
  `#085E5E`. Without this, an admin value like `red;}</style><script>…` could break out of the
  attribute.
- **Logo** is `company_config.logo_data_url`, an admin-uploaded `data:` URI, rendered as
  `<img src="…" style="max-height:48px">`. Shown only when `show_logo` is true AND the data
  URL is present.

### 4. Assembly order (table unchanged)

```
[logo, if show_logo and logo present]
<h2 style="color:{brand_color}">{heading}</h2>
<p>{greeting}</p>
<p>{intro}</p>
[code-generated payment table — table-header background uses {brand_color}]
<div style="…">{footer}</div>
```

The table markup (rows, Total row, the `Invoice No` / `Claim No` header column choice) is
exactly what `render()` produces today. Only its header background colour becomes
`brand_color`. The `Reference` / `Payment method` meta lines that today sit in a fixed block
below the table are folded into the default `footer` template via placeholders, so there is
one Footer field the admin fully controls.

### 5. Backend code

- **`remittance_template.py`** — `render()` gains parameters for the resolved template and
  the logo. New signature:
  `render(group, *, company_name, reference, payment_method, template=None, logo_data_url=None) -> (subject, html)`.
  `template` is a dict of the customizable fields (already merged with defaults by the
  caller, or merged here — see below). A private `_substitute(text, values)` does the
  `{{key}}` → escaped-value replacement; a private `_safe_color(value)` validates the hex.
  With `template=None` and `logo_data_url=None`, output is byte-for-byte today's email, so
  every existing call and test keeps working.
- **`remittance_config.py`** — the resolver additionally surfaces the template dict, the
  brand colour, `show_logo`, and the company `logo_data_url` (read from `company_config` in
  the same query that already reads the SMTP columns). Either extend `RemittanceSettings`
  with a `template: dict`, `logo_data_url: str | None` pair, or return them alongside — the
  plan will pick the cleaner shape. Defaults are applied here (one place), so `render()`
  receives a fully-populated template.
- **`remittance_send.py`** and **`api/v1/remittance.py`** — pass the template + logo through
  to `render()` on both the send path and anywhere else `render` is called. The preview
  endpoint does not need to change (preview is frontend-only), but the send path must use the
  customized template.

### 6. Frontend — Portal Remittance Advice section

Extends the `RemittanceSettings` section added earlier in
`portal/src/pages/admin/AdminPanel.tsx`:

- New inputs under the existing sender-identity fields: **Subject**, **Heading**,
  **Greeting**, **Intro**, **Footer** (Footer and Intro are multi-line), a **Brand Colour**
  colour input, and a **Show company logo** toggle.
- A **placeholder legend** listing the available `{{…}}` tokens, so the admin knows what they
  can insert.
- A **live preview** (pure frontend) rendering the non-table parts: logo (from the config's
  `logo_data_url`, already available on the loaded config), heading in the brand colour,
  greeting, intro, then a muted `[ Payment table appears here ]` block, then the footer.
  Placeholders are substituted with sample values (`Acme Supplies Ltd`, `BP-20260723-0001`,
  `1,234.56 CAD`, `invoices`, etc.) so the admin sees roughly what a vendor receives. The
  preview substitutes the same placeholder set the backend does, using a small shared
  substitution helper — its output is illustrative, not a byte-exact mirror of the backend.
- Each template field saves into `remittance_config.template`. The section keeps the existing
  whole-object PATCH + `isLoading` save-guard behaviour, so the template object is written
  alongside the sender fields (the whole `remittance_config` is replaced on save).

### 7. Testing

Backend (`finance-api/tests/test_remittance.py`):

- A custom template substitutes each placeholder correctly in subject, heading, greeting,
  intro, and footer.
- A blank/absent template falls back to the current default strings (assert the shipped
  wording still appears).
- `{{doc_type}}` renders `invoices` for a vendor group and `expense claims` for an employee
  group.
- An invalid `brand_color` (`red;}</style>`) falls back to `#085E5E` and does not appear raw
  in the HTML.
- A placeholder value that contains markup (vendor named `<script>`) is escaped in the output.
- The logo `<img>` appears only when `show_logo` is true and a `logo_data_url` exists, and is
  absent otherwise.
- `render()` with no template argument produces the existing output (guards the fallback path
  and keeps older tests meaningful).

Frontend: Portal typecheck stays at 0 errors.

## Rollout

Pure additive: no migration, no backend schema change, no epms change. The feature is inert
until an admin edits a template field — an unconfigured `remittance_config.template` renders
exactly today's email. Ships on the same branch as the rest of the remittance feature.
