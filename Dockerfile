FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

RUN apt-get update && \
    apt-get install -y \
    texlive-latex-base \
    texlive-fonts-recommended \
    pandoc \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Copy the application files into the container
COPY requirements.txt .
COPY example_resume.yaml .
COPY job_analysis_format.html .
COPY app.py .

# Install any needed packages specified in requirements.txt
# This assumes you have a requirements.txt file in your project
# When debugging, it's usually faster if --no-cache-dir is removed
RUN pip install --no-cache-dir -r requirements.txt

# Prevent Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

# Expose the port that your application will run on
EXPOSE 8080

# Run your application
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--log-level", "info", "--access-logfile", "-", "--error-logfile", "-", "app:app"]