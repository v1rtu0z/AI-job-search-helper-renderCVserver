import datetime
import os
import subprocess
import tempfile

import jwt
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- Configuration ---
# Get the GCP project ID and secret name from environment variables.
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
API_KEY_SECRET = os.environ.get("API_KEY_SECRET")
EXTENSION_SECRET_NAME = os.environ.get("EXTENSION_SECRET_NAME")
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
API_KEY = os.environ.get("API_KEY_SECRET")
EXTENSION_SECRET = os.environ.get("EXTENSION_SECRET_NAME")

# --- Flask App Initialization ---
app = Flask(__name__)
CORS(app)

# Initialize the rate limiter
limiter = Limiter(
    key_func=get_remote_address,  # Rate limit based on IP address
    app=app
)

if not API_KEY or not EXTENSION_SECRET or not JWT_SECRET_KEY:
    print("WARNING: One or more critical secrets are missing. Server will not function securely.")


# This is the root route.
@app.route('/')
def home():
    return '<h1>CV Generator is running!</h1>'


@app.route('/authenticate', methods=['POST'])
@limiter.limit("5 per minute")  # Limit authentication attempts
def authenticate():
    """
    Endpoint for the extension to get a temporary JWT token.
    It requires the extension to send a 'client_secret' in the request body.
    """
    data = request.json
    if not data or 'client_secret' not in data:
        return jsonify({'error': 'Missing client_secret in request'}), 400

    client_secret = data['client_secret']

    if client_secret == EXTENSION_SECRET:
        payload = {
            'exp': datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
            'iat': datetime.datetime.now(datetime.UTC),
            'sub': 'extension-user'
        }
        token = jwt.encode(payload, JWT_SECRET_KEY, algorithm='HS256')
        return jsonify({'token': token}), 200
    else:
        return jsonify({'error': 'Invalid client secret'}), 401


@app.route('/generate-cv', methods=['POST'])
@limiter.limit("12 per minute")  # Limit authentication attempts
def generate_cv():
    """
    Handles POST requests to generate a CV.
    Now requires a valid JWT token in the 'Authorization' header.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Authorization token is missing or invalid'}), 401

    token = auth_header.split(" ")[1]

    try:
        jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
    except jwt.exceptions.ExpiredSignatureError:
        return jsonify({'error': 'Token has expired'}), 401
    except jwt.exceptions.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 401

    if not request.json:
        return jsonify({"error": "No JSON data provided"}), 400

    # Get the API key from the request header
    api_key_from_request = request.headers.get('X-API-Key')

    # Check if the API key is valid
    if api_key_from_request != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    yaml_string = request.json.get('yaml_string')
    filename = request.json.get('filename')
    full_name = request.json.get('full_name')
    theme = request.json.get('theme', 'engineeringclassic')
    design_yaml_string = request.json.get('design_yaml_string')
    locale_yaml_string = request.json.get('locale_yaml_string')

    if not yaml_string:
        return jsonify({"error": "Missing 'yaml_string' in the request body"}), 400
    if not filename:
        return jsonify({"error": "Missing 'filename' in the request body"}), 400
    if not full_name:
        return jsonify({"error": "Missing 'full_name' in the request body"}), 400
    if not design_yaml_string:
        return jsonify({"error": "Missing 'design_yaml_string' in the request body"}), 400
    if not locale_yaml_string:
        return jsonify({"error": "Missing 'locale_yaml_string' in the request body"}), 400

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create the design.yaml and locale.yaml files
            design_yaml_path = os.path.join(temp_dir, "design.yaml")
            locale_yaml_path = os.path.join(temp_dir, "locale.yaml")

            with open(design_yaml_path, "w", encoding='utf-8') as f:
                f.write(design_yaml_string)

            with open(locale_yaml_path, "w", encoding='utf-8') as f:
                f.write(locale_yaml_string)

            # Use `rendercv new` to create the initial CV YAML file
            new_command = [
                "rendercv", "new", full_name,
                "--theme", theme,
                "--dont-create-theme-source-files",
                "--dont-create-markdown-source-files"
            ]
            subprocess.run(new_command, capture_output=True, text=True, check=True, cwd=temp_dir)

            yaml_path_base = f"{full_name.replace(' ', '_')}_CV.yaml"
            yaml_path = os.path.join(temp_dir, yaml_path_base)
            final_pdf_path = str(os.path.join(temp_dir, filename))

            # Overwrite the generated YAML file with the user's provided YAML string
            with open(yaml_path, "w", encoding='utf-8') as f:
                f.write(yaml_string)

            # Run `rendercv render` with the design and locale files
            render_command = [
                "rendercv", "render", yaml_path_base,
                "--design", design_yaml_path,
                "--locale-catalog", locale_yaml_path,
                "--pdf-path", final_pdf_path,
                "--dont-generate-markdown",
                "--dont-generate-html",
                "--dont-generate-png"
            ]
            subprocess.run(render_command, capture_output=True, text=True, check=True, cwd=temp_dir)

            if not os.path.exists(final_pdf_path):
                return jsonify({"error": "Failed to generate PDF."}), 500

            response = send_file(final_pdf_path, mimetype='application/pdf', as_attachment=True, download_name=filename)

            subprocess.run(['rm', '-rf', 'rendercv_output', './*.yaml', './*.pdf'],
                           capture_output=True, text=True, cwd=temp_dir)

            return response

    except subprocess.CalledProcessError as e:
        return jsonify({
            'error': 'An unexpected error occurred.',
            'details': str(e)
        }), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
