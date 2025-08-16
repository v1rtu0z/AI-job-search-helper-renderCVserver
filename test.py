import os
import time


# --- Test Block ---
def run_tests():
    import requests
    import pdfplumber
    import json

    print("--- Running API Endpoint Tests ---")
    base_url = "http://127.0.0.1:8080"

    # # Step 1: Confirm the server is running
    print("\n1. Pinging base URL to confirm server is running...")
    max_retries = 5
    for i in range(max_retries):
        try:
            response = requests.get(base_url)
            response.raise_for_status()
            print("Server is up and running!")
            break
        except requests.exceptions.ConnectionError:
            print(f"Server not ready yet. Retrying in 2 seconds... ({i + 1}/{max_retries})")
            time.sleep(2)
    else:
        print("Failed to connect to the server. Please ensure the app is running and accessible.")
        return

    # Step 2: Authenticate and get JWT token
    print("\n2. Authenticating...")
    try:
        auth_response = requests.post(f"{base_url}/authenticate",
                                      json={"client_secret": os.getenv("EXTENSION_SECRET")})
        auth_response.raise_for_status()
        token = auth_response.json().get("token")
        if not token:
            raise ValueError("Authentication failed. No token received.")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        print("Authentication successful. JWT token received.")
    except Exception as e:
        print(f"Authentication failed: {e}")
        return

    # Define file paths for inputs
    resume_file_path = "/home/nikola/Downloads/personal/Nikola_Mandic_resume.pdf"  # <--- REPLACE WITH YOUR RESUME FILE PATH
    job_posting_file_path = "sample_job_posting.txt"  # <--- REPLACE WITH YOUR JOB POSTING FILE PATH
    example_yaml_file_path = "example_resume.yaml"  # <--- REPLACE WITH YOUR EXAMPLE YAML PATH
    design_yaml_file_path = "design.yaml"  # <--- REPLACE WITH YOUR DESIGN YAML PATH
    locale_yaml_file_path = "locale.yaml"  # <--- REPLACE WITH YOUR LOCALE YAML PATH

    try:
        # Read the PDF resume content
        with pdfplumber.open(resume_file_path) as pdf:
            resume_content = "".join([page.extract_text() for page in pdf.pages if page.extract_text()])

        # Needed in case we're skipping some steps
        # full_name = "Nikola Mandic"
        # company_name = "Subbyx"
        # with open("resume_data.json", "r") as f:
        #     resume_json_data = f.read()

        with open(job_posting_file_path, "r", encoding="utf-8") as f:
            job_posting_text = f.read()
        with open(example_yaml_file_path, "r", encoding="utf-8") as f:
            example_yaml_resume = f.read()
        with open(design_yaml_file_path, "r", encoding="utf-8") as f:
            design_yaml_string = f.read()
        with open(locale_yaml_file_path, "r", encoding="utf-8") as f:
            locale_yaml_string = f.read()
    except FileNotFoundError as e:
        print(f"Error: A required input file was not found. Please check your file paths: {e}")
        return
    except Exception as e:
        print(f"Error reading input files: {e}")
        return

    # Step 3: Call /get-resume-json
    print("\n3. Calling /get-resume-json...")
    try:
        payload = {
            "resume_content": resume_content,
            "additional_details": "I am a skilled Python and TypeScript developer."
        }
        response = requests.post(f"{base_url}/get-resume-json", json=payload, headers=headers)
        response.raise_for_status()
        resume_data = response.json()
        resume_json_data = json.dumps(resume_data.get("resume_data"))
        search_query = resume_data.get("search_query")
        full_name = resume_data.get("resume_data", {}).get("personal", {}).get("full_name", "John Doe")
        print("Successfully extracted resume data and search query.")
        print(f"  Search Query: {search_query}")
        print(f"  Full Name: {full_name}")
    except Exception as e:
        print(f"Failed to get resume JSON: {e}")
        return

    # Step 4: Call /generate-search-query
    print("\n4. Calling /generate-search-query...")
    try:
        payload = {"resume_json_data": resume_json_data}
        response = requests.post(f"{base_url}/generate-search-query", json=payload, headers=headers)
        response.raise_for_status()
        search_query_again = response.json().get("search_query")
        print("Successfully generated search query.")
        print(f"  Search Query: {search_query_again}")
    except Exception as e:
        print(f"Failed to generate search query: {e}")

    # Step 5: Call /analyze-job-posting
    print("\n5. Calling /analyze-job-posting...")
    try:
        payload = {
            "job_posting_text": job_posting_text,
            "resume_json_data": resume_json_data
        }
        response = requests.post(f"{base_url}/analyze-job-posting", json=payload, headers=headers)
        response.raise_for_status()
        analysis_data = response.json()
        company_name = analysis_data.get("company_name", "Unknown Company")
        print("Successfully analyzed job posting.")
        print(f"  Job Title: {analysis_data.get('job_id')}")
        print(f"  Company: {company_name}")
        print(f"  Analysis:\n{analysis_data.get('job_analysis')}")
    except Exception as e:
        print(f"Failed to analyze job posting: {e}")
        return

    # Step 6: Call /generate-cover-letter
    print("\n6. Calling /generate-cover-letter...")
    try:
        payload = {
            "job_posting_text": job_posting_text,
            "resume_json_data": resume_json_data
        }
        response = requests.post(f"{base_url}/generate-cover-letter", json=payload, headers=headers)
        response.raise_for_status()
        cover_letter_content = response.json().get("content")
        print("Successfully generated cover letter.")
        print(f"  Cover Letter:\n{cover_letter_content}")
    except Exception as e:
        print(f"Failed to generate cover letter: {e}")

    # Step 7: Call /tailor-resume and save the PDF
    print("\n7. Calling /tailor-resume and saving PDF...")
    try:
        filename = f"{full_name.replace(' ', '_')}_resume_{company_name.replace(' ', '_')}.pdf"
        payload = {
            "job_posting_text": job_posting_text,
            "resume_json_data": resume_json_data,
            "example_yaml_resume": example_yaml_resume,
            "theme": "engineeringclassic",
            "design_yaml_string": design_yaml_string,
            "locale_yaml_string": locale_yaml_string,
            "filename": filename
        }
        response = requests.post(f"{base_url}/tailor-resume", json=payload, headers=headers)
        response.raise_for_status()

        with open(filename, "wb") as f:
            f.write(response.content)

        print(f"Successfully generated and saved PDF to {filename}")
    except Exception as e:
        print(f"Failed to tailor resume or save PDF: {e}")
