#!/usr/bin/env python3
"""
Offline clone of your Make.com scenario, now writing to a CSV via pandas.

Flow:
1. GPT-4o spits out 40 Instagram-search queries.
2. For each query → Apify Google-Search actor → scrape results.
3. Extract handle & follower count, keep 100 < followers < 5 000.
4. Dump the deduped leads to leads-<timestamp>.csv in the current dir.
"""

import os, re, time, json, itertools
from datetime import datetime, timezone

import pandas as pd
import openai
from apify_client import ApifyClient
from dotenv import load_dotenv

# ─── Load env vars ────────────────────────────────────────────
load_dotenv(override=True)

OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
APIFY_API_TOKEN = os.environ["APIFY_API_TOKEN"]

openai.api_key = OPENAI_API_KEY
apify          = ApifyClient(APIFY_API_TOKEN)

# ─── Constants ───────────────────────────────────────────────
SYSTEM_PROMPT_TEMPLATE = """Your task is to create a list of lists of possible search terms to search on instagram for finding profiles of interest. For context, the terms you generate will be searched via google in a \n\nsite:instagram.com [your terms here]\n\ntype of manner. You can generate single search terms of multiple ones per run. You should generate *exactly* {n} different searches. Do not include the instagram filter but only the search terms. make the searches as diverse as possible but you do not need to make it too complex. One or two terms can be enough. Output the result in json format as a list of strings called search_list"""
CONTEXT_EXAMPLE = """We want to find profiles that have something to do with real estate and investment in the area of styria - Austria."""

# ─── 1. Generate 40 search queries with GPT-4o ───────────────
def generate_queries(context: str, n: int = 100) -> list[str]:
    """
    Generates a list of search queries using GPT-4o.
    """
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(n=n)

    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.8,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": context},
            ],
        )
        queries = json.loads(resp.choices[0].message.content)["search_list"][:n]
        return queries
    except Exception as e:
        print(f"❌ Error generating queries: {e}")
        return []

# ─── 2. Scrape Google results with Apify ─────────────────────
ACTOR_ID = "apify/google-search-scraper"

def google_scrape(term: str) -> list[dict]:
    """
    Scrapes Google search results using the Apify Google-Search actor.
    """
    query = (
        f'site:instagram.com {term} inurl:"/" '
        '-inurl:"/"*2 -inurl:/p/ -inurl:/reel/ -inurl:/tv/ '
        '-inurl:/stories/ -inurl:/explore/'
    )

    run_input = {
        "queries"        : query,
        "countryCode"    : "at",
        "resultsPerPage" : 100,
        "maxPagesPerQuery": 10,
        "saveHtml"       : False,
    }

    try:
        run = apify.actor(ACTOR_ID).call(run_input=run_input)  # waits for the actor to complete
        return list(apify.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        print(f"❌ Error scraping term '{term}': {e}")
        return []

# ─── 3. Extract & filter leads ───────────────────────────────
RE_NUM = re.compile(r"\D+")

def to_int(txt: str | None) -> int | None:
    """
    Converts a string to an integer, removing non-numeric characters.
    """
    if not txt: return None
    try:        return int(RE_NUM.sub("", txt))
    except ValueError:
        return None

def extract_leads(items: list[dict]) -> list[tuple[str, int]]:
    """
    Extracts and filters leads from the scraped data.
    Keeps only leads with follower counts between 100 and 5000.
    """
    extracted_leads = []
    for it in items:
        leads = it.get("organicResults")
        for lead in leads:
            if "followersAmount" not in lead:
                continue
            followers_string = lead["followersAmount"]
            followers_int = int(re.sub(r"[^\d]", "", followers_string))
            lead["followersAmount"] = followers_int
            if followers_int < 5000 and followers_int > 100:
                extracted_leads.append(lead)
    return extracted_leads

# ─── 4. Main glue code ───────────────────────────────────────
def main():
    """
    Main function to generate queries, scrape data, extract leads, and save to CSV.
    """
    context = CONTEXT_EXAMPLE

    # Generate search queries
    queries = generate_queries(context)
    print(f"🔍  {len(queries)} search terms ready")

    all_leads: list[dict] = []
    for q in queries:
        print(f"   • scraping “{q}”…")
        items  = google_scrape(q)  # Scrape results for each query
        all_leads.extend(extract_leads(items))  # Extract and filter leads
        time.sleep(1)   # Play nice with APIs

    # Save leads to CSV
    df = pd.DataFrame(all_leads)
    print(f"🔍  {len(df)} leads found")
    df = df.drop_duplicates(subset="channelName", keep="first")
    print(f"🔍  {len(df)} deduped leads")
    fname = f"leads-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    df.to_csv(fname, index=False)

    print(f"✅  {len(df)} leads saved to {fname}")

if __name__ == "__main__":
    main()
