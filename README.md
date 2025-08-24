# **AI Job Search Helper Server Backend**

This project provides a backend server for the [AI Job Search Helper browser extension](https://github.com/v1rtu0z/ai-job-search-helper). It's built using **[Flask](https://github.com/pallets/flask)** and designed to run in a **Docker** container, leveraging the **Gemini** API to perform job posting analysis and content generation.

## **Features**

* **RESTful API:** Provides endpoints for the browser extension to request content generation and analysis.  
* **Dockerized:** Easy to set up and run in any environment with Docker installed.  
* **Gemini Integration:** Connects to the Gemini API to perform AI-powered tasks.

## **Getting Started**

### **Prerequisites**

* **Docker:** Ensure you have Docker and Docker Compose installed on your system.  
* **Gemini API Key:** You'll need a valid API key from the Google AI Studio.

### **Configuration**

1. **Environment Variables:** Create a .env file in the root of the project. This file will store your sensitive API key.  
   GOOGLE\_API\_KEY=your\_gemini\_api\_key\_here

   Make sure to replace your\_gemini\_api\_key\_here with your actual API key.  

### **Running with Docker**

To start the server, simply use Docker Compose.

docker-compose up \--build

This command will:

* Build the Docker image using the Dockerfile.  
* Create and start the container.  
* The API will be available at http://localhost:5000 (or the port you specify in your Docker Compose file).

## **API Endpoints**

* **POST /authenticate**: Authenticates the user.  
* **POST /get-resume-json**: Parses the resume and returns a JSON object containing the resume's information.
* **POST /generate-search-query**: Generates a personalized LinkedIn search query.
* **POST /analyze-job-posting**: Analyzes a job posting against a resume.  
* **POST /generate-cover-letter**: Generates a cover letter.
* **POST /tailor-resume**: Generates a tailored resume using [renderCv](https://github.com/rendercv/rendercv).

## **Contributing**

We welcome contributions\! If you would like to contribute, please fork the repository and submit a pull request.

## **License**

This project is licensed under a modified MIT License with a non-commercial clause. See the full [LICENSE](LICENSE.md) file for details.