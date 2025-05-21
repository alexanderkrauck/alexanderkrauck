# file: lead_enrichment.py
#!/usr/bin/env python3
"""
Lead Enrichment & Scoring (steroids) – v2.1
===========================================

Can be run as a CLI  *or*  imported as a library:

>>> from lead_enrichment import score_leads
>>> df = score_leads(
...     target_desc="Affluent Austrians eager to buy rentals in Styria",
...     target_examples="- Graz-based investor\n- Styrian Airbnb host",
...     use_perplexity=False,             # skip web enrichment
...     csv_in="raw_handles.csv",
... )
>>> print(df.head())
"""

from __future__ import annotations
###############################################################################
#                               STANDARD IMPORTS                              #
###############################################################################
import os, re, sys, time, json, textwrap, logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import openai
from apify_client import ApifyClient
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import yaml

###############################################################################
#                          LOGGING & ENVIRONMENT                              #
###############################################################################
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO
)

load_dotenv(override=True)            # reads .env if present

OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
APIFY_API_TOKEN = os.environ["APIFY_API_TOKEN"]
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

openai.api_key = OPENAI_API_KEY
apify          = ApifyClient(APIFY_API_TOKEN)

###############################################################################
#                    PROMPT BUILDERS  (no more globals!)                      #
###############################################################################
DEFAULT_CFG_PATH = Path(__file__).with_name("prompt_placeholders.yaml")
with DEFAULT_CFG_PATH.open(encoding="utf-8") as f:
    _CFG_DEFAULTS = yaml.safe_load(f)

