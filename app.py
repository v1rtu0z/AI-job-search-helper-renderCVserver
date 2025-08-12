import tempfile

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import subprocess
import os

# Import the Google Secret Manager client library
from google.cloud import secretmanager

# --- Configuration ---
# Get the GCP project ID and secret name from environment variables.
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
API_KEY_SECRET = os.environ.get("API_KEY_SECRET")

# Create the Secret Manager client
secret_client = secretmanager.SecretManagerServiceClient()


# --- Functions ---
def get_secret():
    """
    Retrieves the API key from Google Secret Manager.
    """
    if not GCP_PROJECT_ID or not API_KEY_SECRET:
        print("Error: GCP_PROJECT_ID or API_KEY_SECRET environment variable not set.")
        return None

    try:
        # Build the resource name of the secret version.
        name = f"projects/{GCP_PROJECT_ID}/secrets/{API_KEY_SECRET}/versions/latest"

        # Access the secret version.
        response = secret_client.access_secret_version(request={"name": name})

        # Return the secret payload.
        secret_value = response.payload.data.decode("UTF-8")
        print("Successfully retrieved API key from Secret Manager.")
        return secret_value
    except Exception as e:
        print(f"Failed to retrieve secret '{API_KEY_SECRET}': {str(e)}")
        return None


# --- Flask App Initialization ---
app = Flask(__name__)
CORS(app)

# Retrieve the API key when the application starts
API_KEY = get_secret()
if not API_KEY:
    print("WARNING: API key not available. All requests will be unauthorized.")


# This is the root route.
@app.route('/')
def home():
    return '<h1>CV Generator is running!</h1>'


@app.route('/generate-cv', methods=['POST'])
def generate_cv():
    """
    Generates a PDF CV using the rendercv CLI based on a JSON payload.
    The payload should include:
    - yaml_string: The YAML content for the CV.
    - filename: The desired output filename for the PDF.
    - full_name: The user's full name, used for generating the initial YAML file.
    - theme: An optional theme name. Defaults to 'engineeringclassic'.
    - design_yaml_string: Optional content for the design file.
    - locale_yaml_string: Optional content for the locale file.
    """
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
