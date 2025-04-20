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

    url = f"https://rest.uniprot.org/uniprotkb/{protein_id}.json"
    response = requests.get(url)
    if response.status_code != 200:
        return jsonify({"error": "Protein not found."}), 404

    data = response.json()
    protein_name = data["proteinDescription"]["recommendedName"]["fullName"]["value"]
    organism     = data["organism"]["scientificName"]
    sequence     = data["sequence"]["value"]

    return jsonify({
        "protein_name": protein_name,
        "organism":    organism,
        "sequence":    sequence
    })

@app.route("/autocomplete")
def autocomplete():
    query = request.args.get("q")
    if not query:
        return jsonify([])

    url = (
        "https://rest.uniprot.org/uniprotkb/search"
        f"?query={query}"
        "&fields=accession,protein_name"
        "&format=json"
        "&size=5"
    )
    response = requests.get(url)
    if response.status_code != 200:
        return jsonify([])

    data = response.json()
    results = []
    for item in data.get("results", []):
        results.append({
            "id":   item.get("primaryAccession"),
            "name": item
                     .get("proteinDescription", {})
                     .get("recommendedName", {})
                     .get("fullName", {})
                     .get("value", "")
        })
    return jsonify(results)

if __name__ == "__main__":
    # on Render you don't need host/port override
    app.run(debug=True)
