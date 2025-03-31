const testBtn = document.getElementById('testBtn');
const resultDiv = document.getElementById('result');

testBtn.addEventListener('click', testConnection);

async function testConnection() {
    try {
        const response = await fetch('https://dev-playground-8c4p.onrender.com/api/test');
        const data = await response.json();
        resultDiv.innerHTML = `
            <p>Status: ${data.status}</p>
            <p>Message: ${data.message}</p>
            <p>Data: ${JSON.stringify(data.data)}</p>
        `;
    } catch (error) {
        resultDiv.innerHTML = `Error: ${error.message}`;
    }
}