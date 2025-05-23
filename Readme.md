# InfiniLead Project Setup

This project provides tools for lead enrichment and scoring using LLMs and web enrichment APIs.

## Setup Instructions

### 1. Clone the Repository

```
git clone <your-repo-url>
cd InfiniLead
```

### 2. Create and Activate a Virtual Environment

#### On macOS/Linux:
```
python3 -m venv .venv
source .venv/bin/activate
```

#### On Windows:
```
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install Dependencies

```
pip install -r requirements.txt
```

### 4. Environment Variables

Create a `.env` file in the root directory and add your API keys:

```
OPENAI_API_KEY=your_openai_key
APIFY_API_TOKEN=your_apify_token
PERPLEXITY_API_KEY=your_perplexity_key  # optional, for web enrichment
```

### 5. Data and Logs
- All data files (.csv, .json, .md) are stored in the `data/` folder.
- Log files are stored in the `logs/` folder and are timestamped per run.

### 6. Running the Lead Enrichment Script

You can run the main script as a CLI:

```
python enriching_leads.py --csv-in data/leads.csv --csv-out data/leads_scored.csv
```

For more options, use:
```
python enriching_leads.py --help
```

---

For further details, see the code and comments in `enriching_leads.py`.
