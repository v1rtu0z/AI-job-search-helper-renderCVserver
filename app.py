import os
import subprocess
import tempfile

from flask import Flask, request, send_file, jsonify

app = Flask(__name__)

API_KEY = '' # TODO: Fetch this from a secret manager

# TODO: Move these two variables into the extension

DEFAULT_DESIGN_YAML = """
design:
  theme: engineeringclassic
  page:
    size: us-letter
    top_margin: 2cm
    bottom_margin: 2cm
    left_margin: 2cm
    right_margin: 2cm
    show_page_numbering: false
    show_last_updated_date: false
  colors:
    text: rgb(0, 0, 0)
    name: rgb(0, 79, 144)
    connections: rgb(0, 79, 144)
    section_titles: rgb(0, 79, 144)
    links: rgb(0, 79, 144)
    last_updated_date_and_page_numbering: rgb(128, 128, 128)
  text:
    font_family: Raleway
    font_size: 8.8pt
    leading: 0.6em
    alignment: justified
    date_and_location_column_alignment: right
  links:
    underline: false
    use_external_link_icon: false
  header:
    name_font_family: Raleway
    name_font_size: 30pt
    name_bold: false
    photo_width: 3.5cm
    vertical_space_between_name_and_connections: 0.7cm
    vertical_space_between_connections_and_first_section: 0.7cm
    horizontal_space_between_connections: 0.5cm
    connections_font_family: Raleway
    separator_between_connections: ''
    use_icons_for_connections: true
    alignment: left
  section_titles:
    font_family: Raleway
    font_size: 1.4em
    bold: false
    small_caps: false
    line_thickness: 0.5pt
    vertical_space_above: 0.5cm
    vertical_space_below: 0.3cm
  entries:
    date_and_location_width: 4.15cm
    left_and_right_margin: 0.2cm
    horizontal_space_between_columns: 0.1cm
    vertical_space_between_entries: 1.2em
    allow_page_break_in_sections: true
    allow_page_break_in_entries: true
    short_second_row: false
    show_time_spans_in: []
  highlights:
    bullet: •
    top_margin: 0.25cm
    left_margin: 0cm
    vertical_space_between_highlights: 0.25cm
    horizontal_space_between_bullet_and_highlight: 0.5em
    summary_left_margin: 0cm
  entry_types:
    one_line_entry:
      template: '**LABEL:** DETAILS'
    education_entry:
      main_column_first_row_template: '**INSTITUTION**, AREA -- LOCATION'
      degree_column_template: '**DEGREE**'
      degree_column_width: 1cm
      main_column_second_row_template: |-
        SUMMARY
        HIGHLIGHTS
      date_and_location_column_template: DATE
    normal_entry:
      main_column_first_row_template: '**NAME** -- **LOCATION**'
      main_column_second_row_template: |-
        SUMMARY
        HIGHLIGHTS
      date_and_location_column_template: DATE
    experience_entry:
      main_column_first_row_template: '**POSITION**, COMPANY -- LOCATION'
      main_column_second_row_template: |-
        SUMMARY
        HIGHLIGHTS
      date_and_location_column_template: DATE
    publication_entry:
      main_column_first_row_template: '**TITLE**'
      main_column_second_row_template: |-
        AUTHORS
        URL (JOURNAL)
      main_column_second_row_without_journal_template: |-
        AUTHORS
        URL
      main_column_second_row_without_url_template: |-
        AUTHORS
        JOURNAL
      date_and_location_column_template: DATE
"""

DEFAULT_LOCALE_YAML = """
locale:
  language: en
  phone_number_format: international
  page_numbering_template: NAME - Page PAGE_NUMBER of TOTAL_PAGES
  last_updated_date_template: Last updated in TODAY
  date_template: MONTH_ABBREVIATION YEAR
  month: month
  months: months
  year: year
  years: years
  present: present
  to: –
  abbreviations_for_months:
    - Jan
    - Feb
    - Mar
    - Apr
    - May
    - June
    - July
    - Aug
    - Sept
    - Oct
    - Nov
    - Dec
  full_names_of_months:
    - January
    - February
    - March
    - April
    - May
    - June
    - July
    - August
    - September
    - October
    - November
    - December
rendercv_settings:
  date: '2025-03-01'
  bold_keywords: []
"""


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
            final_pdf_path = os.path.join(temp_dir, filename)

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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 8080))
