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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from tqdm import tqdm  # added import for tqdm

###############################################################################
#                          LOGGING & ENVIRONMENT                              #
###############################################################################
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.handlers.clear()

file_handler = logging.FileHandler("lead_enrichment.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.ERROR)
console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

logger.addHandler(file_handler)
logger.addHandler(console_handler)


load_dotenv(override=True)            # reads .env if present

OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
APIFY_API_TOKEN = os.environ["APIFY_API_TOKEN"]
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

openai.api_key = OPENAI_API_KEY
apify          = ApifyClient(APIFY_API_TOKEN)
N_APIFY_PROFILES_AT_ONCE = 500
N_LLM_CALLS_AT_ONCE = 10

###############################################################################
#                    PROMPT BUILDERS  (no more globals!)                      #
###############################################################################
DEFAULT_CFG_PATH = Path(__file__).with_name("prompt_placeholders.yaml")
with DEFAULT_CFG_PATH.open(encoding="utf-8") as f:
    _CFG_DEFAULTS = yaml.safe_load(f)

def build_prompts(
    target_desc: str, target_examples: str, product_desc: str = "",
) -> Dict[str, str]:
    """Return all prompt strings param-substituted with *this* target description."""
    PROMPT_GRADE_SYSTEM_INSTAGRAM = textwrap.dedent(f"""
        You are a lead-qualification expert.

        Give an **integer score 1-5** assessing whether {'an instagram user'} fits
        the following target audience:

        {target_desc}

        Examples:
        {target_examples}

        FORMAT → exactly:

        [your concise argumentation]
        ##Score n
    """).strip()

    PROMPT_GRADE_USER_INSTAGRAM = textwrap.dedent("""
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

    PROMPT_PERPLEXITY = textwrap.dedent(f"""
        You are a lead-qualification expert. The goal is to determine by doing a broader web search if a user with the given Instagram profile
        fits the target audience. The target audience is:
        {target_desc}
        
        # Instagram profile
        The Instagram profile is:
        {{ig_profile}}

        # Output
        Return a table where each site entry has:
          site          – domain / platform (linkedin, github, …)
          url           – full link
          probability   – 0-1 likelihood it's the same entity
          implication   – What that implies w.r.t. the target audience
        
        In the end return why it would be reasonable that the entity would buy specifically this property and in particular why it is reasonable that this person would react to a cold email proposing this property. be critical.
        Finally if possible try to find a contact email of this person but do not include candidate emails that are only a plausible guess.
        
        # Information about the thing the target audience is looking for
        {product_desc}        
    """).strip()

    PROMPT_GRADE_SYSTEM_FINAL = textwrap.dedent(f"""
        You are a lead-qualification expert.

        Give an **integer score 1-5** assessing whether {'a user that you obtain an intagram profile, a previous assessment of the profile and an extended profile via websearch of'} fits
        the following target audience:

        {target_desc}

        Examples:
        {target_examples}

        FORMAT → exactly:

        [your concise argumentation]
        ##Score n
    """).strip()

    PROMPT_GRADE_USER_FINAL = textwrap.dedent("""
        Instagram profile
        -----------------------
        {ig_profile}
        
        Instagram reasoning
        -----------------------
        {ig_reasoning}

        Enrichment from other sources (internet)
        -----------------------
        {enrichment}
    """).strip()

    return dict(
        GRADE_SYS=PROMPT_GRADE_SYSTEM_INSTAGRAM,
        GRADE_USER=PROMPT_GRADE_USER_INSTAGRAM,
        PERPLEXITY=PROMPT_PERPLEXITY,
        SCORE_SYS=PROMPT_GRADE_SYSTEM_FINAL,
        SCORE_USER=PROMPT_GRADE_USER_FINAL,
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
             model="gpt-4o",
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

def _process_profile(
    prof: dict[str, Any],
    *,
    prompts: dict[str, str],
    use_perplexity: bool,
) -> dict[str, Any]:
    """
    Runs **inside a worker thread**.
    Does:  profile → GPT grade → (opt) Perplexity → GPT re-score
    Returns one results-dict that is identical to what you were appending before.
    """
    uname = prof.get("username", "unknown")
    captions = "\n".join(p.get("caption", "") for p in prof.get("latestPosts", []))

    # 1) first grade -----------------------------------------------
    user_msg = prompts["GRADE_USER"].format(
        username=uname,
        full_name=prof.get("fullName", ""),
        location=prof.get("location", ""),
        biography=prof.get("biography", ""),
        external_urls=prof.get("externalUrls", ""),
        captions=captions[:2000],
    )
    ig_text   = ""
    base_score = None
    
    enrich_prompt = None
    base_prompt = None
    final_prompt = None
    
    try:
        messages = [
                {"role": "system", "content": prompts["GRADE_SYS"]},
                {"role": "user",   "content": user_msg},
            ]
        ig_text = gpt_chat(
            messages
        )
        base_prompt = str(messages)
        base_score = extract_score(ig_text)
    except Exception as exc:
        logger.error("GPT grade failed for @%s – %s", uname, exc)
        ig_text = str(exc)

    # 2) optional Perplexity enrichment ---------------------------
    enrichment = ""
    if use_perplexity and PERPLEXITY_API_KEY:
        try:
            enrich_prompt = (
                f"Instagram username: {uname}\n\nIG reasoning:\n{ig_text}\n\n"
                f"{prompts['PERPLEXITY'].format(ig_profile=user_msg)}"
            )
            enrichment = query_perplexity(enrich_prompt)
        except Exception as exc:
            logger.warning("Perplexity failed for @%s – %s", uname, exc)

    # 3) re-score if enrichment exists ----------------------------
    final_score = base_score
    if enrichment:
        try:
            messages = [
                    {"role": "system", "content": prompts["SCORE_SYS"]},
                    {
                        "role": "user",
                        "content": prompts["SCORE_USER"].format(
                            ig_reasoning=ig_text, enrichment=enrichment, ig_profile=user_msg
                        ),
                    },
                ]
            final_prompt = str(messages)
            final_text = gpt_chat(
                messages,
                temperature=0.2,
            )
            final_score = extract_score(final_text) or base_score
        except Exception as exc:
            logging.warning("Re-score GPT failed for @%s – %s", uname, exc)

    logger.info(" → @%-20s  score %s", uname, final_score)
    return dict(
        username   = uname,
        score      = final_score,
        reasoning  = ig_text,
        enrichment = enrichment,
        biography  = prof.get("biography", ""),
        location   = prof.get("location", ""),
        external_urls = prof.get("externalUrls", ""),
        full_name  = prof.get("fullName", ""),
        captions   = captions,
        latestPosts = prof.get("latestPosts", []),
        createdAt  = datetime.now(timezone.utc).isoformat(),
        updatedAt  = datetime.now(timezone.utc).isoformat(),
        enrichmentCreatedAt = datetime.now(timezone.utc).isoformat(),
        enrichmentUpdatedAt = datetime.now(timezone.utc).isoformat(),
        enrich_prompt = enrich_prompt,
        base_prompt = base_prompt,
        final_prompt = final_prompt,
        final_reasoning = final_text if final_text else "",
    )

###############################################################################
#                              CORE FUNCTION                                  #
###############################################################################
def score_leads(
    *,
    target_desc: str  = _CFG_DEFAULTS["TARGET_AUDIENCE_DESCRIPTION"],
    target_examples: str = _CFG_DEFAULTS["TARGET_AUDIENCE_EXAMPLES"],
    product_desc: str = "",
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

    prompts = build_prompts(target_desc, target_examples, product_desc="")

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
    logger.info("Processing %d Instagram handles …", len(handles))
    results: list[dict[str, Any]] = []
    
    pbar = tqdm(total=len(handles), desc="Processing leads")  # overall progress bar
    
    for chunk in batch(handles, N_APIFY_PROFILES_AT_ONCE):
        try:
            run = apify.actor("apify/instagram-profile-scraper").call(
                run_input={"usernames": chunk},
                timeout_secs=1200,
            )
            profiles = list(apify.dataset(run["defaultDatasetId"]).iterate_items())
            # Filter out profiles with empty or None 'biography' (description)
            profiles = [
                prof for prof in profiles
                if prof.get("biography") not in ("", None)
            ]
        except Exception as e:
            logger.warning("Apify failed for %s… – skipping batch (%s)", chunk[:3], e)
            pbar.update(len(chunk))
            continue

        with ThreadPoolExecutor(max_workers=N_LLM_CALLS_AT_ONCE) as pool:
            future_to_uname = {
                pool.submit(
                    _process_profile,
                    prof,
                    prompts=prompts,
                    use_perplexity=use_perplexity,
                ): prof.get("username", "unknown")
                for prof in profiles
            }
            for fut in as_completed(future_to_uname):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.error("Worker failed – %s", exc)
                pbar.update(1)
    pbar.close()
    df_out = pd.DataFrame(results)
    #Filter out low scores
    df_out = df_out[df_out["score"].apply(lambda x: x is not None and x >= min_filter_score)]

    

    # write CSV if asked
    if csv_out:
        csv_out = Path(csv_out)
        df_out.to_csv(csv_out, index=False)
        logger.info("Saved results → %s", csv_out.resolve())

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
