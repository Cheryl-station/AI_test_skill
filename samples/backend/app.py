from flask import Flask, jsonify, request, abort

app = Flask(__name__)

USERS = [
    {"id": 1, "name": "Alice", "age": 30},
    {"id": 2, "name": "Bob", "age": 22},
]

@app.route('/api/users', methods=['GET'])
def list_users():
    return jsonify(USERS), 200

@app.route('/api/users', methods=['POST'])
def create_user():
    payload = request.get_json(force=True)
    if not payload or 'name' not in payload or 'age' not in payload:
        abort(400, 'name and age are required')
    if payload['age'] < 0:
        abort(400, 'age must be non-negative')

    new_user = {
        'id': len(USERS) + 1,
        'name': payload['name'],
        'age': payload['age'],
    }
    USERS.append(new_user)
    return jsonify(new_user), 201

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
