#!/usr/bin/env python3
"""
ensembl.py

Flask backend for Ensembl annotation with UniProt & NCBI fallback support.
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
SLEEP_BETWEEN = 0.08
SLEEP_BETWEEN_UNIPROT = 0.1
SLEEP_BETWEEN_NCBI = 0.1
MAX_RETRIES = 5
MAX_IDS = 200
VERSION = "v2.0.0" # Final version
ENABLE_UNIPROT_FALLBACK = True
ENABLE_NCBI_FALLBACK = True

app = Flask(__name__, static_folder=None)
CORS(app)

# ----- HTTP with retry/backoff -----
def retry_get(url, params=None, headers=HEADERS, max_tries=MAX_RETRIES):
    backoff = 1.0
    for attempt in range(max_tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503):
                time.sleep(backoff)
                backoff *= 2
                continue
            return r
        except requests.RequestException:
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"Failed to GET {url} after {max_tries} attempts")

def retry_post(url, data=None, headers=HEADERS, json_body=False, max_tries=MAX_RETRIES):
    backoff = 1.0
    for attempt in range(max_tries):
        try:
            if json_body:
                r = requests.post(url, json=data, headers=headers, timeout=30)
            else:
                r = requests.post(url, data=data, headers=headers, timeout=30)
            if r.status_code in (200, 201):
                return r
            if r.status_code in (429, 503):
                time.sleep(backoff)
                backoff *= 2
                continue
            return r
        except requests.RequestException:
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"Failed to POST {url} after {max_tries} attempts")

# ----- Ensembl helpers -----
def get_gene_info(ensembl_id):
    """Return a dictionary with symbol, full name, and organism for a gene ID."""
    url = f"{ENSEMBL_REST}/lookup/id/{quote(ensembl_id)}"
    r = retry_get(url)
    if r is None or r.status_code != 200:
        return { "symbol": None, "full_name": None, "organism": None }
    try:
        j = r.json()
    except Exception:
        return { "symbol": None, "full_name": None, "organism": None }
    return {
        "symbol": j.get("display_name") or j.get("external_name"),
        "full_name": j.get("description"),
        "organism": j.get("species")
    }

def get_go_xrefs(ensembl_id):
    """Return list of (go_id, description) tuples from Ensembl xrefs for a gene."""
    url = f"{ENSEMBL_REST}/xrefs/id/{quote(ensembl_id)}?content-type=application/json"
    r = retry_get(url)
    if r is None or r.status_code != 200: return []
    try: items = r.json()
    except Exception: return []
    gos = []
    for item in items:
        # Restore robust check from original script
        dbname = (item.get("dbname") or "").upper()
        db_display = (item.get("db_display_name") or "").upper()
        if "GO" in dbname or "GO" in db_display:
            go_id = item.get("primary_id")
            if go_id:
                desc = item.get("description") or ""
                gos.append((str(go_id), str(desc)))
    return gos

# ----- UniProt helpers -----
def get_uniprot_id(ensembl_id=None, gene_symbol=None, organism=None):
    """Finds a UniProt ID. Prioritizes Ensembl ID, falls back to gene symbol."""
    if not ENABLE_UNIPROT_FALLBACK: return None
    
    # Method 1: Direct mapping from Ensembl ID
    if ensembl_id:
        try:
            url = f"{UNIPROT_REST}/uniprotkb/search"
            query = f'xref:ensembl AND "{ensembl_id}"'
            params = {"query": query, "format": "json", "size": 1}
            r = retry_get(url, params=params)
            if r and r.status_code == 200:
                results = r.json().get("results", [])
                if results: return results[0].get("primaryAccession")
        except Exception: pass

    # Method 2: Fallback to searching by gene symbol and organism
    if gene_symbol and organism:
        try:
            url = f"{UNIPROT_REST}/uniprotkb/search"
            query = f'(gene:"{gene_symbol}") AND (organism_name:"{organism}")'
            params = {"query": query, "format": "json", "size": 1}
            r = retry_get(url, params=params)
            if r and r.status_code == 200:
                results = r.json().get("results", [])
                if results: return results[0].get("primaryAccession")
        except Exception: pass

    return None

def get_gene_symbol_from_uniprot(uniprot_id):
    if not uniprot_id: return None
    url = f"{UNIPROT_REST}/uniprotkb/{uniprot_id}"
    try:
        r = retry_get(url, params={"fields": "gene_names"})
        if r and r.status_code == 200:
            genes = r.json().get("genes", [])
            if genes: return genes[0].get("geneName", {}).get("value")
    except Exception: pass
    return None

def get_go_terms_from_uniprot(uniprot_id):
    """Restored robust GO term parsing from original script's logic."""
    if not uniprot_id: return []
    url = f"{UNIPROT_REST}/uniprotkb/{uniprot_id}"
    try:
        r = retry_get(url)
        if r and r.status_code == 200:
            data = r.json()
            gos = []
            cross_refs = data.get("uniProtKBCrossReferences", []) or data.get("dbReferences", [])
            for xref in cross_refs:
                if (xref.get("database") or xref.get("type") or "").upper() == "GO":
                    go_id = xref.get("id")
                    desc = ""
                    props = xref.get("properties", [])
                    if props:
                        for prop in props:
                            if (prop.get("key") or "").lower() == "goterm":
                                desc = (prop.get("value") or "").split(":")[-1] # Extract term from "C:cytoplasm"
                                break
                    if go_id:
                        gos.append((go_id, desc))
            return gos
    except Exception: pass
    return []

