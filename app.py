from flask import Flask, jsonify
from flask_cors import CORS  # No extra package needed, Flask-Cors is included in Flask

app = Flask(__name__)

# Minimal CORS setup - will allow all origins (for testing only)
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# Test endpoint
@app.route('/api/test')
def test_connection():
    return jsonify({
        "status": "success",
        "message": "Backend is connected!",
        "data": {"sample": 123}
    })

if __name__ == '__main__':
    app.run()