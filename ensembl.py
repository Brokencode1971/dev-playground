#!/usr/bin/env python3
"""
ensembl.py

Flask backend for Ensembl annotation with UniProt & NCBI fallback support.

Serves:
  - GET  /health
  - GET  /version
  - GET  /config
  - POST /annotate
  - GET  /annotate
  - GET  /           -> serves index.html from project root
  - GET  /index.html -> serves index.html
"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests, time, sys, os, json
from urllib.parse import quote
from datetime import datetime

# ----- config -----
ENSEMBL_REST = "https://rest.ensembl.org"
UNIPROT_REST = "https://rest.uniprot.org"
NCBI_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
HEADERS = {"Accept": "application/json"}
SLEEP_BETWEEN = 0.08  # polite delay between Ensembl calls
SLEEP_BETWEEN_UNIPROT = 0.1  # polite delay between UniProt calls
SLEEP_BETWEEN_NCBI = 0.1  # polite delay between NCBI calls
MAX_RETRIES = 5
MAX_IDS = 200  # safety limit
VERSION = "v1.0.0"
ENABLE_UNIPROT_FALLBACK = True  # Enable/disable UniProt fallback
ENABLE_NCBI_FALLBACK = True  # Enable/disable NCBI fallback

app = Flask(__name__, static_folder=None)
CORS(app)  # allow cross-origin (safe for local dev)

# ----- HTTP with retry/backoff -----
def retry_get(url, params=None, headers=HEADERS, max_tries=MAX_RETRIES):
    backoff = 1.0
    for attempt in range(max_tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                return r
            # rate-limited or service unavailable -> backoff and retry
            if r.status_code in (429, 503):
                time.sleep(backoff)
                backoff *= 2
                continue
            # other non-200 -> return response for caller to inspect
            return r
        except requests.RequestException:
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"Failed to GET {url} after {max_tries} attempts")

def retry_post(url, data=None, headers=HEADERS, json_body=False, max_tries=MAX_RETRIES):
    """
    retry_post supports sending either form/raw data (data param) or JSON body (if json_body=True).
    """
    backoff = 1.0
    for attempt in range(max_tries):
        try:
            if json_body:
                r = requests.post(url, json=data, headers=headers, timeout=30)
            else:
                r = requests.post(url, data=data, headers=headers, timeout=30)
            if r.status_code in (200, 201):
                return r
            # rate-limited or service unavailable -> backoff and retry
            if r.status_code in (429, 503):
                time.sleep(backoff)
                backoff *= 2
                continue
            # other non-200 -> return response for caller to inspect
            return r
        except requests.RequestException:
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"Failed to POST {url} after {max_tries} attempts")

# ----- Ensembl helpers -----
def get_gene_symbol(ensembl_id):
    """Return the display_name (gene symbol) for a gene Ensembl ID or None."""
    url = f"{ENSEMBL_REST}/lookup/id/{quote(ensembl_id)}"
    r = retry_get(url)
    if r is None or r.status_code != 200:
        return None
    try:
        j = r.json()
    except Exception:
        return None
    return j.get("display_name") or j.get("external_name") or None

def get_go_xrefs(ensembl_id):
    """Return list of (go_id, description) tuples from Ensembl xrefs for a gene."""
    url = f"{ENSEMBL_REST}/xrefs/id/{quote(ensembl_id)}"
    r = retry_get(url)
    if r is None or r.status_code != 200:
        return []
    try:
        items = r.json()
    except Exception:
        return []
    gos = []
    for it in items:
        dbname = (it.get("dbname") or "").upper()
        db_display = (it.get("db_display_name") or "").upper()
        if "GO" in dbname or "GO" in db_display or dbname == "GENE_ONTOLOGY":
            go_id = it.get("primary_id") or it.get("id") or it.get("display_id")
            if go_id:
                go_id = str(go_id).strip()
                desc = it.get("description") or it.get("display_id") or ""
                gos.append((go_id, desc))
    return gos

# ----- UniProt helpers -----
def _uniprot_poll_job(job_id, timeout=15.0):
    """
    Poll UniProt idmapping job status until finished or timeout (seconds).
    Return True if finished successfully, False otherwise.
    """
    status_url = f"{UNIPROT_REST}/idmapping/status/{job_id}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = retry_get(status_url)
            if not r or r.status_code != 200:
                time.sleep(SLEEP_BETWEEN_UNIPROT)
                continue
            j = r.json()
            # different key names across versions: try common ones
            status_val = j.get("jobStatus") or j.get("status") or j.get("job_status")
            if isinstance(status_val, str):
                status_val = status_val.lower()
            if status_val in ("finished", "complete", "success"):
                return True
            if status_val in ("failed", "error"):
                return False
        except Exception:
            pass
        time.sleep(SLEEP_BETWEEN_UNIPROT)
    return False

def _uniprot_get_mapping_results(job_id, size=10):
    """
    Get mapping results for job_id. Return a list of result dicts (may be empty).
    """
    results_url = f"{UNIPROT_REST}/idmapping/results/{job_id}"
    try:
        r = retry_get(results_url, params={"format": "json", "size": size})
        if r and r.status_code == 200:
            j = r.json()
            # results can appear under different keys; try common patterns
            for key in ("results", "mappedResults", "data", "records"):
                if key in j and isinstance(j[key], list):
                    return j[key]
            # fallback: entire payload may be a list
            if isinstance(j, list):
                return j
    except Exception:
        pass
    return []

def get_uniprot_id_from_ensembl(ensembl_id):
    """
    Map Ensembl gene ID to UniProt ID using UniProt idmapping endpoint (preferred),
    with a fallback to search queries if idmapping fails.

    Returns a single UniProt accession (string) or None.
    """
    if not ENABLE_UNIPROT_FALLBACK or not ensembl_id:
        return None

    # First try the idmapping API (preferred — more reliable)
    try:
        run_url = f"{UNIPROT_REST}/idmapping/run"
        payload = {"from": "Ensembl", "to": "UniProtKB", "ids": ensembl_id}
        # send JSON body
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        r = retry_post(run_url, data=payload, headers=headers, json_body=True)
        if r and r.status_code in (200, 201):
            j = r.json()
            # job id can be under different keys depending on UniProt server version
            job_id = j.get("jobId") or j.get("job_id") or j.get("id") or j.get("jobId")
            if job_id:
                # poll job until finished (small timeout)
                finished = _uniprot_poll_job(job_id, timeout=12.0)
                if finished:
                    results = _uniprot_get_mapping_results(job_id, size=5)
                    if results:
                        # try several common shapes in returned items
                        item = results[0]
                        # many result items contain 'to' or 'to' / 'primaryAccession' / 'id'
                        candidate = item.get("to") or item.get("primaryAccession") or item.get("id") or item.get("to")
                        if candidate:
                            return candidate
    except Exception:
        # swallow and fall back to search below
        pass

    # If idmapping didn't work, fall back to the older search heuristics (kept for compatibility)
    search_queries = [
        f"xref:Ensembl:{ensembl_id}",
        f"xref:ensembl-{ensembl_id}",
        f"database:ensembl AND {ensembl_id}",
        f"gene:{ensembl_id}"
    ]

    for query in search_queries:
        try:
            url = f"{UNIPROT_REST}/uniprotkb/search"
            params = {"query": query, "format": "json", "size": 1}
            time.sleep(SLEEP_BETWEEN_UNIPROT)
            r = retry_get(url, params=params)
            if r and r.status_code == 200:
                data = r.json()
                results = data.get("results", []) or data.get("entries", [])
                if results:
                    # try multiple possible keys for accession
                    accession = results[0].get("primaryAccession") or results[0].get("accession") or results[0].get("id")
                    if accession:
                        return accession
        except Exception:
            continue

    return None

def get_gene_symbol_from_uniprot(uniprot_id):
    """Get gene symbol from UniProt using UniProt ID."""
    if not ENABLE_UNIPROT_FALLBACK or not uniprot_id:
        return None

    url = f"{UNIPROT_REST}/uniprotkb/{uniprot_id}"
    params = {"fields": "genes"}

    try:
        time.sleep(SLEEP_BETWEEN_UNIPROT)
        r = retry_get(url, params=params)
        if r and r.status_code == 200:
            data = r.json()
            genes = data.get("genes", [])
            if genes:
                gene = genes[0]
                gene_name = gene.get("geneName", {}).get("value")
                if gene_name:
                    return gene_name
    except Exception:
        pass
    return None

def get_go_terms_from_uniprot(uniprot_id):
    """Get GO terms from UniProt using UniProt ID."""
    if not ENABLE_UNIPROT_FALLBACK or not uniprot_id:
        return []

    try:
        url = f"{UNIPROT_REST}/uniprotkb/{uniprot_id}"
        time.sleep(SLEEP_BETWEEN_UNIPROT)
        r = retry_get(url)
        if r and r.status_code == 200:
            data = r.json()
            gos = []
            cross_refs = data.get("uniProtKBCrossReferences", []) or data.get("dbReferences", [])
            for xref in cross_refs:
                # different shapes: "database" or "type" keys
                dbname = xref.get("database") or xref.get("type") or ""
                if str(dbname).upper() == "GO":
                    go_id = xref.get("id") or xref.get("properties", {}).get("GO") or None
                    if not go_id:
                        # older shape: properties list of dicts
                        props = xref.get("properties", []) or []
                        for p in props:
                            if p.get("key") in ("GoTerm", "term", "name"):
                                # skip: this is description, not id
                                pass
                        go_id = xref.get("id")
                    if go_id and str(go_id).upper().startswith("GO:"):
                        description = ""
                        properties = xref.get("properties", []) or []
                        # properties may be list of dicts {key,value}
                        for prop in properties:
                            if prop.get("key") == "GoTerm" or prop.get("key") == "term":
                                description = prop.get("value", "")
                                break
                        # for some shapes description may be inside 'properties' as dict
                        if not description and isinstance(xref.get("properties"), dict):
                            description = xref["properties"].get("GoTerm") or xref["properties"].get("term", "")
                        gos.append((str(go_id).strip(), description or ""))
            return gos
    except Exception:
        pass

    return []

# ----- NCBI Gene helpers -----
def get_ncbi_gene_id_from_ensembl(ensembl_id):
    """Map Ensembl gene ID to NCBI Gene ID using NCBI E-utilities."""
    if not ENABLE_NCBI_FALLBACK:
        return None

    try:
        search_url = f"{NCBI_EUTILS}/esearch.fcgi"
        params = {
            "db": "gene",
            "term": f"{ensembl_id}[Ensembl]",
            "retmode": "json",
            "retmax": 1
        }

        time.sleep(SLEEP_BETWEEN_NCBI)
        r = retry_get(search_url, params=params)
        if r and r.status_code == 200:
            data = r.json()
            id_list = data.get("esearchresult", {}).get("idlist", [])
            if id_list:
                return id_list[0]
    except Exception:
        pass
    return None

def get_gene_symbol_from_ncbi(ncbi_gene_id):
    """Get gene symbol from NCBI Gene using NCBI Gene ID."""
    if not ENABLE_NCBI_FALLBACK or not ncbi_gene_id:
        return None

    try:
        summary_url = f"{NCBI_EUTILS}/esummary.fcgi"
        params = {
            "db": "gene",
            "id": ncbi_gene_id,
            "retmode": "json"
        }

        time.sleep(SLEEP_BETWEEN_NCBI)
        r = retry_get(summary_url, params=params)
        if r and r.status_code == 200:
            data = r.json()
            result = data.get("result", {}).get(ncbi_gene_id, {})
            return result.get("nomenclature_symbol") or result.get("name")
    except Exception:
        pass
    return None

def get_go_terms_from_ncbi(ncbi_gene_id):
    """Get GO terms from NCBI Gene using NCBI Gene ID."""
    if not ENABLE_NCBI_FALLBACK or not ncbi_gene_id:
        return []

    try:
        summary_url = f"{NCBI_EUTILS}/esummary.fcgi"
        params = {
            "db": "gene",
            "id": ncbi_gene_id,
            "retmode": "json"
        }

        time.sleep(SLEEP_BETWEEN_NCBI)
        r = retry_get(summary_url, params=params)
        if r and r.status_code == 200:
            data = r.json()
            result = data.get("result", {}).get(ncbi_gene_id, {})
            go_terms = []
            for field in ["go_component", "go_function", "go_process"]:
                if field in result:
                    terms = result[field]
                    if isinstance(terms, list):
                        for term in terms:
                            if isinstance(term, dict) and "value" in term:
                                go_id = term["value"]
                                description = term.get("label", "")
                                if str(go_id).startswith("GO:"):
                                    go_terms.append((go_id, description))
            return go_terms
    except Exception:
        pass
    return []

# small helpers for merging and formatting
def _uniq_sorted(seq):
    return sorted(list(dict.fromkeys(seq)))

def merge_go_maps(*source_lists):
    """
    Accept multiple lists of (go_id, desc) tuples and return:
      - set of unique GO IDs
      - dict go_id -> set(descriptions)
    """
    ids = set()
    desc_map = {}
    for lst in source_lists:
        if not lst:
            continue
        for gid, desc in lst:
            if not gid:
                continue
            gid_up = str(gid).upper()
            ids.add(gid_up)
            if gid_up not in desc_map:
                desc_map[gid_up] = set()
            if desc and isinstance(desc, str) and desc.strip():
                desc_map[gid_up].add(desc.strip())
    return ids, desc_map

# ----- core processing (no file I/O) -----
def annotate_ensembl_ids(id_list):
    """
    Accept list of Ensembl IDs and return a dict that preserves per-source data:
      {
        "annotations": [
           {
             "ensembl_id": "...",
             "sources": {
                "ensembl": {"symbol": "...", "go": [ [id,desc], ... ]},
                "uniprot":  {"id": "...", "symbol": "...", "go": [...]},
                "ncbi":     {"id": "...", "symbol": "...", "go": [...]}
             },
             "merged": { "go_ids": [...], "go_descriptions": {goid: "desc1; desc2"} }
           },
           ...
        ],
        "meta": {...}
      }

    This function ALWAYS queries Ensembl, then UniProt (if enabled), then NCBI (if enabled),
    and preserves each source's returned data for frontend comparison.
    """
    ids = [str(x).strip() for x in id_list if x and str(x).strip()]
    ids = ids[:MAX_IDS]  # truncate for safety

    annotations = []
    gene_symbols_seen = set()
    all_go_ids_global = set()

    # track usage counts
    uniprot_fetch_count = 0
    ncbi_fetch_count = 0

    for enid in ids:
        # Ensembl first
        time.sleep(SLEEP_BETWEEN)
        en_symbol = None
        try:
            en_symbol = get_gene_symbol(enid)
        except Exception:
            en_symbol = None

        time.sleep(SLEEP_BETWEEN)
        en_gos = []
        try:
            en_gos = get_go_xrefs(enid) or []
        except Exception:
            en_gos = []

        # UniProt (always attempt mapping/fetch if enabled)
        up_id = None
        up_symbol = None
        up_gos = []
        if ENABLE_UNIPROT_FALLBACK:
            try:
                up_id = get_uniprot_id_from_ensembl(enid)
            except Exception:
                up_id = None

            if up_id:
                uniprot_fetch_count += 1
                time.sleep(SLEEP_BETWEEN_UNIPROT)
                try:
                    up_symbol = get_gene_symbol_from_uniprot(up_id)
                except Exception:
                    up_symbol = None
                time.sleep(SLEEP_BETWEEN_UNIPROT)
                try:
                    up_gos = get_go_terms_from_uniprot(up_id) or []
                except Exception:
                    up_gos = []

        # NCBI (always attempt mapping/fetch if enabled)
        ncbi_id = None
        ncbi_symbol = None
        ncbi_gos = []
        if ENABLE_NCBI_FALLBACK:
            try:
                ncbi_id = get_ncbi_gene_id_from_ensembl(enid)
            except Exception:
                ncbi_id = None

            if ncbi_id:
                ncbi_fetch_count += 1
                time.sleep(SLEEP_BETWEEN_NCBI)
                try:
                    ncbi_symbol = get_gene_symbol_from_ncbi(ncbi_id)
                except Exception:
                    ncbi_symbol = None
                time.sleep(SLEEP_BETWEEN_NCBI)
                try:
                    ncbi_gos = get_go_terms_from_ncbi(ncbi_id) or []
                except Exception:
                    ncbi_gos = []

        # build per-source structures (preserve raw tuples)
        source_ensembl = {
            "symbol": en_symbol or "",
            "go": [(str(gid).upper(), desc or "") for gid, desc in (en_gos or [])]
        }
        source_uniprot = {
            "id": up_id or "",
            "symbol": up_symbol or "",
            "go": [(str(gid).upper(), desc or "") for gid, desc in (up_gos or [])]
        }
        source_ncbi = {
            "id": ncbi_id or "",
            "symbol": ncbi_symbol or "",
            "go": [(str(gid).upper(), desc or "") for gid, desc in (ncbi_gos or [])]
        }

        # merge GO ids and descriptions across sources
        merged_ids, merged_desc_map = merge_go_maps(source_ensembl["go"], source_uniprot["go"], source_ncbi["go"])
        # add to global set
        all_go_ids_global.update(merged_ids)

        # build merged description strings (join multiple descriptions with '; ')
        merged_desc_str = {gid: "; ".join(sorted(merged_desc_map.get(gid, []))) for gid in merged_desc_map}

        # collect all symbols seen for global list
        for s in (en_symbol, up_symbol, ncbi_symbol):
            if s and isinstance(s, str) and s.strip():
                gene_symbols_seen.add(s.strip())

        annotation = {
            "ensembl_id": enid,
            "sources": {
                "ensembl": source_ensembl,
                "uniprot": source_uniprot,
                "ncbi": source_ncbi
            },
            "merged": {
                "go_ids": sorted(merged_ids),
                "go_descriptions": merged_desc_str
            }
        }

        annotations.append(annotation)

    # build meta
    result = {
        "annotations": annotations,
        "gene_symbols": sorted(gene_symbols_seen),
        "go_ids": sorted(all_go_ids_global),
        "meta": {
            "version": VERSION,
            "count_input": len(id_list),
            "count_processed": len(ids),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "uniprot": {
                "enabled": ENABLE_UNIPROT_FALLBACK,
                "fetch_count": uniprot_fetch_count
            },
            "ncbi": {
                "enabled": ENABLE_NCBI_FALLBACK,
                "fetch_count": ncbi_fetch_count
            }
        }
    }

    return result

# End of first half (endpoints and CLI runner will come in second half)
# ----- HTTP endpoints (second half) -----
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat() + "Z"})


@app.route("/version", methods=["GET"])
def version():
    return jsonify({"version": VERSION, "started": datetime.utcnow().isoformat() + "Z"})


@app.route("/config", methods=["GET"])
def config():
    return jsonify({
        "uniprot_fallback_enabled": ENABLE_UNIPROT_FALLBACK,
        "ncbi_fallback_enabled": ENABLE_NCBI_FALLBACK,
        "max_ids": MAX_IDS,
        "ensembl_rest_url": ENSEMBL_REST,
        "uniprot_rest_url": UNIPROT_REST,
        "version": VERSION
    })


def _choose_preferred_symbol(sources):
    """Prefer Ensembl symbol, then UniProt, then NCBI; fall back to empty string."""
    for key in ("ensembl", "uniprot", "ncbi"):
        val = sources.get(key, {}).get("symbol")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _build_compat_annotation(ann):
    """
    Convert the new per-source annotation into a compatibility shape the frontend expects.
    Keeps the detailed `sources` and `merged` objects, and adds:
      - gene_symbol (best available)
      - go_ids (merged list)
      - go_terms (parallel list of descriptions for each go_id)
    """
    compat = dict(ann)  # shallow copy
    sources = ann.get("sources", {})
    merged = ann.get("merged", {})

    # preferred single symbol (for legacy frontend)
    gene_symbol = _choose_preferred_symbol(sources)
    compat["gene_symbol"] = gene_symbol

    # go_ids: use merged go_ids (already sorted in annotate_ensembl_ids)
    merged_ids = merged.get("go_ids", []) or []
    compat["go_ids"] = merged_ids

    # go_terms: build parallel array (may be empty strings)
    desc_map = merged.get("go_descriptions", {}) or {}
    go_terms = [desc_map.get(gid, "") for gid in merged_ids]
    compat["go_terms"] = go_terms

    return compat


@app.route("/annotate", methods=["POST", "GET"])
def annotate():
    """
    Accepts:
      - POST JSON: { "ids": ["ENSG...","ENSG..."] }  OR  { "id1":"...", "id2":"..." }
      - GET    : /annotate?id1=...&id2=...
    Returns JSON with per-source data plus compatibility fields for the frontend.
    """
    ids = []
    if request.method == "POST":
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            data = {}
        if isinstance(data, dict):
            if "ids" in data and isinstance(data["ids"], list):
                ids = data["ids"]
            else:
                id1 = data.get("id1") or data.get("ensembl1")
                id2 = data.get("id2") or data.get("ensembl2")
                if id1:
                    ids.append(id1)
                if id2:
                    ids.append(id2)

    # GET fallback
    if not ids:
        id1 = request.args.get("id1") or request.args.get("ensembl1")
        id2 = request.args.get("id2") or request.args.get("ensembl2")
        if id1:
            ids.append(id1)
        if id2:
            ids.append(id2)

    if not ids:
        return jsonify({"error": "No Ensembl IDs provided. Provide JSON {'ids': [...] } or id1/id2 params."}), 400

    if len(ids) > MAX_IDS:
        return jsonify({"error": f"Too many IDs (limit {MAX_IDS}). For large jobs use batch mode."}), 400

    try:
        result = annotate_ensembl_ids(ids)
    except Exception as e:
        return jsonify({"error": "Annotation failed", "detail": str(e)}), 500

    # Build compatibility view expected by the frontend:
    # - keep `annotations` but add top-level gene_symbol, go_ids, go_terms to each annotation
    compat_annotations = []
    for ann in result.get("annotations", []):
        compat_annotations.append(_build_compat_annotation(ann))

    compat_result = dict(result)  # shallow copy of meta/gene_symbols/go_ids
    compat_result["annotations"] = compat_annotations

    return jsonify(compat_result)


# Serve index.html from project root
@app.route("/", methods=["GET"])
def home():
    root = os.path.abspath(os.path.dirname(__file__) or ".")
    index_path = os.path.join(root, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(root, "index.html")
    return jsonify({"error": "index.html not found"}), 404


@app.route("/index.html", methods=["GET"])
def index_html():
    return home()


# ----- CLI behavior: process file or run server -----
if __name__ == "__main__":
    # legacy CLI mode: python ensembl.py some_ids.txt
    if len(sys.argv) == 2 and os.path.exists(sys.argv[1]):
        path = sys.argv[1]
        with open(path, "r", encoding="utf-8") as fh:
            ids = [line.strip() for line in fh if line.strip()]
        print(json.dumps(annotate_ensembl_ids(ids), indent=2))
        sys.exit(0)

    # normal server mode
    print(f"Starting Ensembl annotation server (no file output) on http://127.0.0.1:5000 — version {VERSION}")
    # debug=False by default; use an auto-reload dev loop externally if you want hot reload
    app.run(host="0.0.0.0", port=5000, debug=False)
