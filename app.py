from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/test', methods=['GET'])
def test_connection():
    return jsonify({"message": "Frontend is connected to backend!"})

if __name__ == '__main__':
    app.run(debug=True)

