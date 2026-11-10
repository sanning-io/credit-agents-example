# The example as one image for any host that runs containers. Each agent runs
# as its own service from this one image, with its own start command:
#
#   underwriter   credit-demo underwriter   (the default command)
#   checker       credit-demo checker
#
# Each command tests that agent's settings first, then starts it. Running the
# example on a laptop without a container needs nothing in this file.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
# The case documents: each application's folder (the application file and
# the applicant's documents) and the credit policy. The package above is
# installed into site-packages, which holds no documents, so they are copied
# here and CREDIT_DEMO_HOME says where they are
# (src/credit_demo/common/home.py). The run directories default to runs/
# under the same home.
COPY applications ./applications
COPY policy ./policy
ENV CREDIT_DEMO_HOME=/app

# Every interface, over IPv4 and IPv6, inside the container: the underwriter's
# port has to be reached from outside, and the underwriter has to reach the
# checker's, on whichever of the two the host's private network carries (some
# carry IPv6 only). `::` takes both from one socket
# (src/credit_demo/common/listen.py). Neither service asks for a login, so the
# host must keep the checker off the public internet: give it no public
# address, and let only the underwriter reach it over the host's private
# network.
ENV CREDIT_DEMO_HOST=::

# Each service listens on the PORT the host sets. Without one, the
# underwriter listens on 8001 and the checker on 8002.

# Every line a service prints reaches the container log when it is printed.
# Standard output is block-buffered when it is not a terminal, and a line
# still in the buffer is lost if the container is killed.
ENV PYTHONUNBUFFERED=1
# The underwriter's port. The checker's is left unlisted on purpose: it is not
# for the public.
EXPOSE 8001

CMD ["credit-demo", "underwriter"]
