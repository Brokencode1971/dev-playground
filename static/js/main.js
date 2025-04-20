// Dynamically choose backend URL based on where the page is hosted
const isLocal =
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1";

const BACKEND = isLocal
  ? "http://127.0.0.1:5000"
  : "https://dev-playground-8c4p.onrender.com";

document.getElementById("searchForm").addEventListener("submit", function(e) {
    e.preventDefault();
    const query = document.getElementById("proteinId").value;

    fetch(`${BACKEND}/search`, {
        method: "POST",
        headers: {
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: "query=" + encodeURIComponent(query),
    })
    .then(res => res.json())
    .then(data => {
        const resultBox = document.getElementById("result");
        if (data.error) {
            resultBox.innerHTML = `<p style="color:red;">${data.error}</p>`;
        } else {
            resultBox.innerHTML = `
                <h2>Results:</h2>
                <p><strong>Protein Name:</strong> ${data.protein_name}</p>
                <p><strong>Organism:</strong> ${data.organism}</p>
                <p><strong>Sequence:</strong>
                   <code style="word-wrap:break-word;">${data.sequence}</code>
                </p>
            `;
        }
    })
    .catch(err => {
        console.error("Error fetching data:", err);
        document.getElementById("result")
                .innerHTML = `<p style="color:red;">Network error. Check backend.</p>`;
    });
});

document.getElementById("proteinId").addEventListener("input", function() {
    const query = this.value;
    if (query.length < 2) {
        document.getElementById("suggestions").innerHTML = "";
        return;
    }

    fetch(`${BACKEND}/autocomplete?q=${encodeURIComponent(query)}`)
        .then(res => res.json())
        .then(data => {
            const suggestionsBox = document.getElementById("suggestions");
            suggestionsBox.innerHTML = "";
            data.forEach(item => {
                const div = document.createElement("div");
                div.className = "suggestion";
                div.textContent = `${item.name} (${item.id})`;
                div.addEventListener("click", function() {
                    document.getElementById("proteinId").value = item.id;
                    suggestionsBox.innerHTML = "";
                });
                suggestionsBox.appendChild(div);
            });
        })
        .catch(err => console.error("Autocomplete error:", err));
});