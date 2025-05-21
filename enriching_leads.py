#!/usr/bin/env python3
"""
Lead Enrichment & Scoring (steroids) – v2
=========================================
• Reads Instagram handles from leads.csv
• Scrapes basic profile data with Apify
• Initial score by GPT‑4o‑mini
• Enriches with Perplexity web search (cross‑profiles, income hints)
• Re‑scores with enrichment
• Writes graded_leads-YYYYMMDDT...csv

Improvements over v1
--------------------
▪ All prompts collected in one place
▪ Perplexity returns structured JSON (site, url, probability, implication)
▪ Final score combines IG reasoning + enrichment with GPT
▪ Scoring logic shaped around Austrian real‑estate leads ≥ €200 k
"""

from __future__ import annotations

###############################################################################
#                                DEPENDENCIES                                 #
###############################################################################
import os, re, sys, time, json, textwrap, logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, List, Dict

import pandas as pd
import requests
import openai
from apify_client import ApifyClient
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

import yaml, textwrap

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO
)

###############################################################################
#                               GLOBAL CONFIG                                 #
###############################################################################
# ───── File locations (override via CLI if you like) ─────────────────────────
CSV_IN  = Path("leads.csv")
STAMP   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
CSV_OUT = Path(f"graded_leads-{STAMP}.csv")

# ───── Credentials ───────────────────────────────────────────────────────────
load_dotenv(override=True)                       # picks up .env if present
OPENAI_API_KEY     = os.environ["OPENAI_API_KEY"]
APIFY_API_TOKEN    = os.environ["APIFY_API_TOKEN"]
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")   # optional

openai.api_key = OPENAI_API_KEY
apify          = ApifyClient(APIFY_API_TOKEN)

###############################################################################
#                             CONSTANTS & PROMPTS                             #
###############################################################################
# ―― Business criteria ―――――――――――――――――――――――――――――――――――――――――――――――――――――
TARGET_COUNTRY      = "Austria"
TARGET_SUBREGION    = "Styria"
MIN_BUDGET_EUR      = 200_000

# ―― GPT / Perplexity prompts ―――――――――――――――――――――――――――――――――――――――――――
import yaml, textwrap

