# Harvest

# Use the base docker container
from cbmi/harvest_base:version3

MAINTAINER Tyler Rivera "riverat2@email.chop.edu"

# Add the IBD-Harvest files
ADD . /opt/apps/harvest-app

# Ensure all python requirements are met
ENV APP_NAME harvest_project
RUN /opt/ve/harvest-app/bin/pip install -r /opt/apps/harvest-app/requirements.txt

EXPOSE 8000
