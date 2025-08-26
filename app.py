import datetime
import functools
import json
import os
import subprocess
import tempfile

import jwt
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.llms.google_genai import GoogleGenAI
from werkzeug.exceptions import Unauthorized

# --- Configuration ---
EXTENSION_SECRET = os.environ.get('EXTENSION_SECRET')
MODEL_NAME = os.environ.get("MODEL_NAME")
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
REDIS_USER = os.environ.get("REDIS_USER")
REDIS_HOST = os.environ.get("REDIS_HOST")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")

# TODO: Make prompt a parameter

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
        model=model_name or MODEL_NAME,
        api_key=api_key,
    )


PROMPTS = {
    "RESUME_AND_SEARCH_QUERY": lambda resume_content: f"""
        Based on the following user data (resume and additional details), perform two tasks:
        1. Generate a personalized LinkedIn search query using Boolean search operators, formatted as: ("job title 1" OR "job title 2") AND NOT ("skill 1" OR "skill 2" OR "job title 3"). Note how multi-word strings are always quoted while single-word strings don't have to be.
        2. Extract the user's resume data into a structured JSON format.

        User data:
        Resume: {resume_content}

        Provide the output as a single JSON object with two keys: "search_query" and "resume_data".
        The "resume_data" value should be a JSON object representing the user's resume.
        **Output Format:**
        {{
          "search_query": "...",
          "resume_data": {{
              "personal": {{
                "full_name": "...",
                "email": "...",
                "phone": "...",
                "website": "..."
                "social_networks": [...]
                "location": "..."
                ...
              }},
              "summary": "...",
              "education": [...],
              "experience": [...],
              "projects": [...],
              "skills": [...],
              "certifications": [...],
              ...
          }}
        }}
    """,
    "SEARCH_QUERY_ONLY": lambda resume_json_data: f"""
        Based on the user's structured resume data (JSON) provided, generate a personalized LinkedIn search query.
        The query should use Boolean search operators and be in the format: ("job title 1" OR "job title 2") AND NOT ("skill 1" OR "skill 2" OR "job title 3").

        **Resume data JSON:**
        {resume_json_data}

        Return only the search query string and nothing else.
    """,
    "JOB_ANALYSIS": lambda job_posting_text, resume_json_data, job_analysis_format: f"""
        The year is {datetime.date.today().year}. You are a professional career assistant. Your task is to provide a comprehensive job analysis in a structured HTML document, strictly adhering to the format outlined in the provided 'Job Analysis Format' file.

        **Input Data:**
        Job Description:
        {job_posting_text}
        **Resume data JSON:**
        {resume_json_data}
        **Job Analysis Format:**
        {job_analysis_format}
        
        **Instructions:**
        - Compare the user's resume JSON data (including the 'additionalDetails' field) against the job description.
        - The analysis should take into consideration any missing or misaligned elements from the job description like location, remote work policy, industry, position, seniority, salary range, etc.
        - The output must start with [job title] @ [company name] so that it can be easily identified. If these can't be found, return an error message.
        - Replace strength name placeholders with actual sensible data. Same for ares for improvement
        - Make sure that the titles are larger that list items and that you're not repeating yourself
        - Change only the text values in the HTML format, leave everything else as it is.
    """,
    "COVER_LETTER": lambda job_posting_text, resume_json_data: f"""
        The year is {datetime.date.today().year}. You are a professional career assistant. Your task is to generate a cover letter that will
        help the user apply for the job based on the job description, and the users resume data (JSON) provided.
        The resume JSON data includes 'additionalDetails' field you should pay attention to.

        **Job Description:**
        {job_posting_text}
        **Resume data JSON:**
        {resume_json_data}

        Some general guidelines: make it at most 3-4 paragraphs long, address their strengths and in
        case that there are any missing skills, address those head on based on the users other skills
        (ie stuff like quick learning, hard-working, commitment to excellence etc). Make sure to
        reference the details from the job post as much as possible.
        Note that the job description might not be in English and shouldn't be dismissed in that case!
        Always write the cover letter in the same language as the job description.
    """,
    "YAML_CONVERSION": lambda job_posting_text, resume_json_data, example_yaml_resume: f"""
        The year is {datetime.date.today().year}. You are a professional career assistant. Your task is to convert the JSON resume data into a tailored YAML resume, based on the job description.

        Input Data:
        Job Description:
        {job_posting_text}
        Resume data JSON:
        {resume_json_data}
        Example YAML context:
        {example_yaml_resume}
        
        Instructions:
        - Follow the exact YAML structure from the example YAML context.
        - Use the Job Description to highlight and reorder relevant skills and experiences from the JSON data.
        - Maintain professional formatting and proper YAML syntax.
        - Do NOT add any placeholders.
        - Remember that highlights must be simple strings wrapped in quotation marks! They can't have a title, name or start with something like key: ...!.
        - Remember that the 'section' is a part of 'cv', ergo it needs to be indented inside of it and not on the same level.
        - Start/end dates need to be in the format YYYY-MM.
        - The YAML you generate shouldn't contain strings in the format of <X or >X. These should always be separated by a space, like < X or > X.
        - Do not include additional details in the YAML you generate. Only use those to guide the contents of the output YAML.
        - The example YAML contains all of the options for the keys. Do not attempt to add any keys that are not in the example YAML but feel free to omit the unnecessary ones.
        - Make sure to use the same keys as in the example YAML. For example a project entry needs a name. A title or label won't be accepted as keys.
        - Pay attention not to confuse the user's location (present in the resume data JSON) and the job's location (present in the job description).
        - Do not use the literal block scalar ```key: |``` syntax. Instead, use a list of strings for multiline content, for example:
        
        key:
        - "Some text for the first point."
        - "Some text for the second point."
    """,
}


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
def get_resume_json_endpoint():
    data = request.json
    resume_content = data.get('resume_content')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')
    if not resume_content:
        return jsonify({"error": "Missing 'resume_content'"}), 400

    try:
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

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/generate-search-query', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
def generate_search_query_endpoint():
    data = request.json
    resume_json_data = data.get('resume_json_data')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')
    if not resume_json_data:
        return jsonify({"error": "Missing 'resume_json_data'"}), 400

    try:
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

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/analyze-job-posting', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
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

    try:
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

        # Remove the code block wrapper
        cleaned_output = llm_output.strip().removeprefix('```html').removesuffix('```').strip()
        print("\nCleaned LLM Output:")
        print(cleaned_output)

        # Split the cleaned output into lines
        lines = cleaned_output.split('\n', 1)

        # The job ID is now the first line of the cleaned output
        job_id = lines[0].strip()

        # Handle the optional markdown heading (#)
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

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/generate-cover-letter', methods=['POST'])
@jwt_required
@limiter.limit("2 per minute")
@limiter.limit("20 per hour")
@limiter.limit("100 per day")
def generate_cover_letter_endpoint():
    data = request.json
    job_posting_text = data.get('job_posting_text')
    resume_json_data = data.get('resume_json_data')
    user_api_key = data.get('gemini_api_key')
    model_name = data.get('model_name')
    if not job_posting_text or not resume_json_data:
        return jsonify({"error": "Missing 'job_posting_text' or 'resume_json_data'"}), 400

    try:
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

    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    with open("example_resume.yaml", "r") as f:
        example_yaml_resume = f.read()
    prompt = PROMPTS["YAML_CONVERSION"](job_posting_text, resume_json_data, example_yaml_resume)
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

    yaml_string = None
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
                yaml_string = response.message.content.strip()

                if yaml_string.startswith('```yaml'):
                    yaml_string = yaml_string.split('```yaml', 1)[1].rsplit('```', 1)[0].strip()

                    with open(yaml_path, "r", encoding='utf-8') as f:
                        existing_content = f.read()

                    try:
                        split_index = existing_content.index('design:')
                        end_of_file_content = existing_content[split_index:].strip()
                    except ValueError:
                        end_of_file_content = ''

                    combined_yaml = f"{yaml_string.strip()}\n{end_of_file_content}\n"

                    with open(yaml_path, "w", encoding='utf-8') as f:
                        f.write(combined_yaml.strip())

                    # we log the yaml_path contents
                    with open(yaml_path, "r", encoding='utf-8') as f:
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

            except (subprocess.CalledProcessError, Exception) as e:
                print(f"Attempt {attempt + 1} failed. Problematic YAML:")
                print(yaml_file_contents)
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
