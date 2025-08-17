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
def get_llm(user_api_key: str | None = None):
    # Use the user's key if provided, otherwise fallback to the developer's key
    api_key = user_api_key if user_api_key else GEMINI_API_KEY
    if not api_key:
        raise ValueError("No Gemini API key provided. Please provide one.")

    return GoogleGenAI(
        model=MODEL_NAME,
        api_key=api_key,
    )


PROMPTS = {
    "RESUME_AND_SEARCH_QUERY": lambda resume_content: f"""
        Based on the following user data (resume and additional details), perform two tasks:
        1. Generate a personalized LinkedIn search query using Boolean search operators, formatted as: ("job title 1" OR "job title 2") AND NOT ("skill 1" OR "skill 2" OR "job title 3").
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
    "JOB_ANALYSIS": lambda job_posting_text, resume_json_data: f"""
        You are a professional career assistant. Your task is to provide a job analysis in a structured Markdown document.
        You should compare the users resume JSON data, available in the index, against the job description.
        The resume JSON data includes 'additionalDetails' field you should pay attention to.

        **Input Data:**
        Job Description: {job_posting_text}
        **Resume data JSON:**
        {resume_json_data}

        **Instructions:**
        1. Format the analysis into a brief and professional Markdown document.
        2. The document must have the following sections: "Strengths", "Areas for Improvement" and "Overall Fit".
        3. For "Overall Fit", provide a concise summary with a color-coded score. Use only the scores: 'very poor fit', 'poor fit', 'moderate fit', 'good fit', 'very good fit', 'questionable fit'.
        4. The "Overall Fit" summary should be formatted as a single line, for example: "**Overall Fit:** good fit".
        5. Color coding should be done with an HTML wrapping of the overall fit score, for example: "<span style='color: green'>good fit</span>".
        6. Make sure that the "Overall Fit" also contains an actual summary of why it's a good fit, not just the "score".
        7. Ensure all Markdown syntax is correct for headings, lists, and bold text.
        8. The output should start with [job title] @ [company name] so that it can be easily identified. If these can't be found, return an error message.
        9. The analysis should take into consideration any missing or misaligned elements in the job description like location, remote work policy, industry, position, seniority, salary range, etc.
        10.  Start/end dates "in the future" are okay, your training end date was probably years ago
    """,
    "COVER_LETTER": lambda job_posting_text, resume_json_data: f"""
        You are a professional career assistant. Your task is to generate a cover letter that will
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
        You are a professional career assistant. Your task is to convert the JSON resume data into a *tailored*
         YAML resume, based on the job description.

        **Input Data:**
        Job Description: {job_posting_text}
        **Resume data JSON:**
        {resume_json_data}
        **Example YAML context:**
        {example_yaml_resume}

        **Instructions:**
        1.  Follow the exact YAML structure from the example YAML context.
        2.  Use the Job Description to highlight and reorder relevant skills and experiences from the JSON data.
        3.  Maintain professional formatting and proper YAML syntax.
        4.  Do NOT add any placeholders.
        5.  Remember that *highlights must be simple strings wrapped in quotation marks!* They *can't* have a title, name or start with something like key: ...!
        6.  Remember that the 'section' is a part of 'cv', ergo it needs to be indented inside of it and *not* on the same level
        7.  Start/end dates "in the future" are okay, your training end date was probably years ago
        8.  Start/end dates need to be in the format YYYY-MM.
        9.  Make sure that the YAML data correctly reflects the JSON data.
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
    additional_details = data.get('additional_details')
    user_api_key = data.get('gemini_api_key')
    if not resume_content:
        return jsonify({"error": "Missing 'resume_content'"}), 400

    try:
        llm = get_llm(user_api_key)
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

        parsed_data = eval(llm_output)
        parsed_data["resume_data"]["additionalDetails"] = additional_details
        return jsonify(parsed_data)

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
    if not resume_json_data:
        return jsonify({"error": "Missing 'resume_json_data'"}), 400

    try:
        llm = get_llm(user_api_key)
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

    if not job_posting_text or not resume_json_data:
        return jsonify({"error": "Missing 'job_posting_text' or 'resume_json_data'"}), 400

    try:
        llm = get_llm(user_api_key)
        analysis_prompt = PROMPTS["JOB_ANALYSIS"](job_posting_text, resume_json_data)
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
        lines = llm_output.split('\n')
        job_id = lines[0].strip()
        if '@' not in job_id:
            raise ValueError('Job analysis does not start with [job title] @ [company name].')

        company_name = job_id.split(' @ ')[-1]
        job_analysis = (llm_output.split('\n', 1)[1] or '').strip()

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
    if not job_posting_text or not resume_json_data:
        return jsonify({"error": "Missing 'job_posting_text' or 'resume_json_data'"}), 400

    try:
        llm = get_llm(user_api_key)
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
    design_yaml_string = data.get('design_yaml_string')
    locale_yaml_string = data.get('locale_yaml_string')
    filename = data.get('filename')
    user_api_key = data.get('gemini_api_key')

    if not all([
        job_posting_text,
        resume_json_data,
        design_yaml_string,
        locale_yaml_string,
        filename
    ]):
        return jsonify({"error": "Missing one or more required fields"}), 400

    try:
        llm = get_llm(user_api_key)
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
        response = llm.chat(messages)
        yaml_string = response.message.content.strip()

        if yaml_string.startswith('```yaml'):
            yaml_string = yaml_string.split('```yaml', 1)[1].rsplit('```', 1)[0].strip()

        with tempfile.TemporaryDirectory() as temp_dir:
            design_yaml_path = os.path.join(temp_dir, "design.yaml")
            locale_yaml_path = os.path.join(temp_dir, "locale.yaml")

            with open(design_yaml_path, "w", encoding='utf-8') as f:
                f.write(design_yaml_string)

            with open(locale_yaml_path, "w", encoding='utf-8') as f:
                f.write(locale_yaml_string)

            resume_json_data = json.loads(resume_json_data)

            full_name = resume_json_data["personal"]["full_name"].lower().replace(" ", "_")

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

            with open(yaml_path, "w", encoding='utf-8') as f:
                f.write(yaml_string)

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
            'error': 'Command failed with exit status {}'.format(e.returncode),
            'details': e.stdout + '\n' + e.stderr
        }), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