# load once at start-up
with open("prompts_config.yaml", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

TAD  = CFG["TARGET_AUDIENCE_DESCRIPTION"]      # handy shortcut for prompts

# ───────────────────────── primary grading prompt ────────────────────────────
PROMPT_GRADE_SYSTEM = textwrap.dedent(f"""
    You are a real-estate lead-qualification expert.

    Give an **integer score 1–5** assessing whether an Instagram account fits
    the following target audience:

    {TAD}

    FORMAT → exactly:

    [your concise argumentation]
    ##Score n
""").strip()

PROMPT_GRADE_USER = textwrap.dedent("""
    Instagram profile
    -----------------
    username      : {username}
    full name     : {full_name}
    location      : {location}
    biography     : {biography}
    external urls : {external_urls}

    Latest captions
    ---------------
    {captions}
""").strip()

# ─────────────────────── enrichment (Perplexity) prompt ──────────────────────
PROMPT_PERPLEXITY = textwrap.dedent("""
    Find online profiles that probably belong to the same person/company and
    could affect our judgment of income or real-estate interest.

    Return a **JSON list** where each object has:
      site          – domain / platform (linkedin, github, …)
      url           – full link
      probability   – 0-1 likelihood it's the same entity
      implication   – one short sentence (e.g. “C-level exec → high income”)

    Only return valid JSON. Nothing else.
""").strip()

# ───────────────────────── re-scoring prompt (after enrichment) ───────────────
PROMPT_SCORE_SYSTEM = textwrap.dedent(f"""
    Output **only** the improved integer score 1–5 (same scale as before)
    after considering both the Instagram argumentation and the enrichment JSON.
    Prefer higher scores when the enrichment increases confidence that the
    profile matches:

    {TAD}
""").strip()

PROMPT_SCORE_USER = textwrap.dedent("""
    Instagram argumentation
    -----------------------
    {ig_reasoning}

    Enrichment
    ----------
    {enrichment_json}
""").strip()


###############################################################################
#                              REGEX / HELPERS                                #
###############################################################################
RE_SCORE = re.compile(r"score[^0-9]{0,10}(\d)", re.I | re.S)

def extract_score(text: str) -> int | None:
    """Return first int 1‑5 found in text or None."""
    m = RE_SCORE.search(text or "")
    if m:
        try:
            val = int(m.group(1))
            return val if 1 <= val <= 5 else None
        except ValueError:
            pass
    return None


def read_handles(csv_path: Path) -> pd.DataFrame:
    """Load CSV and return df with at least 'channelName'."""
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    if "channelName" not in df.columns:
        logging.error("Column 'channelName' not found in %s", csv_path)
        sys.exit(1)
    df["channelName"] = df["channelName"].str.strip().str.lstrip("@")
    df = df[df["channelName"] != ""].drop_duplicates("channelName")
    if df.empty:
        logging.error("No valid handles in %s", csv_path)
        sys.exit(1)
    return df


def batch(lst: List[str], n: int) -> List[List[str]]:
    """Chunk lst into n‑sized blocks."""
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def scrape_profiles(handles: List[str]) -> List[Dict[str, Any]]:
    """Call Apify IG scraper once for ≤100 usernames."""
    try:
        run = apify.actor("apify/instagram-profile-scraper").call(
            run_input={"usernames": handles},
            timeout_secs=1200,
        )
        ds_items = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
        return ds_items
    except Exception as e:
        logging.warning("Apify failed for batch %s…: %s", handles[:3], e)
        return []

###############################################################################
#                         GPT & PERPLEXITY WRAPPERS                            #
###############################################################################

def build_grade_messages(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    captions = "\n".join(
        p.get("caption", "") for p in profile.get("latestPosts", [])
    )
    user_content = PROMPT_GRADE_USER.format(
        username      = profile.get("username", ""),
        full_name     = profile.get("fullName", ""),
        location      = profile.get("location", ""),
        biography     = profile.get("biography", ""),
        external_urls = profile.get("externalUrls", ""),
        captions      = captions[:2000],  # avoid excessive tokens
    )
    msgs = [
        {"role": "system", "content": PROMPT_GRADE_SYSTEM},
        {"role": "user",   "content": user_content},
    ]
    return msgs


@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(openai.OpenAIError),
    reraise=True,
)
def gpt_chat(messages: List[Dict[str, Any]], model="gpt-4o-mini",
             temperature=0.7, max_tokens=1024) -> str:
    resp = openai.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=messages,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content


def initial_grade(profile: Dict[str, Any]) -> tuple[int | None, str]:
    txt = gpt_chat(build_grade_messages(profile))
    return extract_score(txt), txt


def query_perplexity(username: str, ig_reasoning: str) -> List[Dict[str, Any]]:
    """Ask Perplexity for cross‑profile enrichment. Returns list (may be empty)."""
    if not PERPLEXITY_API_KEY:
        return []

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type":  "application/json",
    }
    prompt = (
        f"Instagram username: {username}\n\nIG reasoning:\n{ig_reasoning}\n\n"
        f"{PROMPT_PERPLEXITY}"
    )
    try:
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=40,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        
        return content
    except Exception as e:
        logging.warning("Perplexity failed for @%s: %s", username, e)
        return []


def improve_score(ig_reasoning: str, enrichment: str) -> int | None:
    """Let GPT output new score (1‑5) based on enrichment."""
    if not enrichment:
        return extract_score(ig_reasoning)      # nothing to improve
    messages = [
        {"role": "system", "content": PROMPT_SCORE_SYSTEM},
        {"role": "user",   "content": PROMPT_SCORE_USER.format(
            ig_reasoning=ig_reasoning, enrichment=enrichment
        )},
    ]
    txt = gpt_chat(messages, temperature=0.2)
    return extract_score(txt)

###############################################################################
#                                   MAIN                                      #
###############################################################################

def main(src: Path = CSV_IN, dst: Path = CSV_OUT) -> None:
    df = read_handles(src)
    handles = df["channelName"].tolist()[:5] #TODO for testing only first 5
    logging.info("Loaded %d unique handles from %s", len(handles), src)

    results: List[Dict[str, Any]] = []

    for chunk in batch(handles, 100):
        profiles = scrape_profiles(chunk)
        time.sleep(1)

        for prof in profiles:
            handle = prof.get("username", "unknown")
            logging.info("Scoring @%s …", handle)

            try:
                base_score, ig_reasoning = initial_grade(prof)
            except Exception as e:
                logging.error("OpenAI error @%s: %s", handle, e)
                base_score, ig_reasoning = None, str(e)

            enrichment_str = query_perplexity(handle, ig_reasoning)
            final_score = improve_score(ig_reasoning, enrichment_str) or base_score

            results.append(
                {
                    "username":   handle,
                    "score":      final_score,
                    "reasoning":  ig_reasoning,
                    "enrichment": json.dumps(enrichment_str, ensure_ascii=False),
                }
            )
            logging.info("   → score %s", final_score)
            time.sleep(0.5)

    pd.DataFrame(results).to_csv(dst, index=False)
    logging.info("Saved %d graded leads → %s", len(results), dst.absolute())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.warning("Interrupted by user – exiting.")
