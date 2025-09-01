import datetime

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
              "summary": [...],
              "education": [...],
              "experience": [...],
              "projects": [...],
              "skills": [...],
              "certifications": [...],
              "references": [...],
              ...
          }}
        }}
        
        In case that start/end dates are missing, you should omit them from the JSON output.
        Also, if the website is not provided, omit it from the JSON output.
    """,
    "SEARCH_QUERY_ONLY": lambda resume_json_data: f"""
        Based on the user's structured resume data (JSON) provided, generate a personalized LinkedIn search query.
        The query should use Boolean search operators and be in the format: ("job title 1" OR "job title 2") AND NOT ("skill 1" OR "skill 2" OR "job title 3").

        **Resume data JSON:**
        {resume_json_data}

        Return only the search query string and nothing else.
    """,
    "JOB_ANALYSIS": lambda job_posting_text, resume_json_data, job_analysis_format, previous_analysis=None,
                           job_specific_context=None: f"""
        The year is {datetime.date.today().year}. You are a professional career assistant. Your task is to provide a comprehensive job analysis in a structured HTML document, strictly adhering to the format outlined in the provided 'Job Analysis Format' file.

        **Input Data:**
        Job Description:
        {job_posting_text}
        **Resume data JSON:**
        {resume_json_data}
        **Job Analysis Format:**
        {job_analysis_format}

        {f"Previous analysis to improve upon: {previous_analysis}" if previous_analysis else ""}
        {f"Job-specific context to consider: {job_specific_context}" if job_specific_context else ""}

        **Instructions:**
        - Compare the user's resume JSON data (including the 'additionalDetails' field) against the job description.
        - The analysis should take into consideration any missing or misaligned elements from the job description like location, remote work policy, industry, position, seniority, salary range, etc.
        - The output must start with [job title] @ [company name] so that it can be easily identified. If these can't be found, return an error message.
        - Replace strength name placeholders with actual sensible data. Same for areas for improvement
        - Make sure that the titles are larger that list items and that you're not repeating yourself
        - Change only the text values in the HTML format, leave everything else as it is.
        - Make sure to properly color the fit score - the very poor fit should be very red and the very good fit should be very green along with everything in between properly color as well.
        {f"- Address the previous analysis and context provided above to improve the output." if previous_analysis or job_specific_context else ""}
    """,
    "COVER_LETTER": lambda job_posting_text, resume_json_data, job_specific_context=None, current_content=None,
                           retry_feedback=None: f"""
        The year is {datetime.date.today().year}. You are a professional career assistant. Your task is to generate a cover letter that will
        help the user apply for the job based on the job description, and the users resume data (JSON) provided.
        The resume JSON data includes 'additionalDetails' field you should pay attention to.

        **Job Description:**
        {job_posting_text}
        **Resume data JSON:**
        {resume_json_data}

        {f"Job-specific context: {job_specific_context}" if job_specific_context else ""}
        {f"Current cover letter content to improve: {current_content}" if current_content else ""}
        {f"Feedback to address: {retry_feedback}" if retry_feedback else ""}

        Some general guidelines: make it at most 3-4 paragraphs long, address their strengths and in
        case that there are any missing skills, address those head on based on the users other skills
        (ie stuff like quick learning, hard-working, commitment to excellence etc). Make sure to
        reference the details from the job post as much as possible.
        Note that the job description might not be in English and shouldn't be dismissed in that case!
        Always write the cover letter in the same language as the job description.
        {f"Address the feedback provided above to improve the cover letter." if retry_feedback else ""}
    """,
    "JSON_CONVERSION": lambda job_posting_text, resume_json_data, current_resume_data=None, retry_feedback=None: f"""
    The year is {datetime.date.today().year}. You are a professional career assistant. Your task is to convert the JSON resume data into a tailored JSON resume, based on the job description.

    Input Data:
    Job Description:
    {job_posting_text}
    Resume data JSON:
    {resume_json_data}

    {f"Current resume data to improve: {current_resume_data}" if current_resume_data else ""}
    {f"Feedback to address: {retry_feedback}" if retry_feedback else ""}

    Instructions:
    - Use the Job Description to highlight and reorder relevant skills and experiences from the JSON data.
    - *DO NOT* add any skills or experience to the output JSON that are not a part of the resume data JSON!
    - Output ONLY valid JSON in the exact structure shown below.
    - Do NOT add any placeholders or example data.
    - The JSON you generate shouldn't contain strings in the format of <X or >X. These should always be separated by a space, like < X or > X.
    - Start/end dates need to be in the format YYYY-MM. If they're not present in resume data, it's okay to omit them
    - Do not include additional details. Only use the input data to populate the output JSON.
    - You *have to* omit unnecessary or empty sections but maintain the structure for sections you include.
    - Pay attention not to confuse the user's location and the job's location.
    - If the user provides their linkedin, there's no need for the full linkedin url, use only the linkedin username, it will get parsed into a proper url later
    {f"- Address the feedback provided above to improve the resume data." if retry_feedback else ""}

    Required JSON Structure:
    {{
        "cv": {{
            "name": "Full Name",
            "location": "City, State/Country",
            "email": "email@example.com",
            "phone": "phone number",
            "website": "website url",
            "social_networks": [
                {{
                    "network": "LinkedIn", (here the keys must be one of: "LinkedIn", "GitHub", "GitLab", "Instagram", "ORCID", "Mastodon", "StackOverflow", "ResearchGate", "YouTube", "Google Scholar", "Telegram", "X" (It can't be twitter or anything else!))
                    "username": "username"
                }},
                {{
                    "network": "GitHub", 
                    "username": "username"
                }}
            ],
            "sections": {{
                "summary": [
                    "Summary text",
                ],
                "education": [
                    {{
                        "institution": "University Name",
                        "area": "Field of Study", (this field is mandatory!)
                        "degree": "Degree Type (One of: BA, BS, MA, MBA, Phd. Omit if not applicable)",
                        "start_date": "YYYY-MM",
                        "end_date": "YYYY-MM or present",
                        "location": "City, State/Country",
                        "highlights": [
                            "Achievement or detail 1",
                            "Achievement or detail 2"
                        ]
                    }}
                ],
                "experience": [
                    {{
                        "company": "Company Name",
                        "position": "Job Title",
                        "start_date": "YYYY-MM",
                        "end_date": "YYYY-MM or present", 
                        "location": "City, State/Country",
                        "highlights": [
                            "Accomplishment 1",
                            "Accomplishment 2"
                        ]
                    }}
                ],
                "projects": [
                    {{
                        "name": "Project Name",
                        "start_date": "YYYY-MM",
                        "end_date": "YYYY-MM or present",
                        "summary": "Brief project description",
                        "highlights": [
                            "Key feature or achievement 1",
                            "Key feature or achievement 2"
                        ]
                    }}
                ],
                "skills": [
                    {{
                        "label": "Skill Category",
                        "details": "Specific skills and proficiency levels"
                    }}
                ],
                "publications": [
                    {{
                        "title": "Publication Title",
                        "authors": ["Author 1", "Author 2"],
                        "doi": "DOI number",
                        "url": "publication url",
                        "journal": "Journal name",
                        "date": "YYYY-MM"
                    }}
                ],
                "certifications": [
                    {{
                        "name": "Certification Name",
                        "institution": "Issuing Organization",
                        "date": "YYYY-MM",
                        "expiry_date": "YYYY-MM or empty string if no expiry",
                        "url": "credential url or empty string"
                    }}
                ],
                "references": [
                    {{
                        "name": "Reference Name",
                        "highlights: [
                            "Reference details 1",
                            "Reference details 2"
                        ]
                    }}
                ]
            }}
        }}
    }}
""",
}
