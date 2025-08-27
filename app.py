import datetime
import functools
import json
import os
import re
import subprocess
import tempfile

import jwt
import yaml
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.llms.google_genai import GoogleGenAI
from werkzeug.exceptions import Unauthorized
import requests

from prompts import PROMPTS

# --- Configuration ---
EXTENSION_SECRET = os.environ.get('EXTENSION_SECRET')
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
REDIS_USER = os.environ.get("REDIS_USER")
REDIS_HOST = os.environ.get("REDIS_HOST")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")

# --- Flask App Initialization ---
app = Flask(__name__)
CORS(app)


# --- JWT & Rate Limiting Helpers ---
def get_jwt_user_id():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise Unauthorized("Authorization token is missing or invalid.")
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
        return payload.get('sub')
    except jwt.exceptions.ExpiredSignatureError:
        raise Unauthorized("Token has expired.")
    except jwt.exceptions.InvalidTokenError:
        raise Unauthorized("Invalid token.")


# Initialize the rate limiter with a key function for JWT-based auth
limiter = Limiter(
    key_func=get_jwt_user_id,
    app=app,
    storage_uri=f"redis://{REDIS_USER}:{REDIS_PASSWORD}@{REDIS_HOST}",
)


def jwt_required(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            get_jwt_user_id()
            return func(*args, **kwargs)
        except Unauthorized as e:
            return jsonify({'error': str(e)}), 401

    return wrapper


# --- LLM and Prompt Definitions ---
def get_llm(user_api_key: str | None = None, model_name: str | None = None):
    # Use the user's key if provided, otherwise fallback to the developer's key
    api_key = user_api_key if user_api_key else GEMINI_API_KEY
    if not api_key:
        raise ValueError("No Gemini API key provided. Please provide one.")

    return GoogleGenAI(
        model=model_name,
        api_key=api_key,
    )


def handle_llm_errors(func):
    """Decorator to handle LLM errors and return appropriate HTTP responses"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
            elif e.response.status_code == 503:
                return jsonify({"error": "Service temporarily unavailable. Please try again later."}), 503
            else:
                return jsonify({"error": f"HTTP error: {str(e)}"}), 500
        except Exception as e:
            error_str = str(e).lower()
            if any(phrase in error_str for phrase in ['rate limit', 'quota', 'too many requests', '429']):
                return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
            return jsonify({"error": str(e)}), 500

    return wrapper


# --- Server Endpoints ---
@app.route('/')
def home():
    return '<h1>CV Generator is running!</h1>'


@app.route('/authenticate', methods=['POST'])
@limiter.limit("2 per minute", key_func=get_remote_address)  # Limit authentication attempts per IP for safety
def authenticate():
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
        print(
            f"Authentication attempt with invalid client secret: {client_secret}, EXTENSION_SECRET: "
            f"{EXTENSION_SECRET}"
        )
        return jsonify({'error': 'Invalid client secret'}), 401


# --- New AI Endpoints ---
@app.route('/get-resume-json', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
@handle_llm_errors
def get_resume_json_endpoint():
    data = request.json
    resume_content = data.get('resume_content')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')
    if not resume_content:
        return jsonify({"error": "Missing 'resume_content'"}), 400

    llm = get_llm(user_api_key, model_name=model_name)
    messages = [
        ChatMessage(
            role=MessageRole.SYSTEM,
            content="You are a professional career assistant. Your task is to provide a json formatted " +
                    "resume data and an advanced linkedin search query based on the user's resume and additional " +
                    "details.",
        ),
        ChatMessage(
            role=MessageRole.USER,
            content=PROMPTS["RESUME_AND_SEARCH_QUERY"](resume_content),
        ),
    ]
    response = llm.chat(messages)
    llm_output = response.message.content.strip()

    if llm_output.startswith('```json'):
        llm_output = llm_output.split('```json', 1)[1].rsplit('```', 1)[0].strip()
    else:
        raise ValueError('Response does not start with ```json```.')

    return llm_output


@app.route('/generate-search-query', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
@handle_llm_errors
def generate_search_query_endpoint():
    data = request.json
    resume_json_data = data.get('resume_json_data')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')
    if not resume_json_data:
        return jsonify({"error": "Missing 'resume_json_data'"}), 400

    llm = get_llm(user_api_key, model_name=model_name)
    messages = [
        ChatMessage(
            role=MessageRole.SYSTEM,
            content="You are a professional career assistant. Your task is to provide a json " +
                    "formatted resume data and an advanced linkedin search query based on the user's " +
                    "resume and additional details.",
        ),
        ChatMessage(
            role=MessageRole.USER,
            content=PROMPTS["SEARCH_QUERY_ONLY"](resume_json_data),
        )
    ]
    response = llm.chat(messages)
    search_query = response.message.content.strip()
    return jsonify({"search_query": search_query})


@app.route('/analyze-job-posting', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
@handle_llm_errors
def analyze_job_posting_endpoint():
    data = request.json
    job_posting_text = data.get('job_posting_text')
    resume_json_data = data.get('resume_json_data')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')

    if not job_posting_text or not resume_json_data:
        return jsonify({"error": "Missing 'job_posting_text' or 'resume_json_data'"}), 400

    with open("job_analysis_format.html", "r") as f:
        job_analysis_format = f.read()

    llm = get_llm(user_api_key, model_name=model_name)
    analysis_prompt = PROMPTS["JOB_ANALYSIS"](job_posting_text, resume_json_data, job_analysis_format)
    messages = [
        ChatMessage(
            role=MessageRole.SYSTEM,
            content="You are a professional career assistant. Your task is to provide a job analysis in a structured Markdown document.",
        ),
        ChatMessage(
            role=MessageRole.USER,
            content=analysis_prompt,
        )
    ]
    response = llm.chat(messages)
    llm_output = response.message.content.strip()
    print("Raw LLM Output:")
    print(llm_output)

    cleaned_output = llm_output.strip().removeprefix('```html').removesuffix('```').strip()
    print("\nCleaned LLM Output:")
    print(cleaned_output)

    lines = cleaned_output.split('\n', 1)

    job_id = lines[0].strip()

    if job_id.startswith('#'):
        job_id = job_id[1:].strip()

    if '@' not in job_id:
        raise ValueError('Job analysis does not start with [job title] @ [company name].')

    company_name = job_id.split(' @ ')[-1]

    # The rest of the content is the job analysis
    job_analysis = (lines[1] or '').strip() if len(lines) > 1 else ''

    return jsonify({
        "job_id": job_id,
        "company_name": company_name,
        "job_analysis": job_analysis,
    })


@app.route('/generate-cover-letter', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
@handle_llm_errors
def generate_cover_letter_endpoint():
    data = request.json
    job_posting_text = data.get('job_posting_text')
    resume_json_data = data.get('resume_json_data')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')
    if not job_posting_text or not resume_json_data:
        return jsonify({"error": "Missing 'job_posting_text' or 'resume_json_data'"}), 400

    llm = get_llm(user_api_key, model_name=model_name)
    prompt = PROMPTS["COVER_LETTER"](job_posting_text, resume_json_data)
    messages = [
        ChatMessage(
            role=MessageRole.SYSTEM,
            content="You are a professional career assistant. Your task is to generate a cover letter that" +
                    " will help the user apply for the job based on the job description, and the users resume " +
                    "data (JSON) provided.",
        ),
        ChatMessage(
            role=MessageRole.USER,
            content=prompt,
        )
    ]
    response = llm.chat(messages)
    cover_letter_content = response.message.content.strip()
    return jsonify({"content": cover_letter_content})


@app.route('/tailor-resume', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
def tailor_resume_endpoint():
    data = request.json
    job_posting_text = data.get('job_posting_text')
    resume_json_data = data.get('resume_json_data')
    theme = data.get('theme', 'engineeringclassic')
    filename = data.get('filename')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')

    if not all([
        job_posting_text,
        resume_json_data,
        filename
    ]):
        return jsonify({"error": "Missing one or more required fields"}), 400

    llm = get_llm(user_api_key, model_name=model_name)
    prompt = PROMPTS["JSON_CONVERSION"](job_posting_text, resume_json_data)
    messages = [
        ChatMessage(
            role=MessageRole.SYSTEM,
            content="You are a professional career assistant. Your task is to convert the JSON resume data into a *tailored* YAML resume, based on the job description.",
        ),
        ChatMessage(
            role=MessageRole.USER,
            content=prompt,
        )
    ]
    last_error_details = None

    resume_json_data = json.loads(resume_json_data)
    full_name = resume_json_data["personal"]["full_name"].lower().replace(" ", "_")

    yaml_file_contents = None

    # Retry the entire process (LLM call + PDF generation) up to 3 times
    with tempfile.TemporaryDirectory() as temp_dir:
        new_command = [
            "rendercv", "new", full_name,
            "--theme", theme
        ]
        print(f"Running command: {new_command}")
        subprocess.run(new_command, capture_output=True, text=True, check=True, cwd=temp_dir)

        yaml_path_base = f"{full_name}_CV.yaml"
        yaml_path = os.path.join(temp_dir, yaml_path_base)
        final_pdf_path = str(os.path.join(temp_dir, filename))

        for attempt in range(3):
            try:
                response = llm.chat(messages)
                json_string = response.message.content.strip()

                if json_string.startswith('```json'):
                    json_string = json_string.split('```json', 1)[1].rsplit('```', 1)[0].strip()
                elif json_string.startswith('```'):
                    json_string = json_string.split('```', 1)[1].rsplit('```', 1)[0].strip()

                try:
                    json_data = json.loads(json_string)

                    yaml_string = yaml.dump(json_data, default_flow_style=False, allow_unicode=True, sort_keys=False,
                                            default_style='"')

                    yaml_string = re.sub(r'^(\s*)(-\s+)?"([^"]+)":(\s)', r'\1\2\3:\4', yaml_string, flags=re.MULTILINE)
                except json.JSONDecodeError as e:
                    print(f"Error parsing JSON: {e}")
                    print(f"Raw response: {json_string}")
                    raise

                with open(yaml_path, "r") as f:
                    existing_content = f.read()

                try:
                    split_index = existing_content.index('design:')
                    end_of_file_content = existing_content[split_index:].strip()
                except ValueError:
                    end_of_file_content = ''

                combined_yaml = f"{yaml_string.strip()}\n{end_of_file_content}\n"

                with open(yaml_path, "w") as f:
                    f.write(combined_yaml.strip())

                with open(yaml_path, "r") as f:
                    print(f"Contents of {yaml_path}:")
                    yaml_file_contents = f.read()
                    print(yaml_file_contents)

                render_command = [
                    "rendercv", "render", yaml_path_base,
                    "--pdf-path", final_pdf_path,
                    "--design.page.show_last_updated_date", "false",
                    "--locale.phone_number_format", "international"
                ]
                print(f"Running command: {render_command}")
                subprocess.run(render_command, capture_output=True, text=True, check=True, cwd=temp_dir)

                if os.path.exists(final_pdf_path):
                    return send_file(final_pdf_path, mimetype='application/pdf', as_attachment=True,
                                     download_name=filename)
                else:
                    raise Exception("Failed to generate PDF file despite successful command execution.")

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
                elif e.response.status_code == 503:
                    return jsonify({"error": "Service temporarily unavailable. Please try again later."}), 503
                else:
                    last_error_details = {"error": f"HTTP error: {str(e)}"}
            except (subprocess.CalledProcessError, Exception) as e:
                print(f"Attempt {attempt + 1} failed. Problematic YAML:")
                print(yaml_file_contents)

                # Check for rate limiting errors
                error_str = str(e).lower()
                if any(phrase in error_str for phrase in ['rate limit', 'quota', 'too many requests', '429']):
                    return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429

                if isinstance(e, subprocess.CalledProcessError):
                    last_error_details = {
                        'error': f"Command failed with exit status {e.returncode}",
                        'details': e.stdout + '\n' + e.stderr
                    }
                else:
                    last_error_details = {"error": str(e)}

    return jsonify(last_error_details), 500


# NOTE: # Uncomment when testing and debugging. Rate limiting needs to be commented for testing
# if __name__ == "__main__": 
#     import threading
#     import time
#     from test import run_tests
# 
#     # Start the Flask app in a separate thread
#     flask_thread = threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 8080})
#     flask_thread.start()
# 
#     # Give the server a moment to start
#     time.sleep(2)
# 
#     # Run the tests
#     run_tests()

if __name__ == '__main__':
    # This will run a development server that hot-reloads on file changes.
    # It will only run when you execute `python app.py`
    # Gunicorn will not execute this part of the code
    app.run(host='0.0.0.0', port=8080, debug=True)