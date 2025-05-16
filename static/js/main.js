const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
const BACKEND = isLocal
  ? "http://127.0.0.1:5000"
  : "https://dev-playground-8c4p.onrender.com";

document.getElementById("searchForm").addEventListener("submit", async function(e) {
  e.preventDefault();
  const query = document.getElementById("proteinId").value;
  const res = await fetch(`${BACKEND}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `query=${encodeURIComponent(query)}`
  });
  const data = await res.json();
  const resultBox = document.getElementById("result");
  if (data.error) {
    resultBox.innerHTML = `<p style="color:red;">${data.error}</p>`;
    return;
  }

  let html = `
    <h2>Results:</h2>
    <p><strong>Protein Name:</strong> ${data.protein_name}</p>
    <p><strong>Organism:</strong> ${data.organism}</p>
    <p><strong>Sequence:</strong> <code style="word-wrap:break-word;">${data.sequence}</code></p>
    <p><strong>PDB Structure:</strong></p>
  `;

  if (data.pdb_ids.length) {
    const pdb = data.pdb_ids[0];
    html += `
      <p>Available PDB ID: ${pdb}</p>
      <iframe src="https://www.rcsb.org/3d-view/${pdb}" width="100%" height="400" frameborder="0"></iframe>
    `;
    if (data.pdb_text) {
      html += `
        <details>
          <summary>View Raw PDB</summary>
          <pre style="max-height:300px; overflow:auto; background:#f0f0f0; padding:10px;">${data.pdb_text.replace(/</g, '&lt;')}</pre>
        </details>
      `;
    }
  } else {
    html += `<p style="color:gray;">PDB structure not available.</p>`;
  }

  // External Links Button
  html += `<button id="extLinksBtn">External links</button>`;
  html += `<div id="extLinks" style="display:none;"><ul>`;
  data.cross_links.forEach(link => {
    html += `<li><a href="${link.url}" target="_blank">${link.db}: ${link.id}</a></li>`;
  });
  html += `</ul></div>`;

  // BLAST Link
  html += `<p><a href="https://blast.ncbi.nlm.nih.gov/Blast.cgi?PROGRAM=blastp&QUERY=${encodeURIComponent(data.sequence)}" target="_blank">Run BLAST</a></p>`;

  resultBox.innerHTML = html;

  // Toggle external links
  document.getElementById('extLinksBtn').addEventListener('click', () => {
    const extDiv = document.getElementById('extLinks');
    extDiv.style.display = extDiv.style.display === 'none' ? 'block' : 'none';
  });
});

// Autocomplete unchanged
const input = document.getElementById("proteinId");
input.addEventListener("input", async function() {
  const q = this.value;
  if (q.length < 2) {
    document.getElementById("suggestions").innerHTML = "";
    return;
  }
  const resp = await fetch(`${BACKEND}/autocomplete?q=${encodeURIComponent(q)}`);
  const suggestions = await resp.json();
  const box = document.getElementById("suggestions");
  box.innerHTML = "";
  suggestions.forEach(item => {
    const div = document.createElement("div");
    div.className = "suggestion";
    div.textContent = `${item.name} (${item.id})`;
    div.addEventListener("click", () => {
      input.value = item.id;
      box.innerHTML = "";
    });
    box.appendChild(div);
  });
});