def build_prompts(
    target_desc: str, target_examples: str
) -> Dict[str, str]:
    """Return all prompt strings param-substituted with *this* target description."""
    PROMPT_GRADE_SYSTEM = textwrap.dedent(f"""
        You are a lead-qualification expert.

        Give an **integer score 1-5** assessing whether an Instagram account fits
        the following target audience:

        {target_desc}

        Examples:
        {target_examples}

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

    PROMPT_PERPLEXITY = textwrap.dedent("""
        Find online profiles that probably belong to the same person/company/entity and
        could affect our judgment of income or real-estate interest.

        Return a **JSON list** where each object has:
          site          – domain / platform (linkedin, github, …)
          url           – full link
          probability   – 0-1 likelihood it's the same entity
          implication   – one short sentence (e.g. “C-level exec → high income”)

        Only return valid JSON. Nothing else.
    """).strip()

    PROMPT_SCORE_SYSTEM = textwrap.dedent(f"""
        Output **only** the improved integer score 1-5 (same scale as before)
        after considering both the Instagram argumentation and the enrichment JSON.
        Prefer higher scores when the enrichment increases confidence that the
        profile matches:

        {target_desc}
    """).strip()

    PROMPT_SCORE_USER = textwrap.dedent("""
        Instagram argumentation
        -----------------------
        {ig_reasoning}

        Enrichment
        ----------
        {enrichment_json}
    """).strip()

    return dict(
        GRADE_SYS=PROMPT_GRADE_SYSTEM,
        GRADE_USER=PROMPT_GRADE_USER,
        PERPLEXITY=PROMPT_PERPLEXITY,
        SCORE_SYS=PROMPT_SCORE_SYSTEM,
        SCORE_USER=PROMPT_SCORE_USER,
    )

###############################################################################
#                              REGEX & HELPERS                                #
###############################################################################
RE_SCORE = re.compile(r"score[^0-9]{0,10}(\d)", re.I | re.S)

def extract_score(text: str) -> Optional[int]:
    m = RE_SCORE.search(text or "")
    if m:
        try:
            n = int(m.group(1))
            return n if 1 <= n <= 5 else None
        except ValueError:
            pass
    return None

def batch(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

###############################################################################
#                         GPT & PERPLEXITY HELPERS                            #
###############################################################################
@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(openai.OpenAIError),
    reraise=True,
)
def gpt_chat(messages: List[Dict[str, Any]],
             model="gpt-4o-mini",
             temperature=0.7,
             max_tokens=1024) -> str:
    resp = openai.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=messages,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content

def query_perplexity(prompt: str, timeout=40) -> str:
    hdr = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers=hdr,
        json={
            "model": "sonar",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

###############################################################################
#                              CORE FUNCTION                                  #
###############################################################################
def score_leads(
    *,
    target_desc: str  = _CFG_DEFAULTS["TARGET_AUDIENCE_DESCRIPTION"],
    target_examples: str = _CFG_DEFAULTS["TARGET_AUDIENCE_EXAMPLES"],
    use_perplexity: bool = True,
    csv_in: str | Path = "leads.csv",
    csv_out: str | Path | None = None,
    max_handles: int | None = None,
    min_filter_score: int = 1,
) -> pd.DataFrame:
    """
    Parameters
    ----------
    target_desc        – short paragraph describing the exact audience
    target_examples    – bullet list / examples (optional but boosts GPT accuracy)
    use_perplexity     – False ⇢ skip the web-enrichment step entirely
    csv_in             – file with column 'channelName' holding the IG handles
    csv_out            – if given, write the resulting DataFrame to this CSV
    max_handles        – dev helper: limit how many handles to process
    min_filter_score   – only return handles with a score ≥ this value

    Returns
    -------
    pandas.DataFrame with columns:
        username, score, reasoning, enrichment
    """

    csv_in = Path(csv_in)
    if not csv_in.exists():
        raise FileNotFoundError(csv_in)

    prompts = build_prompts(target_desc, target_examples)

    # ─── read & validate handles ──────────────────────────────────────────────
    df_src = (
        pd.read_csv(csv_in, dtype=str)
          .fillna("")
          .assign(channelName=lambda d: d["channelName"].str.strip().str.lstrip("@"))
          .query("channelName != ''")
          .drop_duplicates("channelName")
    )
    if df_src.empty:
        raise ValueError("No usable 'channelName' entries in input CSV")

    handles = df_src["channelName"].tolist()
    if max_handles:
        handles = handles[:max_handles]

    logging.info("Processing %d Instagram handles …", len(handles))
    results: list[dict[str, Any]] = []

    # ─── iterate in Apify-friendly batches (≤100) ────────────────────────────
    for chunk in batch(handles, 100):
        try:
            run = apify.actor("apify/instagram-profile-scraper").call(
                run_input={"usernames": chunk},
                timeout_secs=1200,
            )
            profiles = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
        except Exception as e:
            logging.warning("Apify failed for %s… – skipping batch (%s)", chunk[:3], e)
            continue

        # ─── per profile: GPT grade → (optional) Perplexity → re-score ───────
        for prof in profiles:
            uname = prof.get("username", "unknown")
            logging.info("Scoring @%s", uname)

            # build GPT messages for the first grade
            captions = "\n".join(p.get("caption", "") for p in prof.get("latestPosts", []))
            user_msg  = prompts["GRADE_USER"].format(
                username=uname,
                full_name=prof.get("fullName", ""),
                location=prof.get("location",""),
                biography=prof.get("biography",""),
                external_urls=prof.get("externalUrls",""),
                captions=captions[:2000],
            )
            messages = [
                {"role":"system","content":prompts["GRADE_SYS"]},
                {"role":"user"  ,"content":user_msg},
            ]

            try:
                ig_text = gpt_chat(messages)
                base_score = extract_score(ig_text)
            except Exception as exc:
                logging.error("GPT grade failed for @%s – %s", uname, exc)
                ig_text, base_score = str(exc), None

            # optional Perplexity enrichment
            enrichment = ""
            if use_perplexity and PERPLEXITY_API_KEY:
                try:
                    enrich_prompt = (
                        f"Instagram username: {uname}\n\nIG reasoning:\n{ig_text}\n\n"
                        f"{prompts['PERPLEXITY']}"
                    )
                    enrichment = query_perplexity(enrich_prompt)
                except Exception as exc:
                    logging.warning("Perplexity failed for @%s – %s", uname, exc)

            # re-score if we got enrichment
            final_score = base_score
            if enrichment:
                try:
                    resc_messages = [
                        {"role":"system","content":prompts["SCORE_SYS"]},
                        {"role":"user","content":prompts["SCORE_USER"].format(
                            ig_reasoning=ig_text, enrichment_json=enrichment
                        )},
                    ]
                    final_text  = gpt_chat(resc_messages, temperature=0.2)
                    final_score = extract_score(final_text) or base_score
                except Exception as exc:
                    logging.warning("Re-score GPT failed for @%s – %s", uname, exc)

            results.append(dict(
                username   = uname,
                score      = final_score,
                reasoning  = ig_text,
                enrichment = enrichment,
            ))
            logging.info(" → @%-20s  score %s", uname, final_score)
            time.sleep(0.5)

    df_out = pd.DataFrame(results)
    #Filter out low scores
    df_out = df_out[df_out["score"].apply(lambda x: x is not None and x >= min_filter_score)]

    

    # write CSV if asked
    if csv_out:
        csv_out = Path(csv_out)
        df_out.to_csv(csv_out, index=False)
        logging.info("Saved results → %s", csv_out.resolve())

    return df_out

###############################################################################
#                               CLI FACADE                                    #
###############################################################################
if __name__ == "__main__":
    import argparse, pprint
    p = argparse.ArgumentParser(description="Lead scorer CLI")
    p.add_argument("--csv-in",  default="leads.csv")
    p.add_argument("--csv-out", help="Write results here")
    p.add_argument("--no-perp", action="store_true", help="Skip Perplexity step")
    p.add_argument("--max", type=int, help="Limit number of handles (dev)")
    args = p.parse_args()

    df = score_leads(
        csv_in=args.csv_in,
        csv_out=args.csv_out,
        use_perplexity=not args.no_perp,
        max_handles=args.max,
    )
    pprint.pp(df.head())
