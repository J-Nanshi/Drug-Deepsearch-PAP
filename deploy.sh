#!/bin/bash

# Update system
sudo yum update -y

# Install required packages
sudo yum install -y python3 python3-pip git gcc python3-devel wkhtmltopdf xorg-x11-server-Xvfb

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install gunicorn if not already installed
pip install gunicorn

# Create systemd service file
sudo tee /etc/systemd/system/flask_app.service << EOF
[Unit]
Description=Gunicorn instance to serve flask application
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/deep_review
Environment="PATH=/home/ec2-user/deep_review/venv/bin"
ExecStart=/home/ec2-user/deep_review/venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 4 --threads 2 --timeout 120 app:app

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Start and enable the service
sudo systemctl start flask_app
sudo systemctl enable flask_app 