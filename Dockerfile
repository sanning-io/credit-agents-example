# The example as one image for any host that runs containers. Each service runs
# on its own from this one image, with its own start command:
#
#   underwriter       credit-demo underwriter   (the default command)
#   checker           credit-demo checker
#   evidence-builder  credit-demo evidence-builder
#
# Each command tests that service's settings first, then starts it. Running the
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
# checker's and the evidence builder's, on whichever of the two the host's
# private network carries (some carry IPv6 only). `::` takes both from one
# socket (src/credit_demo/common/listen.py). No service asks for a login, so
# the host must keep the checker and the evidence builder off the public
# internet: give them no public address, and let only the underwriter reach
# them over the host's private network.
ENV CREDIT_DEMO_HOST=::

# Each service listens on the PORT the host sets. Without one, the
# underwriter listens on 8001, the checker on 8002, and the evidence builder
# on port 8003.

# Every line a service prints reaches the container log when it is printed.
# Standard output is block-buffered when it is not a terminal, and a line
# still in the buffer is lost if the container is killed.
ENV PYTHONUNBUFFERED=1
# The underwriter's port. The checker's and the evidence builder's are left
# unlisted on purpose: they are not for the public.
EXPOSE 8001

CMD ["credit-demo", "underwriter"]
