function fetchData() {
    const localURL = "http://127.0.0.1:5000/test";
    const renderURL = "https://your-render-url.onrender.com/test";  // Replace with actual Render URL

    fetch(localURL)
        .then(response => response.json())
        .then(data => document.getElementById("response").innerText = data.message)
        .catch(error => {
            console.error("Local server failed, trying Render:", error);
            fetch(renderURL)
                .then(response => response.json())
                .then(data => document.getElementById("response").innerText = data.message)
                .catch(err => console.error("Both local and Render servers failed:", err));
        });
}