# ----- NCBI Gene helpers -----
def get_ncbi_gene_id(ensembl_id=None):
    if not ENABLE_NCBI_FALLBACK or not ensembl_id: return None
    try:
        url = f"{NCBI_EUTILS}/esearch.fcgi"
        params = {"db": "gene", "term": ensembl_id, "retmode": "json", "retmax": 1}
        r = retry_get(url, params=params)
        if r and r.status_code == 200:
            id_list = r.json().get("esearchresult", {}).get("idlist", [])
            if id_list: return id_list[0]
    except Exception: pass
    return None

def get_gene_symbol_from_ncbi(ncbi_gene_id):
    if not ncbi_gene_id: return None
    try:
        url = f"{NCBI_EUTILS}/esummary.fcgi"
        params = {"db": "gene", "id": ncbi_gene_id, "retmode": "json"}
        r = retry_get(url, params=params)
        if r and r.status_code == 200:
            result = r.json().get("result", {}).get(str(ncbi_gene_id), {})
            return result.get("nomenclaturesymbol")
    except Exception: pass
    return None

# ----- small helpers -----
def merge_go_maps(*source_lists):
    ids, desc_map = set(), {}
    for lst in source_lists:
        for gid, desc in (lst or []):
            if not gid: continue
            gid = str(gid)
            ids.add(gid)
            if gid not in desc_map: desc_map[gid] = set()
            if desc: desc_map[gid].add(str(desc))
    return ids, desc_map

# ----- core processing -----
def annotate_ensembl_ids(id_list):
    ids = [str(x).strip() for x in id_list if x and str(x).strip()][:MAX_IDS]
    annotations = []
    
    for enid in ids:
        time.sleep(SLEEP_BETWEEN)
        en_info = get_gene_info(enid)
        en_gos = get_go_xrefs(enid)

        up_id = get_uniprot_id(enid, en_info.get("symbol"), en_info.get("organism"))
        up_symbol = get_gene_symbol_from_uniprot(up_id)
        up_gos = get_go_terms_from_uniprot(up_id)

        ncbi_id = get_ncbi_gene_id(enid)
        ncbi_symbol = get_gene_symbol_from_ncbi(ncbi_id)

        source_ensembl = {
            "symbol": en_info.get("symbol") or "",
            "full_name": en_info.get("full_name") or "",
            "organism": en_info.get("organism") or "",
            "go": en_gos
        }
        source_uniprot = {"id": up_id or "", "symbol": up_symbol or "", "go": up_gos}
        source_ncbi = {"id": ncbi_id or "", "symbol": ncbi_symbol or ""}

        merged_ids, merged_desc_map = merge_go_maps(en_gos, up_gos)
        merged_desc_str = {gid: "; ".join(sorted(list(descs))) for gid, descs in merged_desc_map.items()}

        annotations.append({
            "ensembl_id": enid,
            "sources": {"ensembl": source_ensembl, "uniprot": source_uniprot, "ncbi": source_ncbi},
            "merged": {"go_ids": sorted(list(merged_ids)), "go_descriptions": merged_desc_str}
        })

    return {"annotations": annotations}

# ----- HTTP endpoints -----
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

def _choose_preferred_symbol(sources):
    for key in ("ensembl", "uniprot", "ncbi"):
        if sources.get(key, {}).get("symbol"): return sources[key]["symbol"]
    return ""

def _build_compat_annotation(ann):
    compat = dict(ann)
    sources = ann.get("sources", {})
    merged = ann.get("merged", {})
    ensembl_source = sources.get("ensembl", {})
    
    compat["gene_symbol"] = _choose_preferred_symbol(sources)
    compat["full_name"] = ensembl_source.get("full_name", "")
    compat["organism"] = ensembl_source.get("organism", "")
    
    merged_ids = merged.get("go_ids", [])
    compat["go_ids"] = merged_ids
    desc_map = merged.get("go_descriptions", {})
    compat["go_terms"] = [desc_map.get(gid, "") for gid in merged_ids]
    return compat

@app.route("/annotate", methods=["POST", "GET"])
def annotate():
    ids = []
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        ids = data.get("ids", [])
        if not ids and ("id1" in data or "id2" in data):
            if data.get("id1"): ids.append(data["id1"])
            if data.get("id2"): ids.append(data["id2"])
    else: # GET
        if request.args.get("id1"): ids.append(request.args.get("id1"))
        if request.args.get("id2"): ids.append(request.args.get("id2"))
    
    if not ids:
        return jsonify({"error": "No Ensembl IDs provided."}), 400
    if len(ids) > MAX_IDS:
        return jsonify({"error": f"Too many IDs provided. Limit is {MAX_IDS}."}), 400

    try:
        result = annotate_ensembl_ids(ids)
        result["annotations"] = [_build_compat_annotation(ann) for ann in result.get("annotations", [])]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": "An error occurred during annotation.", "detail": str(e)}), 500

@app.route("/", defaults={"path": "index.html"})
@app.route("/<path:path>")
def serve_static(path):
    # Serve from the same directory as the script
    root_dir = os.path.abspath(os.path.dirname(__file__))
    return send_from_directory(root_dir, path)

if __name__ == "__main__":
    # Allow running from command line with a file of IDs
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        with open(sys.argv[1], "r") as f:
            ids = [line.strip() for line in f if line.strip()]
        print(json.dumps(annotate_ensembl_ids(ids), indent=2))
    else:
        # Default server mode
        print(f"Starting server version {VERSION} on https://dev-playground-8c4p.onrender.com")
        app.run(host="0.0.0.0", port=5000)
