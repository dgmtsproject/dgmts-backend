from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "OK", "message": "Flask on GoDaddy Works!"})

@app.route('/api/test')
def test():
    return jsonify({"data": "This is a test endpoint!"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)  # Added port configuration