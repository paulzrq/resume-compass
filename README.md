# Resume Compass

AI-assisted resume assessment for **Grace Harbor Academy (仁港学院)**. Upload a resume, choose a career path, and receive evidence-based feedback on how the resume presents your qualifications for that role.

[Open the app](https://resume-compass.streamlit.app/)

## Features

- **28 career paths**, organized into four categories with a two-level selector.
- **PDF and image uploads** (PNG, JPG/JPEG, and WEBP) for standard assessments.
- **Seven scoring dimensions**, with role-specific weights and scoring anchors.
- **Experimental In-depth assessment** for text-readable PDFs, using an evidence planner, a tool-assisted scorer, and a review auditor.
- **Personalized share cards** with role-specific characters and colors. One of five layouts—A, B, D, E, or F—is selected for each assessment.
- **Responsive web reports** for phones and desktops, with a downloadable PDF and resume annotations where available.
- An English interface and English assessment instructions. Verbatim resume evidence retains its original language.
- Mobile layouts and system-aware light/dark styling on the home screen.

## Assessment flow

1. Upload a resume and select a category and career path.
2. Optionally enable **In-depth assessment** for a PDF.
3. Start the assessment. The app first displays a share card.
4. Use the share action to reveal the report and generate the downloadable PDF.

Native sharing depends on browser and device support. The share action is a report-unlock interaction; it does **not** verify that a user actually published a post or sent a message in WeChat or another app.

### Standard assessment

The app extracts PDF text, or submits an uploaded image for visual analysis, and requests structured scores from Anthropic Claude. Python validates the response and calculates the weighted total. The default is one scoring run.

### In-depth assessment

The LangGraph pipeline follows **Plan → Score → Review → Report**:

- **Planner:** chooses an evidence-gathering strategy for each dimension.
- **Scorer:** uses scoring anchors, local job-description references, and quote verification to assess the resume.
- **Reviewer:** audits the result and can correct dimension scores.
- **Report step:** calculates the total deterministically and assembles the result.

The app uses one scoring round with review, rather than routinely sending the resume back through another scoring round. Invalid model responses may trigger one automatic retry. Tool use can still require multiple model requests, so In-depth mode generally takes longer and costs more than standard assessment.

Cost controls include batched quote verification, scoring anchors supplied together, prompt caching in the tool-assisted scoring flow, and bounded JD excerpts: at most two entries per search, with up to 1,200 characters per entry before the truncation marker. Retrieved JD text is cached within an assessment. These controls do not guarantee a fixed latency or price.

## Scoring framework

`framework.json` defines the dimensions, scoring anchors, career paths, weights, bonus rules, and tiers. It is the source of truth for scoring configuration.

| Dimension | Key |
| --- | --- |
| Education | `edu` |
| Internship / Work Experience | `exp` |
| Projects | `proj` |
| Skills & Tools | `skill` |
| Certifications & Exams | `cert` |
| Leadership & Extracurriculars | `lead` |
| Resume Presentation | `present` |

Each dimension is scored from 1 to 5. Python applies field weights, eligible bonuses, and score caps to calculate the final score out of 100. AI assessments are advisory; they are not hiring decisions or guarantees of career outcomes.

## Run locally

Use **Python 3.12** and create a fresh virtual environment for your operating system.

```bash
git clone https://github.com/paulzrq/resume-compass.git
cd resume-compass
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r app/requirements.txt
```

Configure the API key in `.streamlit/secrets.toml` at the repository root:

```toml
ANTHROPIC_API_KEY = "your-api-key"
```

Alternatively, provide `ANTHROPIC_API_KEY` through your environment. Streamlit Secrets take precedence over the environment variable. The public interface does not ask students to enter an API key.

Start the app from the repository root:

```bash
python -m streamlit run app/app.py
```

Do not reuse a virtual environment copied from another machine or operating system. Model identifiers are configured in `app/scoring.py`; the API account must have access to the configured model. API usage is billed separately from hosting.

## Optional email archive

When email is configured, the report workflow can send the generated report and original resume as attachments. The current implementation uses Gmail SMTP with STARTTLS on port 587.

Add these settings to Streamlit Secrets only if email archival is intended:

```toml
SMTP_SENDER_EMAIL = "sender@example.com"
SMTP_SENDER_PASSWORD = "your-app-password"
REPORT_RECIPIENT_EMAIL = "archive@example.com"
```

If the recipient is omitted, the sender address is used. Without sender credentials, email archival is skipped. Review the destination and obtain appropriate consent before using this feature with student data.

## Development checks

Run from the repository root with dependencies installed:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python tests/check_result_flow.py
python tests/check_result_flow.py --agent
```

These checks use mocked model responses. The result-flow checks cover the share gate, report rendering, PDF creation, and rerun behavior while mocking email delivery and report-file writes. They do not validate live API access, production latency, native mobile sharing, or the full browser layout.

Before deployment, also inspect the app in a browser at desktop and mobile widths, in light and dark modes. Use synthetic resumes for development and live API smoke tests.

## Deployment

The hosted app runs on Streamlit Community Cloud:

- Repository: `paulzrq/resume-compass`
- Branch: `main`
- Entry point: `app/app.py`
- Python version: **3.12**

Configure `ANTHROPIC_API_KEY` in the app's Streamlit Cloud Secrets. Configure the optional email settings there if needed.

**Pushing to `main` triggers deployment to the live app.** Verify locally and inspect the staged files before pushing. Python 3.12 is the deployment baseline; review dependency compatibility before changing it, especially the pinned `cryptography` version.

## Repository map

```text
app/
  app.py                 Streamlit entry point and assessment/report workflow
  home_ui.py             Home screen, career picker, and responsive styling
  scoring.py             Standard scoring, validation, and JD loading
  agents/                In-depth graph, prompts, nodes, and tools
  web_report.py          Responsive HTML report
  report.py              PDF report generation
  share_card.py          Share-card generation and sharing interface
  highlight.py           Resume annotation helpers
  emailer.py             Optional email archival
  mascots.py             Career-character mapping
  mascots/               Original career illustrations
  mascots_transparent/   Transparent illustrations for the home screen
  assets/                Static visual assets
  fonts/                 Bundled fonts
  requirements.txt       Runtime dependencies
framework.json           Scoring configuration
jd-reference-library/    Career-specific job-description references
tests/                   Offline regression and result-flow checks
eval/                    Evaluation tooling
```

JD references are organized by field ID. Entries include source information and sections for responsibilities, basic requirements, and preferred qualifications. Internal “Implications for Our Framework” notes are excluded from model input. The standard scoring path caps injected JD entries independently of the library's total size.

## Data handling and limitations

- Resume content is sent to Anthropic for assessment. Optional email archival sends the report and original resume to the configured recipient.
- Keep API keys, SMTP credentials, real resumes, generated student reports, and private evaluation data out of this public repository.
- `reports/`, `resumes/`, `eval/raw_resumes/`, `app/samples/`, and local secrets are excluded from version control. Inspect staged files before every commit.
- Streamlit Cloud's local filesystem is temporary. Downloaded files or an explicitly configured archive are needed for long-term retention.
- The app does not currently provide a user login gate or per-user spending quota. Account-level API controls and access management should be considered before broader rollout.
- Scanned PDFs may not provide usable extracted text. Standard image assessment is available, but image inputs do not support the same text-based quote verification as readable PDFs. In-depth mode requires PDF text.
- Native share-sheet behavior varies by browser and operating system; browser sharing cannot prove that a social post was published.
