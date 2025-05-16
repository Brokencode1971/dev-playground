from flask import Flask, render_template, request, jsonify
import requests

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search", methods=["POST"])
def search():
    protein_id = request.form.get("query")
    if not protein_id:
        return jsonify({"error": "No ID provided."}), 400

    # Fetch UniProt JSON
    url = f"https://rest.uniprot.org/uniprotkb/{protein_id}.json"
    response = requests.get(url)
    if response.status_code != 200:
        return jsonify({"error": "Protein not found."}), 404

    data = response.json()
    name = data["proteinDescription"]["recommendedName"]["fullName"]["value"]
    organism = data["organism"]["scientificName"]
    sequence = data["sequence"]["value"]

    # PDB IDs
    pdb_ids = [xref["id"] for xref in data.get("uniProtKBCrossReferences", []) if xref.get("database") == "PDB"]

    # Raw PDB text
    pdb_text = None
    if pdb_ids:
        pdb_url = f"https://files.rcsb.org/download/{pdb_ids[0]}.pdb"
        resp = requests.get(pdb_url)
        if resp.status_code == 200:
            pdb_text = resp.text

    # External cross-links
    url_templates = {
        "Ensembl":    "https://www.ensembl.org/Homo_sapiens/Gene/Summary?g={id}",
        "KEGG":       "https://www.genome.jp/dbget-bin/www_bget?hsa:{id}",
        "Reactome":   "https://reactome.org/PathwayBrowser/#/{id}",
        "GeneID":     "https://www.ncbi.nlm.nih.gov/gene/{id}",
        "Pfam":       "https://pfam.xfam.org/protein/{id}",
        "InterPro":   "https://www.ebi.ac.uk/interpro/entry/UniProt/{protein_id}"    
    }
    cross_links = []
    for xref in data.get("uniProtKBCrossReferences", []):
        db = xref.get("database")
        _id = xref.get("id")
        if db in url_templates:
            cross_links.append({
                "db": db,
                "id": _id,
                "url": url_templates[db].format(id=_id, protein_id=protein_id)
            })

    return jsonify({
        "protein_name": name,
        "organism":      organism,
        "sequence":      sequence,
        "pdb_ids":       pdb_ids,
        "pdb_text":      pdb_text,
        "cross_links":   cross_links
    })

@app.route("/autocomplete")
def autocomplete():
    query = request.args.get("q") or ""
    url = (
        "https://rest.uniprot.org/uniprotkb/search"
        f"?query={query}"
        "&fields=accession,protein_name"
        "&format=json"
        "&size=5"
    )
    resp = requests.get(url)
    data = resp.json() if resp.status_code == 200 else {}
    results = []
    for item in data.get("results", []):
        results.append({
            "id": item.get("primaryAccession"),
            "name": item.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "")
        })
    return jsonify(results)

if __name__ == "__main__":
    app.run(debug=True)