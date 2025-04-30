FROM python:3
WORKDIR /app
COPY app.py .
RUN pip install flask
RUN pip install requests
RUN pip install python-dotenv
RUN pip install humanize
RUN pip install datetime
CMD ["python", "app.py"]