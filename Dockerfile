# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install the latest version of pip
RUN pip install --upgrade pip

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Expose port 3000 (or whichever port you want Flask to listen on)
EXPOSE 3000

# Set environment variables for Flask
ENV FLASK_APP=api_server.py
ENV FLASK_ENV=production

# Run the Flask app when the container starts (in production mode)
CMD ["python", "api_server.py"]
